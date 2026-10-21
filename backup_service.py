import asyncio
import calendar
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlmodel import Session, select

from db import engine, sqlite_file_name
from models import BackupSchedule
from observability import logger

BACKUP_DIRECTORY = Path(sqlite_file_name).resolve().parent / "backups"
BACKUP_RETENTION_COUNT = 7
BACKUP_LOCK_PATH = BACKUP_DIRECTORY / ".backup.lock"


class BackupInProgressError(RuntimeError):
    pass


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def add_month(value: datetime) -> datetime:
    month_index = value.month - 1 + 1
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def is_backup_due(frequency: str, last_backup_at: datetime | None,
                  now: datetime | None = None) -> bool:
    if frequency == "manual":
        return False
    current_time = now or datetime.now(timezone.utc)
    previous_backup = as_utc(last_backup_at)
    if previous_backup is None:
        return True
    if frequency == "daily":
        return current_time >= previous_backup + timedelta(days=1)
    if frequency == "weekly":
        return current_time >= previous_backup + timedelta(days=7)
    if frequency == "monthly":
        return current_time >= add_month(previous_backup)
    return False


@contextmanager
def backup_lock():
    BACKUP_DIRECTORY.mkdir(parents=True, exist_ok=True)
    try:
        lock_handle = os.open(BACKUP_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        try:
            lock_age = datetime.now(timezone.utc).timestamp() - BACKUP_LOCK_PATH.stat().st_mtime
            if lock_age > 3600:
                BACKUP_LOCK_PATH.unlink()
                lock_handle = os.open(BACKUP_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            else:
                raise BackupInProgressError("Another backup is already in progress") from error
        except FileNotFoundError:
            raise BackupInProgressError("Another backup is already in progress") from error

    try:
        os.close(lock_handle)
        yield
    finally:
        BACKUP_LOCK_PATH.unlink(missing_ok=True)


def prune_old_backups() -> None:
    backups = sorted(
        BACKUP_DIRECTORY.glob("sunsync-backup-*.db"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for old_backup in backups[BACKUP_RETENTION_COUNT:]:
        old_backup.unlink(missing_ok=True)


def _create_database_backup_unlocked(prune=True) -> Path:
    source = Path(sqlite_file_name).resolve()
    if not source.exists():
        raise FileNotFoundError(f"Database file not found: {source}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    destination = BACKUP_DIRECTORY / f"sunsync-backup-{timestamp}.db"
    source_connection = sqlite3.connect(source)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()
    if prune:
        prune_old_backups()
    return destination


def resolve_backup_path(filename: str) -> Path:
    backup_path = (BACKUP_DIRECTORY / filename).resolve()
    if (backup_path.parent != BACKUP_DIRECTORY.resolve() or
            backup_path.suffix != ".db" or
            not backup_path.name.startswith("sunsync-backup-")):
        raise FileNotFoundError("Backup not found")
    if not backup_path.is_file():
        raise FileNotFoundError("Backup not found")
    return backup_path


def restore_database_backup(filename: str) -> Path:
    with backup_lock():
        selected_backup = resolve_backup_path(filename)
        source_connection = sqlite3.connect(selected_backup)
        try:
            try:
                integrity_result = source_connection.execute(
                    "PRAGMA integrity_check"
                ).fetchone()
                tables = {
                    row[0] for row in source_connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
            except sqlite3.DatabaseError as error:
                raise ValueError(
                    "Selected backup is not a valid SQLite database"
                ) from error
            if not integrity_result or integrity_result[0] != "ok":
                raise ValueError("Selected backup failed SQLite integrity check")
            required_tables = {
                "admin",
                "backupschedule",
                "devices",
                "sessions",
                "systemsettings",
                "systemstatus",
                "transactions",
            }
            if not required_tables.issubset(tables):
                raise ValueError("Selected backup is missing required Sun Sync tables")
        finally:
            source_connection.close()

        safety_backup = _create_database_backup_unlocked(prune=False)
        source_connection = sqlite3.connect(selected_backup)
        target_connection = sqlite3.connect(Path(sqlite_file_name).resolve())
        try:
            source_connection.backup(target_connection)
        finally:
            target_connection.close()
            source_connection.close()
        prune_old_backups()
        engine.dispose()
        return safety_backup


def create_database_backup() -> Path:
    with backup_lock():
        return _create_database_backup_unlocked()


def record_backup_time() -> datetime:
    backup_time = datetime.now(timezone.utc)
    with Session(engine) as session:
        schedule = session.exec(select(BackupSchedule)).first()
        if schedule is None:
            schedule = BackupSchedule(frequency="manual")
        schedule.last_backup_at = backup_time
        session.add(schedule)
        session.commit()
    return backup_time


async def backup_scheduler_loop() -> None:
    while True:
        try:
            with backup_lock():
                with Session(engine) as session:
                    schedule = session.exec(select(BackupSchedule)).first()
                    if schedule and is_backup_due(schedule.frequency, schedule.last_backup_at):
                        _create_database_backup_unlocked()
                        schedule.last_backup_at = datetime.now(timezone.utc)
                        session.add(schedule)
                        session.commit()
        except BackupInProgressError:
            pass
        except Exception:
            logger.exception(
                "Scheduled database backup failed",
                extra={"event": "scheduled_backup_failed"},
            )
        await asyncio.sleep(60)
