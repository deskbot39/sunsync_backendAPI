import csv
from datetime import datetime, timezone
from decimal import Decimal
from io import StringIO
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select
from db import get_session
from models import (
    Devices,
    Ina219Reading,
    OverviewSummaryResponse,
    SolarSummaryResponse,
    SystemHealthResponse,
    SystemSettings,
    SystemStatus,
    Sessions,
    Transactions,
)
from routers.auth import get_current_admin

router = APIRouter(
    prefix="/status",
    tags=["status"],
    responses={404: {"description": "Not found"}}
    )

@router.get("/solar-summary", response_model=SolarSummaryResponse)
def get_solar_summary(session: Session = Depends(get_session),
                      current_admin=Depends(get_current_admin)):
    statuses = session.exec(
        select(SystemStatus)
        .order_by(SystemStatus.id.desc())
        .limit(24)
    ).all()
    if not statuses:
        raise HTTPException(status_code=404, detail="Solar status not found")

    statuses.reverse()
    output_history = [item.solar_voltage * item.solar_current for item in statuses]
    latest = statuses[-1]
    return SolarSummaryResponse(
        current_output_watts=output_history[-1],
        battery_pct=latest.battery_pct,
        peak_output_watts=max(output_history),
        output_history=output_history,
        history_labels=[item.timestamp.isoformat() for item in statuses],
    )

SENSOR_FRESH_SECONDS = 15

@router.get("/sensors")
def get_sensors(session: Session = Depends(get_session),
                current_admin=Depends(get_current_admin)):
    def latest(sensor_id: int) -> dict:
        reading = session.exec(
            select(Ina219Reading)
            .where(Ina219Reading.sensor_id == sensor_id)
            .order_by(Ina219Reading.id.desc())
        ).first()
        if not reading:
            return {"available": False}
        timestamp = reading.timestamp
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - timestamp).total_seconds()
        return {
            "available": age <= SENSOR_FRESH_SECONDS,
            "bus_voltage_v": reading.voltage_v,
            "current_ma": reading.current_a * 1000,
            "power_w": reading.power_w,
            "timestamp": timestamp.isoformat(),
        }

    # Sensor 1 is the solar panel and sensor 2 is the battery (see mqtt_service.py).
    return {"ina219": latest(2), "ina219_panel": latest(1)}

@router.get("/overview-summary", response_model=OverviewSummaryResponse)
def get_overview_summary(session: Session = Depends(get_session),
                         current_admin=Depends(get_current_admin)):
    settings = session.exec(select(SystemSettings)).first()
    latest_status = session.exec(
        select(SystemStatus).order_by(SystemStatus.id.desc())
    ).first()
    if not settings or not latest_status:
        raise HTTPException(status_code=404, detail="Overview data not found")

    wifi_sessions = session.exec(
        select(Sessions).where(Sessions.session_type == "wifi")
    ).all()
    charging_sessions = session.exec(
        select(Sessions).where(Sessions.session_type == "charging")
    ).all()
    transactions = session.exec(select(Transactions)).all()
    return OverviewSummaryResponse(
        wifi_rate_per_coin=settings.wifi_rate_per_coin,
        charging_rate_per_coin=settings.charging_rate_per_coin,
        battery_pct=latest_status.battery_pct,
        active_wifi_users=sum(1 for item in wifi_sessions if item.session_active),
        active_charging_users=sum(1 for item in charging_sessions if item.session_active),
        total_sales=sum((item.total_amount for item in transactions), Decimal("0")),
        registered_devices=len(session.exec(select(Devices)).all()),
        active_sessions=sum(1 for item in wifi_sessions + charging_sessions if item.session_active),
        last_status_update=latest_status.timestamp,
    )

@router.get("/system-health", response_model=SystemHealthResponse)
def get_system_health(session: Session = Depends(get_session),
                      current_admin=Depends(get_current_admin)):
    latest = session.exec(
        select(SystemStatus).order_by(SystemStatus.id.desc())
    ).first()
    if not latest:
        raise HTTPException(status_code=404, detail="System health not found")
    return latest

