from datetime import datetime, timedelta, timezone
from decimal import Decimal

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request
from sqlmodel import Session, select, update
from db import get_session
from models import (
    ChargingSessionResponse,
    ChargingSummaryResponse,
    Devices,
    ServiceSessionCreate,
    ForfeitCreate,
    Sessions,
    SystemSettings,
    Transactions,
    WifiClientResponse,
    WifiSummaryResponse,
    WifiUsagePoint,
)
from config import MAX_CHARGING_USERS, MAX_PAUSE_SECONDS, MAX_WIFI_USERS
from device_auth import require_device_key
from routers.auth import get_current_admin, get_optional_admin
from rate_limits import enforce_rate_limit

router = APIRouter(
    prefix="/sessions",
    tags=["sessions"],
    responses={404: {"description": "Not found"}}
    )

def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

def expire_session_if_needed(session_data: Sessions, session: Session) -> None:
    if not session_data.session_active:
        return
    now = datetime.now(timezone.utc)
    if (session_data.paused_at is None and session_data.expires_at is not None and
            now >= as_utc(session_data.expires_at)):
        session_data.session_active = False
        session_data.session_end = session_data.expires_at
        session.add(session_data)
        session.commit()
        session.refresh(session_data)
        return
    # A paused session holds a Wi-Fi/charging slot open with no running clock. Cap how long
    # it can stay paused so a forgotten or abused pause cannot block other customers forever.
    if (session_data.paused_at is not None and
            (now - as_utc(session_data.paused_at)).total_seconds() >= MAX_PAUSE_SECONDS):
        session_data.paused_seconds += max(
            0, int((now - as_utc(session_data.paused_at)).total_seconds())
        )
        session_data.paused_at = None
        session_data.session_active = False
        session_data.session_end = now
        session.add(session_data)
        session.commit()
        session.refresh(session_data)

