from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from sqlmodel import Session, select

from db import get_session
from models import Devices
from rate_limits import enforce_rate_limit

router = APIRouter(
    prefix="/devices",
    tags=["devices"],
)


@router.post("/register", response_model=Devices)
def register_device(request: Request,
                    device_id: str = Query(min_length=1, max_length=100),
                    session: Session = Depends(get_session)):
    enforce_rate_limit(request, "device_registration", limit=10)
    device = session.exec(
        select(Devices).where(Devices.device_id == device_id)
    ).first()

    now = datetime.now(timezone.utc)
    if device is None:
        device = Devices(device_id=device_id, last_seen=now)
    else:
        device.online_status = True
        device.last_seen = now

    session.add(device)
    session.commit()
    session.refresh(device)
    return device
