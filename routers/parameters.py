from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlmodel import Session, select
from db import get_session
from models import (
    ChargingSummaryResponse,
    Devices,
    ServiceSessionCreate,
    Sessions,
    SystemSettings,
    WifiClientResponse,
    WifiSummaryResponse,
)
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
    if (session_data.session_active and session_data.paused_at is None and
            session_data.expires_at is not None and
            datetime.now(timezone.utc) >= as_utc(session_data.expires_at)):
        session_data.session_active = False
        session_data.session_end = session_data.expires_at
        session.add(session_data)
        session.commit()
        session.refresh(session_data)

@router.get("/charging-summary", response_model=ChargingSummaryResponse)
def get_charging_summary(session: Session = Depends(get_session),
                         current_admin=Depends(get_current_admin)):
    charging_sessions = session.exec(
        select(Sessions).where(Sessions.session_type == "charging")
    ).all()
    used_sessions = [item for item in charging_sessions if item.session_active]
    durations = [
        item.duration_seconds for item in charging_sessions
        if item.duration_seconds is not None
    ]
    return ChargingSummaryResponse(
        used_ports=len(used_sessions),
        inactive_ports=len(charging_sessions) - len(used_sessions),
        current_load_watts=sum(
            item.port_output_watts or 0 for item in used_sessions
        ),
        average_session_seconds=(sum(durations) // len(durations) if durations else 0),
    )

@router.get("/wifi-summary", response_model=WifiSummaryResponse)
def get_wifi_summary(session: Session = Depends(get_session),
                     current_admin=Depends(get_current_admin)):
    wifi_sessions = session.exec(
        select(Sessions)
        .where(Sessions.session_type == "wifi")
        .order_by(Sessions.session_start.desc())
    ).all()
    return WifiSummaryResponse(
        active_clients=sum(1 for item in wifi_sessions if item.session_active),
        total_sessions=len(wifi_sessions),
        total_download_kb=sum(item.data_download_kb or 0 for item in wifi_sessions),
        clients=[
            WifiClientResponse(
                device_id=item.device_id,
                session_target=item.session_target,
                session_active=item.session_active,
                data_download_kb=item.data_download_kb or 0,
                duration_seconds=item.duration_seconds,
            )
            for item in wifi_sessions[:100]
        ],
    )

@router.get("/{id}", response_model=Sessions)
def get_session(id: int, session: Session = Depends(get_session),
                current_admin=Depends(get_current_admin)):
    session_data = session.get(Sessions, id)
    if not session_data:
        raise HTTPException(status_code=404, detail="Session not found")
    expire_session_if_needed(session_data, session)
    return session_data

@router.get("/device/{device_id}/active", response_model=Sessions | None)
def get_active_device_session(device_id: str,
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

@router.post("/start", response_model=Sessions, status_code=201)
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
        session_start=now,
        expires_at=now + timedelta(seconds=duration_seconds),
    )
    session.add(session_data)
    session.commit()
    session.refresh(session_data)
    return session_data

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
                  device_id: str | None = Query(default=None, min_length=1, max_length=100),
                  current_admin=Depends(get_optional_admin)):
    if current_admin is None:
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
                   device_id: str | None = Query(default=None, min_length=1, max_length=100),
                   current_admin=Depends(get_optional_admin)):
    if current_admin is None:
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

@router.patch("/{id}/end", response_model=Sessions)
def end_session(id: int, request: Request,
                session: Session = Depends(get_session),
                device_id: str | None = Query(default=None, min_length=1, max_length=100),
                current_admin=Depends(get_optional_admin)):
    if current_admin is None:
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