@router.get("/charging-summary", response_model=ChargingSummaryResponse)
def get_charging_summary(session: Session = Depends(get_session),
                         current_admin=Depends(get_current_admin)):
    charging_sessions = session.exec(
        select(Sessions)
        .where(Sessions.session_type == "charging")
        .order_by(Sessions.session_start.desc())
    ).all()
    for item in charging_sessions:
        expire_session_if_needed(item, session)
    used_sessions = [item for item in charging_sessions if item.session_active]
    total_ports = MAX_CHARGING_USERS
    used_ports = min(len(used_sessions), total_ports)
    durations = [
        item.duration_seconds for item in charging_sessions
        if item.duration_seconds is not None
    ]
    return ChargingSummaryResponse(
        used_ports=used_ports,
        inactive_ports=total_ports - used_ports,
        total_sessions=len(charging_sessions),
        current_load_watts=sum(
            item.port_output_watts or 0 for item in used_sessions
        ),
        average_session_seconds=(sum(durations) // len(durations) if durations else 0),
        sessions=[
            ChargingSessionResponse(
                id=item.id,
                device_id=item.device_id,
                session_active=item.session_active,
                duration_seconds=item.duration_seconds,
                session_start=item.session_start,
            )
            for item in charging_sessions[:100]
        ],
    )

@router.get("/wifi-summary", response_model=WifiSummaryResponse)
def get_wifi_summary(session: Session = Depends(get_session),
                     current_admin=Depends(get_current_admin)):
    wifi_sessions = session.exec(
        select(Sessions)
        .where(Sessions.session_type == "wifi")
        .order_by(Sessions.session_start.desc())
    ).all()
    for item in wifi_sessions:
        expire_session_if_needed(item, session)
    return WifiSummaryResponse(
        active_clients=sum(1 for item in wifi_sessions if item.session_active),
        total_sessions=len(wifi_sessions),
        total_download_kb=sum(item.data_download_kb or 0 for item in wifi_sessions),
        clients=[
            WifiClientResponse(
                id=item.id,
                device_id=item.device_id,
                session_target=item.session_target,
                session_active=item.session_active,
                data_download_kb=item.data_download_kb or 0,
                duration_seconds=item.duration_seconds,
            )
            for item in wifi_sessions[:100]
        ],
        usage=[
            WifiUsagePoint(session_start=item.session_start,
                           data_download_kb=item.data_download_kb or 0)
            for item in wifi_sessions
            if item.session_start and (item.data_download_kb or 0) > 0
        ],
    )

@router.get("/{id}", response_model=Sessions)
def get_session_by_id(id: int, session: Session = Depends(get_session),
                current_admin=Depends(get_current_admin)):
    session_data = session.get(Sessions, id)
    if not session_data:
        raise HTTPException(status_code=404, detail="Session not found")
    expire_session_if_needed(session_data, session)
    return session_data

@router.get("/device/{device_id}/active", response_model=Sessions | None,
            dependencies=[Depends(require_device_key)])
def get_active_device_session(device_id: Annotated[str, Path(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")],
                              request: Request,
                              session: Session = Depends(get_session)):
    enforce_rate_limit(request, "active_device_session", device_id, limit=30)
    enforce_rate_limit(request, "active_device_session_ip", limit=60)
    session_data = session.exec(
        select(Sessions)
        .where(Sessions.device_id == device_id)
        .where(Sessions.session_active)
        .where(Sessions.session_end.is_(None))
        .order_by(Sessions.session_start.desc())
    ).first()
    if session_data:
        expire_session_if_needed(session_data, session)
        if not session_data.session_active:
            return None
    return session_data

@router.post("/forfeit", status_code=201, dependencies=[Depends(require_device_key)])
def forfeit_credits(request_data: ForfeitCreate, request: Request,
                    session: Session = Depends(get_session)):
    enforce_rate_limit(request, "forfeit_credits", request_data.device_id, limit=10)
    enforce_rate_limit(request, "forfeit_credits_ip", limit=30)
    device = session.exec(
        select(Devices).where(Devices.device_id == request_data.device_id)
    ).first()
    if device is None:
        raise HTTPException(status_code=404, detail="Device is not registered")
    # Coins were already collected, so unspent credits are recorded as revenue.
    record = Transactions(
        device_id=request_data.device_id,
        coin_denomination=1,
        coins_count=request_data.credits,
        total_amount=Decimal(request_data.credits),
        transaction_type="forfeited",
    )
    session.add(record)
    session.commit()
    return {"forfeited": request_data.credits}

@router.post("/start", response_model=Sessions, status_code=201,
             dependencies=[Depends(require_device_key)])
def start_device_session(
        request_data: ServiceSessionCreate,
        request: Request,
        session: Session = Depends(get_session)):
    enforce_rate_limit(request, "start_device_session", request_data.device_id, limit=10)
    enforce_rate_limit(request, "start_device_session_ip", limit=30)

    device = session.exec(
        select(Devices).where(Devices.device_id == request_data.device_id)
    ).first()
    if device is None:
        raise HTTPException(status_code=404, detail="Device is not registered")

    active_sessions = session.exec(
        select(Sessions)
        .where(Sessions.device_id == request_data.device_id)
        .where(Sessions.session_active)
        .where(Sessions.session_end.is_(None))
        .order_by(Sessions.session_start.desc())
    ).all()
    for active_session in active_sessions:
        expire_session_if_needed(active_session, session)
    if any(active_session.session_active for active_session in active_sessions):
        raise HTTPException(
            status_code=409,
            detail="This device already has an active session",
        )

    capacity = (MAX_WIFI_USERS if request_data.session_type == "wifi"
                else MAX_CHARGING_USERS)
    type_sessions = session.exec(
        select(Sessions)
        .where(Sessions.session_type == request_data.session_type)
        .where(Sessions.session_active)
        .where(Sessions.session_end.is_(None))
    ).all()
    for type_session in type_sessions:
        expire_session_if_needed(type_session, session)
    if sum(1 for item in type_sessions if item.session_active) >= capacity:
        raise HTTPException(
            status_code=409,
            detail=f"All {request_data.session_type} slots are in use",
        )

    settings = session.get(SystemSettings, 1)
    if settings is None:
        raise HTTPException(status_code=503, detail="Service rates are unavailable")
    rate_per_credit = (
        settings.wifi_rate_per_coin
        if request_data.session_type == "wifi"
        else settings.charging_rate_per_coin
    )
    duration_seconds = rate_per_credit * request_data.credits
    if duration_seconds <= 0:
        raise HTTPException(
            status_code=409,
            detail="This service is currently unavailable",
        )
    if duration_seconds > 86400:
        raise HTTPException(
            status_code=422,
            detail="Session duration cannot exceed 24 hours",
        )

    now = datetime.now(timezone.utc)
    session_data = Sessions(
        device_id=request_data.device_id,
        session_type=request_data.session_type,
        session_target="router" if request_data.session_type == "wifi" else "charger",
        session_active=True,
        duration_seconds=duration_seconds,
        pending_credits=request_data.credits,
        session_start=now,
        expires_at=now + timedelta(seconds=duration_seconds),
    )
    session.add(session_data)
    session.commit()
    session.refresh(session_data)
    return session_data

def record_session_sale(session_data: Sessions, session: Session) -> bool:
    """Books the session's pending credits as a sale once; the caller commits."""
    credits = session_data.pending_credits
    if not credits or session_data.id is None:
        return False
    # Claim the credits with a conditional UPDATE so concurrent confirm/telemetry/usage
    # requests holding stale copies cannot both book the sale.
    claimed = session.execute(
        update(Sessions)
        .where(Sessions.id == session_data.id, Sessions.pending_credits == credits)
        .values(pending_credits=None)
        .execution_options(synchronize_session=False)
    ).rowcount
    if claimed != 1:
        session_data.pending_credits = None
        return False
    # One credit is one peso coin pulse.
    session.add(Transactions(
        device_id=session_data.device_id,
        coin_denomination=1,
        coins_count=credits,
        total_amount=Decimal(credits),
        transaction_type=session_data.session_type,
    ))
    session_data.pending_credits = None
    return True

@router.post("/{id}/confirm", dependencies=[Depends(require_device_key)])
def confirm_session_started(id: int, request: Request,
                            device_id: str = Query(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$"),
                            session: Session = Depends(get_session)):
    enforce_rate_limit(request, "confirm_session", str(id), limit=20)
    session_data = session.get(Sessions, id)
    if not session_data or session_data.device_id != device_id:
        raise HTTPException(status_code=404, detail="Session not found")
    # Confirmed even if it has since ended: the kiosk did start it, so the coins were spent.
    recorded = record_session_sale(session_data, session)
    session.commit()
    return {"confirmed": True, "sale_recorded": recorded}

def load_session(id: int, session: Session, device_id: str | None = None,
                 current_admin=None) -> Sessions:
    session_data = session.get(Sessions, id)
    if not session_data:
        raise HTTPException(status_code=404, detail="Session not found")
    if current_admin is None and session_data.device_id != device_id:
        raise HTTPException(status_code=404, detail="Session not found")
    expire_session_if_needed(session_data, session)
    if not session_data.session_active or session_data.session_end is not None:
        raise HTTPException(status_code=409, detail="Session has ended")
    return session_data

@router.patch("/{id}/pause", response_model=Sessions)
def pause_session(id: int, request: Request,
                  session: Session = Depends(get_session),
                  device_id: str | None = Query(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$"),
                  current_admin=Depends(get_optional_admin),
                  x_device_key: str | None = Header(default=None)):
    if current_admin is None:
        require_device_key(request, x_device_key)
        enforce_rate_limit(request, "public_session_ip", limit=60)
        enforce_rate_limit(request, "public_session", device_id or "", limit=20)
    session_data = load_session(id, session, device_id, current_admin)
    if session_data.paused_at is not None:
        raise HTTPException(status_code=409, detail="Session is already paused")

    session_data.paused_at = datetime.now(timezone.utc)
    session.add(session_data)
    session.commit()
    session.refresh(session_data)
    return session_data

@router.patch("/{id}/resume", response_model=Sessions)
def resume_session(id: int, request: Request,
                   session: Session = Depends(get_session),
                   device_id: str | None = Query(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$"),
                   current_admin=Depends(get_optional_admin),
                   x_device_key: str | None = Header(default=None)):
    if current_admin is None:
        require_device_key(request, x_device_key)
        enforce_rate_limit(request, "public_session_ip", limit=60)
        enforce_rate_limit(request, "public_session", device_id or "", limit=20)
    session_data = load_session(id, session, device_id, current_admin)
    if session_data.paused_at is None:
        raise HTTPException(status_code=409, detail="Session is not paused")
    if session_data.expires_at is None:
        raise HTTPException(status_code=409, detail="Session has no expiration time")

    now = datetime.now(timezone.utc)
    paused_duration = max(0, int(
        (now - as_utc(session_data.paused_at)).total_seconds()
    ))
    session_data.paused_seconds += paused_duration
    session_data.expires_at += now - as_utc(session_data.paused_at)
    session_data.paused_at = None
    session.add(session_data)
    session.commit()
    session.refresh(session_data)
    return session_data

@router.patch("/end-all")
def end_all_sessions(session: Session = Depends(get_session),
                     current_admin=Depends(get_current_admin)):
    return end_active_sessions(session, None)

@router.patch("/end-all/{session_type}")
def end_all_sessions_of_type(session_type: str,
                             session: Session = Depends(get_session),
                             current_admin=Depends(get_current_admin)):
    if session_type not in ("wifi", "charging"):
        raise HTTPException(status_code=404, detail="Unknown session type")
    return end_active_sessions(session, session_type)

def end_active_sessions(session: Session, session_type: str | None):
    now = datetime.now(timezone.utc)
    query = (
        select(Sessions)
        .where(Sessions.session_active)
        .where(Sessions.session_end.is_(None))
    )
    if session_type:
        query = query.where(Sessions.session_type == session_type)
    active = session.exec(query).all()
    for item in active:
        if item.paused_at is not None:
            item.paused_seconds += max(
                0, int((now - as_utc(item.paused_at)).total_seconds())
            )
            item.paused_at = None
        item.session_active = False
        item.session_end = now
        session.add(item)
    session.commit()
    return {"ended": len(active)}

@router.patch("/{id}/usage")
def report_session_usage(id: int, request: Request,
                         data_download_kb: float = Query(ge=0, le=100_000_000),
                         session: Session = Depends(get_session),
                         device_id: str | None = Query(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$"),
                         current_admin=Depends(get_optional_admin),
                         x_device_key: str | None = Header(default=None)):
    if current_admin is None:
        require_device_key(request, x_device_key)
        enforce_rate_limit(request, "session_usage", str(id), limit=120)
    session_data = session.get(Sessions, id)
    if not session_data or (current_admin is None and session_data.device_id != device_id):
        raise HTTPException(status_code=404, detail="Session not found")
    # Usage only grows, so a late or repeated report cannot lower the total.
    session_data.data_download_kb = max(session_data.data_download_kb or 0.0, data_download_kb)
    session.add(session_data)
    if current_admin is None:
        # Only a session the kiosk actually ran gets usage reports.
        record_session_sale(session_data, session)
    session.commit()
    return {"data_download_kb": session_data.data_download_kb}

@router.patch("/{id}/end", response_model=Sessions)
def end_session(id: int, request: Request,
                session: Session = Depends(get_session),
                device_id: str | None = Query(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$"),
                current_admin=Depends(get_optional_admin),
                x_device_key: str | None = Header(default=None)):
    if current_admin is None:
        require_device_key(request, x_device_key)
        enforce_rate_limit(request, "public_session_ip", limit=60)
        enforce_rate_limit(request, "public_session", device_id or "", limit=20)
    session_data = load_session(id, session, device_id, current_admin)
    now = datetime.now(timezone.utc)
    if session_data.paused_at is not None:
        session_data.paused_seconds += max(
            0, int((now - as_utc(session_data.paused_at)).total_seconds())
        )
        session_data.paused_at = None
    session_data.session_active = False
    session_data.session_end = now
    session.add(session_data)
    session.commit()
    session.refresh(session_data)
    return session_data
