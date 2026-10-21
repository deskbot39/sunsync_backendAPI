from datetime import datetime, timezone
from pwdlib import PasswordHash
from sqlalchemy import event, inspect, text
from sqlmodel import SQLModel, Session, create_engine, select
from config import INITIAL_ADMIN_PASSWORD, INITIAL_ADMIN_USERNAME
from models import (
    Admin,
    BackupSchedule,
    Devices,
    Sessions,
    SystemSettings,
    SystemStatus,
    Transactions,
)


sqlite_file_name = "test.db"
sqlite_url = f"sqlite:///{sqlite_file_name}"
engine = create_engine(sqlite_url, echo=False)
ph_time = datetime.now(timezone.utc)
password_hash = PasswordHash.recommended()

@event.listens_for(engine, "connect")
def enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    dbapi_connection.execute("PRAGMA foreign_keys=ON")

def migrate_device_foreign_keys():
    inspector = inspect(engine)
    session_columns = {
        column["name"] for column in inspector.get_columns("sessions")
    }
    if (inspector.get_foreign_keys("sessions") and
            inspector.get_foreign_keys("transactions") and
            {"paused_at", "paused_seconds"}.issubset(session_columns)):
        return

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        transaction = connection.begin()
        try:
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "ix_devices_device_id_unique ON devices (device_id)"
            )
            connection.exec_driver_sql("DROP TABLE IF EXISTS sessions_new")
            connection.exec_driver_sql("""
                CREATE TABLE sessions_new (
                    id INTEGER PRIMARY KEY,
                    device_id VARCHAR(100) NOT NULL,
                    session_type VARCHAR NOT NULL,
                    session_target VARCHAR NOT NULL,
                    data_download_kb FLOAT,
                    port_output_watts FLOAT,
                    session_active BOOLEAN NOT NULL,
                    duration_seconds INTEGER,
                    paused_at DATETIME,
                    paused_seconds INTEGER NOT NULL DEFAULT 0,
                    expires_at DATETIME,
                    session_start DATETIME NOT NULL,
                    session_end DATETIME,
                    FOREIGN KEY (device_id) REFERENCES devices (device_id)
                )
            """)
            paused_at_column = (
                "paused_at" if "paused_at" in session_columns
                else "NULL AS paused_at"
            )
            paused_seconds_column = (
                "paused_seconds" if "paused_seconds" in session_columns
                else "0 AS paused_seconds"
            )
            connection.exec_driver_sql("""
                INSERT INTO sessions_new
                SELECT id, device_id, session_type, session_target,
                       data_download_kb, port_output_watts, session_active,
                       duration_seconds, {paused_at_column},
                       {paused_seconds_column}, expires_at,
                      session_start, session_end
                FROM sessions
            """.format(
                paused_at_column=paused_at_column,
                paused_seconds_column=paused_seconds_column,
            ))
            connection.exec_driver_sql("DROP TABLE sessions")
            connection.exec_driver_sql("ALTER TABLE sessions_new RENAME TO sessions")

            connection.exec_driver_sql("DROP TABLE IF EXISTS transactions_new")
            connection.exec_driver_sql("""
                CREATE TABLE transactions_new (
                    id INTEGER PRIMARY KEY,
                    device_id VARCHAR(100),
                    coin_denomination INTEGER NOT NULL DEFAULT 1,
                    coins_count INTEGER NOT NULL,
                    total_amount NUMERIC(10, 2) NOT NULL,
                    transaction_type VARCHAR(50) NOT NULL,
                    timestamp DATETIME NOT NULL,
                    FOREIGN KEY (device_id) REFERENCES devices (device_id)
                )
            """)
            connection.exec_driver_sql("""
                INSERT INTO transactions_new
                SELECT id, device_id, coin_denomination, coins_count,
                       total_amount, transaction_type, timestamp
                FROM transactions
            """)
            connection.exec_driver_sql("DROP TABLE transactions")
            connection.exec_driver_sql(
                "ALTER TABLE transactions_new RENAME TO transactions"
            )
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_sessions_device_id "
                "ON sessions (device_id)"
            )
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_sessions_session_target "
                "ON sessions (session_target)"
            )
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_transactions_device_id "
                "ON transactions (device_id)"
            )
            transaction.commit()
        except Exception:
            transaction.rollback()
            raise
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)
    if "coin_denomination" not in {
        column["name"] for column in inspect(engine).get_columns("transactions")
    }:
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE transactions ADD COLUMN coin_denomination "
                "INTEGER NOT NULL DEFAULT 1"
            ))
    if "device_id" not in {
        column["name"] for column in inspect(engine).get_columns("transactions")
    }:
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE transactions ADD COLUMN device_id VARCHAR(100)"
            ))
    migrate_device_foreign_keys()

