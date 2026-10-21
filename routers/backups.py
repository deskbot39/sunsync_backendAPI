from datetime import datetime, timezone
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from backup_service import (
    BACKUP_DIRECTORY,
    BackupInProgressError,
    add_month,
    create_database_backup,
    record_backup_time,
    resolve_backup_path,
    restore_database_backup,
)
from db import get_session
from models import (
    BackupFileResponse,
    BackupSchedule,
    BackupScheduleResponse,
    BackupScheduleUpdate,
    BackupStatusResponse,
    BackupRestoreRequest,
)
from routers.auth import get_current_admin
from observability import logger
from rate_limits import enforce_rate_limit

router = APIRouter(prefix="/backups", tags=["backups"])


def next_backup_at(frequency: str, last_backup_at: datetime | None) -> datetime | None:
    if frequency == "manual" or last_backup_at is None:
        return None
    if frequency == "daily":
        from datetime import timedelta
        return last_backup_at + timedelta(days=1)
    if frequency == "weekly":
        from datetime import timedelta
        return last_backup_at + timedelta(days=7)
    return add_month(last_backup_at)


@router.get("/schedule", response_model=BackupScheduleResponse)
def get_backup_schedule(session: Session = Depends(get_session),
                        current_admin=Depends(get_current_admin)):
    schedule = session.exec(select(BackupSchedule)).first()
    if schedule is None:
        schedule = BackupSchedule(frequency="manual")
        session.add(schedule)
        session.commit()
        session.refresh(schedule)
    return schedule


@router.patch("/schedule", response_model=BackupScheduleResponse)
def update_backup_schedule(
        update: BackupScheduleUpdate,
        session: Session = Depends(get_session),
        current_admin=Depends(get_current_admin)):
    schedule = session.exec(select(BackupSchedule)).first()
    if schedule is None:
        schedule = BackupSchedule(frequency=update.frequency)
    else:
        schedule.frequency = update.frequency
    session.add(schedule)
    session.commit()
    session.refresh(schedule)
    return schedule


@router.get("/status", response_model=BackupStatusResponse)
def get_backup_status(session: Session = Depends(get_session),
                      current_admin=Depends(get_current_admin)):
    schedule = session.exec(select(BackupSchedule)).first()
    if schedule is None:
        schedule = BackupSchedule(frequency="manual")

    backup_files = []
    for backup_path in sorted(
            BACKUP_DIRECTORY.glob("sunsync-backup-*.db"),
            key=lambda path: path.stat().st_mtime,
            reverse=True):
        created_at = datetime.fromtimestamp(backup_path.stat().st_mtime, timezone.utc)
        backup_files.append(BackupFileResponse(
            filename=backup_path.name,
            size_bytes=backup_path.stat().st_size,
            created_at=created_at,
            download_url=f"/backups/{backup_path.name}",
        ))

    return BackupStatusResponse(
        frequency=schedule.frequency,
        last_backup_at=schedule.last_backup_at,
        next_backup_at=next_backup_at(schedule.frequency, schedule.last_backup_at),
        backups=backup_files,
    )


@router.post("/", response_model=dict[str, str])
def create_manual_backup(
        request: Request,
        current_admin=Depends(get_current_admin)):
    enforce_rate_limit(request, "backup_create", limit=2, window_seconds=60)
    try:
        backup_path = create_database_backup()
        created_at = record_backup_time()
    except BackupInProgressError as error:
        logger.warning(
            "Backup creation rejected because another backup is active",
            extra={"event": "backup_create_rejected", "actor": current_admin.username},
        )
        raise HTTPException(status_code=409, detail="Another backup is already in progress") from error
    except (OSError, sqlite3.Error, SQLAlchemyError) as error:
        logger.exception(
            "Manual database backup failed",
            extra={"event": "backup_create_failed", "actor": current_admin.username},
        )
        raise HTTPException(status_code=500, detail="Unable to create database backup") from error

    logger.info(
        "Manual database backup completed",
        extra={"event": "backup_create_succeeded", "actor": current_admin.username},
    )
    return {
        "filename": backup_path.name,
        "created_at": created_at.isoformat(),
        "download_url": f"/backups/{backup_path.name}",
    }


@router.post("/restore", response_model=dict[str, str])
def restore_backup(request: Request,
                   update: BackupRestoreRequest,
                   current_admin=Depends(get_current_admin)):
    enforce_rate_limit(request, "backup_restore", limit=1, window_seconds=300)
    try:
        safety_backup = restore_database_backup(update.filename)
    except FileNotFoundError as error:
        logger.warning(
            "Database restore rejected because the backup was not found",
            extra={
                "event": "backup_restore_rejected",
                "actor": current_admin.username,
                "backup_name": update.filename,
            },
        )
        raise HTTPException(status_code=404, detail="Backup not found") from error
    except BackupInProgressError as error:
        logger.warning(
            "Database restore rejected because another backup is active",
            extra={
                "event": "backup_restore_rejected",
                "actor": current_admin.username,
                "backup_name": update.filename,
            },
        )
        raise HTTPException(status_code=409, detail="Another backup is already in progress") from error
    except ValueError as error:
        logger.warning(
            "Database restore rejected because the selected backup is invalid",
            extra={
                "event": "backup_restore_rejected",
                "actor": current_admin.username,
                "backup_name": update.filename,
            },
        )
        raise HTTPException(status_code=422, detail=str(error)) from error
    except OSError as error:
        logger.exception(
            "Database restore failed",
            extra={
                "event": "backup_restore_failed",
                "actor": current_admin.username,
                "backup_name": update.filename,
            },
        )
        raise HTTPException(status_code=500, detail="Unable to restore database backup") from error
    except sqlite3.Error as error:
        logger.exception(
            "Database restore failed",
            extra={
                "event": "backup_restore_failed",
                "actor": current_admin.username,
                "backup_name": update.filename,
            },
        )
        raise HTTPException(status_code=500, detail="Unable to restore database backup") from error

    logger.info(
        "Database restore completed",
        extra={
            "event": "backup_restore_succeeded",
            "actor": current_admin.username,
            "backup_name": update.filename,
        },
    )
    return {
        "message": "Database restored successfully",
        "safety_backup": safety_backup.name,
    }


@router.get("/{filename}", response_class=FileResponse)
def download_backup(request: Request, filename: str,
                    current_admin=Depends(get_current_admin)):
    enforce_rate_limit(request, "backup_download", limit=10)
    try:
        backup_path = resolve_backup_path(filename)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Backup not found") from error
    if not backup_path.is_file():
        raise HTTPException(status_code=404, detail="Backup not found")
    logger.info(
        "Database backup downloaded",
        extra={
            "event": "backup_downloaded",
            "actor": current_admin.username,
            "backup_name": backup_path.name,
        },
    )
    return FileResponse(
        backup_path,
        media_type="application/x-sqlite3",
        filename=backup_path.name,
    )
