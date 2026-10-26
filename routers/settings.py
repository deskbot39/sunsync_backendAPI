from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from db import get_session
from models import (
    PublicRatesResponse,
    SystemSettings,
    SystemSettingsResponse,
    SystemSettingsUpdate,
)
from device_auth import require_device_key
from routers.auth import get_current_admin

router = APIRouter(
    prefix="/settings",
    tags=["settings"],
    responses={404: {"description": "Not found"}},
    )

@router.get("/public/{id}", response_model=PublicRatesResponse,
            dependencies=[Depends(require_device_key)])
def get_public_rates(id: int, session: Session = Depends(get_session)):
    settings = session.get(SystemSettings, id)
    if not settings:
        raise HTTPException(status_code=404, detail="Settings not found")
    return settings

@router.get("/{id}", response_model=SystemSettingsResponse)
def get_settings(id: int, session: Session = Depends(get_session),
                 current_admin=Depends(get_current_admin)):
    settings = session.exec(select(SystemSettings).where(SystemSettings.id == id)).first()
    if not settings:
        raise HTTPException(status_code=404, detail="Settings not found")
    return settings

@router.patch("/{id}", response_model=SystemSettingsResponse)
def update_settings(id: int,updated_settings: SystemSettingsUpdate,
    session: Session = Depends(get_session), current_admin=Depends(get_current_admin)):
    settings = session.get(SystemSettings, id)
    if not settings:
        raise HTTPException(status_code=404, detail="Settings not found")
    
    updates = updated_settings.model_dump(exclude_unset=True)
    wifi_rate_per_coin = updates.get(
        "wifi_rate_per_coin", settings.wifi_rate_per_coin
    )
    for key, value in updates.items():
        if key in {"wifi_rate_per_coin", "charging_rate_per_coin"}:
            continue
        setattr(settings, key, value)

    settings.wifi_rate_per_coin = wifi_rate_per_coin
    settings.charging_rate_per_coin = wifi_rate_per_coin
    settings.date_updated = datetime.now(timezone.utc)
    session.add(settings)
    session.commit()
    session.refresh(settings)
    return settings