def create_initial_settings():
    with Session(engine) as session:
        existing_settings = session.exec(select(SystemSettings)).first()
        if existing_settings is None:
            session.add(SystemSettings(
                wifi_ssid="SunSync",
                wifi_password="SunSync123",
                wifi_rate_per_coin=180,
                charging_rate_per_coin=180,
                solar_battery_min_cutoff=20.0,
                max_load_watts=40.0
            ))

            session.commit()
        else:
            return

def hash_password(password: str) -> str:
    return password_hash.hash(password)

def create_admin_user():
    with Session(engine) as session:
        existing_users = session.exec(select(Admin)).all()
        legacy_user = next(
            (user for user in existing_users if user.username == "admin"),
            None,
        )
        if (legacy_user is not None and
                password_hash.verify("admin123", legacy_user.password_hash)):
            if not INITIAL_ADMIN_PASSWORD or len(INITIAL_ADMIN_PASSWORD) < 12:
                raise RuntimeError(
                    "The stored admin account still uses the legacy default password. "
                    "Set INITIAL_ADMIN_PASSWORD to rotate it before starting the backend."
                )
            legacy_user.password_hash = hash_password(INITIAL_ADMIN_PASSWORD)
            session.add(legacy_user)
            session.commit()

        if existing_users:
            return
        if not INITIAL_ADMIN_PASSWORD or len(INITIAL_ADMIN_PASSWORD) < 12:
            raise RuntimeError(
                "Set INITIAL_ADMIN_PASSWORD to a unique password of at least 12 characters."
            )
        session.add(Admin(
            username=INITIAL_ADMIN_USERNAME,
            password_hash=hash_password(INITIAL_ADMIN_PASSWORD)
        ))
        session.commit()

def create_initial_backup_schedule():
    with Session(engine) as session:
        existing_schedule = session.exec(select(BackupSchedule)).first()
        if existing_schedule is None:
            session.add(BackupSchedule(frequency="manual"))
            session.commit()

def create_dummy_data():
    with Session(engine) as session:
        if session.exec(select(Devices)).first() is None:
            session.add_all([
                Devices(device_id=f"SUN-{index:03d}", online_status=index % 4 != 0)
                for index in range(1, 11)
            ])

        if session.exec(select(Sessions)).first() is None:
            session.add_all([
                Sessions(
                    device_id=f"SUN-{index:03d}",
                    session_type="wifi" if index % 2 else "charging",
                    session_target=f"user{index}",
                    data_download_kb=120.5 * index,
                    port_output_watts=8.0 * index if index % 2 == 0 else None,
                    session_active=index % 3 != 0,
                    duration_seconds=300 * index,
                )
                for index in range(1, 11)
            ])

        if session.exec(select(SystemStatus)).first() is None:
            session.add_all([
                SystemStatus(
                    system_name=f"SunSync Unit {index}",
                    battery_pct=45.0 + index * 4,
                    solar_voltage=18.0 + index * 0.5,
                    solar_current=2.0 + index * 0.25,
                    wifi_strength=-40 - index,
                    is_online=index % 4 != 0,
                    uptime_seconds=3600 * index,
                )
                for index in range(1, 11)
            ])

        if session.exec(select(Transactions)).first() is None:
            session.add_all([
                Transactions(
                    device_id=f"SUN-{index:03d}",
                    coin_denomination=[1, 5, 10, 20][(index - 1) % 4],
                    coins_count=index * 2,
                    total_amount=[1, 5, 10, 20][(index - 1) % 4] * index * 2,
                    transaction_type="wifi" if index % 2 else "charging",
                )
                for index in range(1, 11)
            ])

        session.commit()

def get_session():
    with Session(engine) as session:
        yield session
