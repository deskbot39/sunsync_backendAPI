from typing import Literal, Optional
from datetime import datetime, timezone
from decimal import Decimal
from pydantic import ConfigDict
from sqlmodel import SQLModel, Field, Column, DateTime

class SystemSettingsUpdate(SQLModel):
    model_config = ConfigDict(extra="forbid")

    wifi_ssid: Optional[str] = Field(default=None, min_length=1, max_length=64)
    wifi_password: Optional[str] = Field(default=None, min_length=8, max_length=128)
    wifi_rate_per_coin: Optional[int] = Field(default=None, ge=0)
    charging_rate_per_coin: Optional[int] = Field(default=None, ge=0)
    solar_battery_min_cutoff: Optional[float] = Field(default=None, ge=0, le=100)
    max_load_watts: Optional[float] = Field(default=None, gt=0)

class SystemSettingsResponse(SQLModel):
    id: int
    wifi_ssid: str
    wifi_rate_per_coin: int
    charging_rate_per_coin: int
    solar_battery_min_cutoff: float
    max_load_watts: float
    date_updated: datetime

class PublicRatesResponse(SQLModel):
    wifi_rate_per_coin: int
    charging_rate_per_coin: int

class ServiceSessionCreate(SQLModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1, max_length=100)
    session_type: Literal["wifi", "charging"]
    credits: int = Field(ge=1, le=10000)

class PasswordChangeRequest(SQLModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=12, max_length=128)

class BackupScheduleUpdate(SQLModel):
    frequency: Literal["manual", "daily", "weekly", "monthly"]

class BackupScheduleResponse(SQLModel):
    id: int
    frequency: Literal["manual", "daily", "weekly", "monthly"]
    last_backup_at: Optional[datetime]

class BackupFileResponse(SQLModel):
    filename: str
    size_bytes: int
    created_at: datetime
    download_url: str

class BackupStatusResponse(SQLModel):
    frequency: Literal["manual", "daily", "weekly", "monthly"]
    last_backup_at: Optional[datetime]
    next_backup_at: Optional[datetime]
    backups: list[BackupFileResponse]

class BackupRestoreRequest(SQLModel):
    filename: str = Field(min_length=1, max_length=255)

class ChargingSummaryResponse(SQLModel):
    used_ports: int
    inactive_ports: int
    current_load_watts: float
    average_session_seconds: int

class SolarSummaryResponse(SQLModel):
    current_output_watts: float
    battery_pct: float
    peak_output_watts: float
    output_history: list[float]
    history_labels: list[str]

class SystemHealthResponse(SQLModel):
    uptime_seconds: int
    is_online: bool
    last_ping: datetime

class OverviewSummaryResponse(SQLModel):
    wifi_rate_per_coin: int
    charging_rate_per_coin: int
    battery_pct: float
    active_wifi_users: int
    active_charging_users: int
    total_sales: Decimal
    registered_devices: int
    active_sessions: int
    last_status_update: datetime

class WifiClientResponse(SQLModel):
    device_id: str
    session_target: str
    session_active: bool
    data_download_kb: float
    duration_seconds: int | None

class WifiSummaryResponse(SQLModel):
    active_clients: int
    total_sessions: int
    total_download_kb: float
    clients: list[WifiClientResponse]

class TransactionCreate(SQLModel):
    device_id: str = Field(min_length=1, max_length=100)
    coin_denomination: Literal[1, 5, 10, 20]
    coins_count: int = Field(gt=0)
    transaction_type: str = Field(min_length=1, max_length=50)

class AdminUpdate(SQLModel):
    model_config = ConfigDict(extra="forbid")

    username: Optional[str] = None
    password_hash: Optional[str] = None
    is_active: Optional[bool] = None

class SystemSettings(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    wifi_ssid: str = Field(default="SunSync", nullable=False)
    wifi_password: str = Field(default="SunSync123", nullable=False)
    wifi_rate_per_coin: int = Field(default=180, nullable=False)
    charging_rate_per_coin: int = Field(default=180, nullable=False)
    solar_battery_min_cutoff: float = Field(default=20.0, nullable=False)
    max_load_watts: float = Field(default=40.0, nullable=False)
    date_updated: datetime = Field(sa_column=Column(DateTime(timezone=True)),
        default_factory=lambda: datetime.now(timezone.utc))

class SystemStatus(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    system_name: str = Field(default="SunSync", nullable=False)
    battery_pct: float = Field(ge=0, le=100)
    solar_voltage: float
    solar_current: float
    wifi_strength: int
    is_online: bool = Field(default=True)
    uptime_seconds: int = Field(default=0, ge=0, nullable=False)
    last_ping: datetime = Field(sa_column=Column(DateTime(timezone=True)),
        default_factory=lambda: datetime.now(timezone.utc))
    timestamp: datetime = Field(sa_column=Column(DateTime(timezone=True)),
        default_factory=lambda: datetime.now(timezone.utc))

class Devices(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    device_id: str = Field(index=True, unique=True, min_length=1, max_length=100)
    online_status: bool = Field(default=True)
    last_seen: datetime = Field(sa_column=Column(DateTime(timezone=True)),
        default_factory=lambda: datetime.now(timezone.utc))

class Sessions(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    device_id: str = Field(foreign_key="devices.device_id", index=True)
    session_type: str
    session_target: str = Field(index=True)
    data_download_kb: Optional[float] = Field(default=0.0)
    port_output_watts: Optional[float] = Field(default=None)
    session_active: bool = Field(default=True)
    duration_seconds: Optional[int] = Field(default=None)
    paused_at: Optional[datetime] = Field(
        sa_column=Column(DateTime(timezone=True)), default=None)
    paused_seconds: int = Field(default=0, ge=0, nullable=False)
    expires_at: Optional[datetime] = Field(
        sa_column=Column(DateTime(timezone=True)), default=None)
    session_start: datetime = Field(sa_column=Column(DateTime(timezone=True)),
        default_factory=lambda: datetime.now(timezone.utc))
    session_end: Optional[datetime] = Field(
        sa_column=Column(DateTime(timezone=True)), default=None)

class Transactions(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    device_id: Optional[str] = Field(default=None, foreign_key="devices.device_id", index=True)
    coin_denomination: int = Field(default=1, gt=0)
    coins_count: int = Field(gt=0)
    total_amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    transaction_type: str = Field(min_length=1, max_length=50)
    timestamp: datetime = Field(sa_column=Column(DateTime(timezone=True)),
        default_factory=lambda: datetime.now(timezone.utc))

class Admin(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str
    password_hash: str
    is_active: bool = Field(default=True)
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True)),
        default_factory=lambda: datetime.now(timezone.utc))

class BackupSchedule(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    frequency: str = Field(default="manual", nullable=False)
    last_backup_at: Optional[datetime] = Field(
        sa_column=Column(DateTime(timezone=True)), default=None)

class Ina219Reading(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    sensor_id: int = Field(index=True, ge=1, le=2)
    voltage_v: float
    current_a: float
    power_w: float
    timestamp: datetime = Field(sa_column=Column(DateTime(timezone=True)),
        default_factory=lambda: datetime.now(timezone.utc))