@router.get("/export.csv", response_class=StreamingResponse)
def export_business_data(session: Session = Depends(get_session),
                         current_admin=Depends(get_current_admin)):
    settings = session.exec(select(SystemSettings)).first()
    devices = session.exec(select(Devices).order_by(Devices.id)).all()
    sessions = session.exec(select(Sessions).order_by(Sessions.session_start.desc())).all()
    transactions = session.exec(
        select(Transactions).order_by(Transactions.timestamp.desc())
    ).all()
    statuses = session.exec(
        select(SystemStatus).order_by(SystemStatus.id.desc())
    ).all()
    latest_status = statuses[0] if statuses else None
    exported_at = datetime.now(timezone.utc).isoformat()

    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Section", "Record ID", "Description", "Value", "Unit", "Recorded At"])

    def add_row(section, record_id, description, value, unit="", recorded_at=exported_at):
        writer.writerow([
            section,
            record_id or "",
            description,
            "" if value is None else value,
            unit,
            recorded_at,
        ])

    total_revenue = sum(
        (item.total_amount for item in transactions), Decimal("0")
    )
    active_sessions = sum(1 for item in sessions if item.session_active)
    add_row("Summary", "", "Total Revenue", f"{total_revenue:.2f}", "PHP")
    add_row("Summary", "", "Total Transactions", len(transactions), "transactions")
    add_row("Summary", "", "Registered Devices", len(devices), "devices")
    add_row("Summary", "", "Active Sessions", active_sessions, "sessions")
    if latest_status:
        add_row("Summary", "", "Current Battery Level", latest_status.battery_pct, "%")

    if settings:
        recorded_at = settings.date_updated.isoformat()
        add_row("System Settings", settings.id, "Wi-Fi Rate", settings.wifi_rate_per_coin,
                "PHP/coin", recorded_at)
        add_row("System Settings", settings.id, "Charging Rate", settings.charging_rate_per_coin,
                "PHP/coin", recorded_at)

    for item in devices:
        recorded_at = item.last_seen.isoformat()
        add_row("Device", item.id, "Device ID", item.device_id, recorded_at=recorded_at)
        add_row("Device", item.id, "Connection Status",
                "Online" if item.online_status else "Offline", recorded_at=recorded_at)
        add_row("Device", item.id, "Last Seen", recorded_at, recorded_at=recorded_at)

    for item in sessions:
        recorded_at = item.session_start.isoformat()
        add_row("Session", item.id, "Session Type", item.session_type.title(),
                recorded_at=recorded_at)
        add_row("Session", item.id, "Device", item.device_id, recorded_at=recorded_at)
        add_row("Session", item.id, "Session Target", item.session_target,
                recorded_at=recorded_at)
        add_row("Session", item.id, "Status",
                "Active" if item.session_active else "Inactive", recorded_at=recorded_at)
        add_row("Session", item.id, "Data Downloaded", item.data_download_kb or 0,
                "KB", recorded_at)
        add_row("Session", item.id, "Port Output", item.port_output_watts or 0,
            "W", recorded_at)
        add_row("Session", item.id, "Duration", item.duration_seconds or 0,
                "seconds", recorded_at)
        add_row("Session", item.id, "Paused At", item.paused_at,
            recorded_at=recorded_at)
        add_row("Session", item.id, "Paused Duration", item.paused_seconds,
            "seconds", recorded_at)
        add_row("Session", item.id, "Expires At", item.expires_at,
            recorded_at=recorded_at)
        add_row("Session", item.id, "Session End", item.session_end,
            recorded_at=recorded_at)

    for item in transactions:
        recorded_at = item.timestamp.isoformat()
        add_row("Transaction", item.id, "Transaction Type", item.transaction_type.title(),
                recorded_at=recorded_at)
        add_row("Transaction", item.id, "Device", item.device_id or "Unknown",
                recorded_at=recorded_at)
        add_row("Transaction", item.id, "Coin Value", item.coin_denomination,
                "PHP", recorded_at)
        add_row("Transaction", item.id, "Coin Count", item.coins_count,
                "coins", recorded_at)
        add_row("Transaction", item.id, "Total Amount", f"{item.total_amount:.2f}",
                "PHP", recorded_at)

    for item in statuses:
        recorded_at = item.timestamp.isoformat()
        add_row("System Status", item.id, "System Name", item.system_name,
                recorded_at=recorded_at)
        add_row("System Status", item.id, "Online Status",
                "Online" if item.is_online else "Offline", recorded_at=recorded_at)
        add_row("System Status", item.id, "Uptime", item.uptime_seconds,
                "seconds", recorded_at)
        add_row("System Status", item.id, "Battery Level", item.battery_pct,
                "%", recorded_at)
        add_row("System Status", item.id, "Solar Voltage", item.solar_voltage,
                "V", recorded_at)
        add_row("System Status", item.id, "Solar Current", item.solar_current,
                "A", recorded_at)
        add_row("System Status", item.id, "Wi-Fi Signal", item.wifi_strength,
                "dBm", recorded_at)
        add_row("System Status", item.id, "Last Ping", item.last_ping.isoformat(),
                recorded_at=recorded_at)

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=sunsync-business-data.csv"},
    )

@router.get("/{id}", response_model=SystemStatus)
def get_status(id: int, session: Session = Depends(get_session),
               current_admin=Depends(get_current_admin)):
    status = session.exec(select(SystemStatus).where(SystemStatus.id == id)).first()
    if not status:
        raise HTTPException(status_code=404, detail="Status not found")
    return status

@router.post("/uptime/{id}", response_model=SystemStatus)
def update_uptime(id: int, uptime_seconds: int = Query(ge=0),
                  session: Session = Depends(get_session),
                  current_admin=Depends(get_current_admin)):
    status = session.get(SystemStatus, id)
    if not status:
        raise HTTPException(status_code=404, detail="Status not found")

    status.is_online = True
    status.uptime_seconds = uptime_seconds
    status.last_ping = datetime.now(timezone.utc)
    session.add(status)
    session.commit()
    session.refresh(status)
    return status
