from datetime import datetime, timezone
from threading import Lock
from time import monotonic

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
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


class TelemetryClient(BaseModel):
    mac: str = Field(max_length=17)
    ip: str = Field(max_length=45)
    rssi_dbm: int | None = None
    downloaded_bytes: int = Field(default=0, ge=0)
    uploaded_bytes: int = Field(default=0, ge=0)


class DeviceTelemetry(BaseModel):
    device_id: str = Field(min_length=1, max_length=100)
    uptime_ms: int = Field(ge=0)
    upstream_connected: bool
    internet_sharing_enabled: bool
    internet_sharing_supported: bool = True
    clients: list[TelemetryClient] = Field(default_factory=list, max_length=32)


_LOCK = Lock()
_LATEST: dict[str, tuple[float, datetime, DeviceTelemetry]] = {}


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
    for item in session.exec(
            select(Sessions).where(Sessions.session_active == True)).all():  # noqa: E712
        expire_session_if_needed(item, session)
        if item.session_active and item.id is not None:
            active_ids.append(item.id)
    return {"active_session_ids": active_ids}


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
