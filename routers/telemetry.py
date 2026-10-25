import ipaddress
from datetime import datetime, timedelta, timezone
from threading import Lock
from time import monotonic
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from sqlmodel import Session, select

from db import get_session
from device_auth import require_device_key
from models import Sessions
from rate_limits import enforce_rate_limit
from routers.auth import get_current_admin
from routers.parameters import expire_session_if_needed

router = APIRouter(prefix="/devices", tags=["telemetry"])

# The kiosk reports every ~15 s; two missed reports mean it is considered offline.
OFFLINE_AFTER_SECONDS = 45
MAX_TRACKED_DEVICES = 20
ORPHAN_GRACE_SECONDS = 60


class TelemetryClient(BaseModel):
    mac: str = Field(pattern=r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")
    # The kiosk sends "unknown" for a station that has not been given an address yet.
    ip: str = Field(max_length=45)
    rssi_dbm: int | None = None
    downloaded_bytes: int = Field(default=0, ge=0)
    uploaded_bytes: int = Field(default=0, ge=0)

    @field_validator("ip")
    @classmethod
    def validate_ip(cls, value: str) -> str:
        if value == "unknown":
            return value
        return str(ipaddress.ip_address(value))


class DeviceTelemetry(BaseModel):
    device_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    uptime_ms: int = Field(ge=0)
    upstream_connected: bool
    internet_sharing_enabled: bool
    internet_sharing_supported: bool = True
    clients: list[TelemetryClient] = Field(default_factory=list, max_length=32)
    # Backend session the kiosk is currently serving (0 = none); None from older firmware.
    local_session_id: int | None = Field(default=None, ge=0)


_LOCK = Lock()
_LATEST: dict[str, tuple[float, datetime, DeviceTelemetry]] = {}
# While the router is off the kiosk has no internet to hear "on" again, so every off command
# expires by itself and the kiosk restores power on its own timer.
ROUTER_OFF_MAX_SECONDS = 600
_ROUTER_OFF_UNTIL: datetime | None = None


def router_off_seconds() -> int:
    with _LOCK:
        until = _ROUTER_OFF_UNTIL
    if until is None:
        return 0
    return max(0, int((until - datetime.now(timezone.utc)).total_seconds()))


class RouterPowerRequest(BaseModel):
    power: Literal["on", "off"]


@router.post("/telemetry", dependencies=[Depends(require_device_key)])
def receive_telemetry(request: Request,
                      telemetry: DeviceTelemetry,
                      session: Session = Depends(get_session)):
    enforce_rate_limit(request, "device_telemetry", limit=30)
    with _LOCK:
        if (telemetry.device_id not in _LATEST
                and len(_LATEST) >= MAX_TRACKED_DEVICES):
            oldest = min(_LATEST, key=lambda key: _LATEST[key][0])
            del _LATEST[oldest]
        _LATEST[telemetry.device_id] = (
            monotonic(), datetime.now(timezone.utc), telemetry)

    # The kiosk ends its local session when the backend no longer lists it as active,
    # so an admin stopping a session from the cloud dashboard also cuts the user's access.
    active_ids = []
    now = datetime.now(timezone.utc)
    for item in session.exec(
            select(Sessions).where(Sessions.session_active == True)).all():  # noqa: E712
        expire_session_if_needed(item, session)
        if not item.session_active or item.id is None:
            continue
        # A session the kiosk is not serving (it rebooted or lost it) would block the user
        # from paying again, so close it. New sessions get a grace period to start locally.
        started = item.session_start
        if started is not None and started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if (telemetry.local_session_id is not None
                and item.id != telemetry.local_session_id
                and started is not None
                and (now - started).total_seconds() > ORPHAN_GRACE_SECONDS):
            item.session_active = False
            item.session_end = now
            session.add(item)
            session.commit()
            continue
        active_ids.append(item.id)
    return {"active_session_ids": active_ids, "router_off_seconds": router_off_seconds()}


@router.get("/router")
def get_router_power(current_admin=Depends(get_current_admin)):
    remaining = router_off_seconds()
    return {"power": "off" if remaining > 0 else "on", "off_seconds_remaining": remaining}


@router.post("/router")
def set_router_power(request_data: RouterPowerRequest,
                     current_admin=Depends(get_current_admin)):
    global _ROUTER_OFF_UNTIL
    with _LOCK:
        _ROUTER_OFF_UNTIL = (
            datetime.now(timezone.utc) + timedelta(seconds=ROUTER_OFF_MAX_SECONDS)
            if request_data.power == "off" else None
        )
    remaining = router_off_seconds()
    return {"power": "off" if remaining > 0 else "on", "off_seconds_remaining": remaining}


@router.get("/live")
def get_live_device(current_admin=Depends(get_current_admin)):
    with _LOCK:
        newest = max(_LATEST.values(), key=lambda entry: entry[0], default=None)
    if newest is None:
        return {"online": False, "age_seconds": None, "health": None, "clients": []}

    received_monotonic, received_at, telemetry = newest
    age = monotonic() - received_monotonic
    online = age <= OFFLINE_AFTER_SECONDS
    return {
        "online": online,
        "age_seconds": round(age, 1),
        "received_at": received_at.isoformat(),
        "health": {
            "device_id": telemetry.device_id,
            "uptime_ms": telemetry.uptime_ms + int(age * 1000),
            "upstream_connected": telemetry.upstream_connected,
            "internet_sharing_enabled": telemetry.internet_sharing_enabled,
            "internet_sharing_supported": telemetry.internet_sharing_supported,
        },
        "clients": [client.model_dump() for client in telemetry.clients],
    }
