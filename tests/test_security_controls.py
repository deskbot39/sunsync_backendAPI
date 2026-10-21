import sqlite3
import tempfile
import unittest
from contextlib import closing
import json
import logging
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

import backup_service
import config
import db
import rate_limits
from observability import StructuredJsonFormatter
from models import Admin, SQLModel
from sqlalchemy import create_engine


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        with rate_limits._LOCK:
            rate_limits._WINDOWS.clear()

    def request_from(self, host: str) -> Request:
        return Request({
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/auth/login",
            "raw_path": b"/auth/login",
            "query_string": b"",
            "headers": [],
            "client": (host, 12345),
            "server": ("testserver", 80),
        })

    def test_exceeding_limit_returns_retry_after(self):
        request = self.request_from("192.0.2.10")
        rate_limits.enforce_rate_limit(
            request, "test", limit=2, window_seconds=60
        )
        rate_limits.enforce_rate_limit(
            request, "test", limit=2, window_seconds=60
        )

        with self.assertRaises(HTTPException) as raised:
            rate_limits.enforce_rate_limit(
                request, "test", limit=2, window_seconds=60
            )

        self.assertEqual(raised.exception.status_code, 429)
        self.assertIn("Retry-After", raised.exception.headers)

    def test_separate_clients_have_separate_buckets(self):
        rate_limits.enforce_rate_limit(
            self.request_from("192.0.2.10"), "test", limit=1
        )
        rate_limits.enforce_rate_limit(
            self.request_from("192.0.2.11"), "test", limit=1
        )


class AdminBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            f"sqlite:///{Path(self.temporary_directory.name) / 'admin.db'}"
        )
        SQLModel.metadata.create_all(self.engine, tables=[Admin.__table__])

    def tearDown(self):
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def test_missing_initial_password_blocks_first_admin_creation(self):
        with (
            patch.object(db, "engine", self.engine),
            patch.object(db, "INITIAL_ADMIN_USERNAME", "admin"),
            patch.object(db, "INITIAL_ADMIN_PASSWORD", None),
        ):
            with self.assertRaises(RuntimeError):
                db.create_admin_user()

    def test_initial_password_creates_admin_without_legacy_default(self):
        initial_password = "correct-horse-battery"
        with (
            patch.object(db, "engine", self.engine),
            patch.object(db, "INITIAL_ADMIN_USERNAME", "admin"),
            patch.object(db, "INITIAL_ADMIN_PASSWORD", initial_password),
        ):
            db.create_admin_user()
            with db.Session(self.engine) as session:
                admin = session.exec(
                    db.select(Admin).where(Admin.username == "admin")
                ).one()

        self.assertTrue(db.password_hash.verify(initial_password, admin.password_hash))
        self.assertFalse(db.password_hash.verify("admin123", admin.password_hash))

    def test_legacy_default_password_is_rotated(self):
        with db.Session(self.engine) as session:
            session.add(Admin(
                username="admin",
                password_hash=db.hash_password("admin123"),
            ))
            session.commit()

        replacement_password = "replace-this-legacy-password"
        with (
            patch.object(db, "engine", self.engine),
            patch.object(db, "INITIAL_ADMIN_USERNAME", "admin"),
            patch.object(db, "INITIAL_ADMIN_PASSWORD", replacement_password),
        ):
            db.create_admin_user()
            with db.Session(self.engine) as session:
                admin = session.exec(
                    db.select(Admin).where(Admin.username == "admin")
                ).one()

        self.assertTrue(
            db.password_hash.verify(replacement_password, admin.password_hash)
        )


class SecurityConfigurationTests(unittest.TestCase):
    def test_placeholder_secret_is_rejected(self):
        with patch.object(config, "SECRET_KEY", "change-me-in-production"):
            with self.assertRaises(RuntimeError):
                config.validate_security_configuration()

    def test_random_secret_of_sufficient_length_is_accepted(self):
        with patch.object(config, "SECRET_KEY", "a" * 64):
            config.validate_security_configuration()


class StructuredLoggingTests(unittest.TestCase):
    def test_formatter_outputs_event_fields_as_json(self):
        record = logging.LogRecord(
            "sunsync", logging.INFO, "source.py", 12, "backup done", (), None
        )
        record.event = "backup_create_succeeded"
        record.backup_name = "sunsync-backup-example.db"
        entry = json.loads(StructuredJsonFormatter().format(record))

        self.assertEqual(entry["event"], "backup_create_succeeded")
        self.assertEqual(entry["backup_name"], "sunsync-backup-example.db")
        self.assertNotIn("filename", entry)


class BackupRestoreTests(unittest.TestCase):
    def test_restore_validates_integrity_and_creates_safety_backup(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            backup_directory = root / "backups"
            database_path = root / "test.db"
            required_tables = (
                "admin",
                "backupschedule",
                "devices",
                "sessions",
                "systemsettings",
                "systemstatus",
                "transactions",
            )
            with closing(sqlite3.connect(database_path)) as connection:
                for table in required_tables:
                    connection.execute(f'CREATE TABLE "{table}" (value TEXT)')
                connection.execute("INSERT INTO admin VALUES ('before')")
                connection.commit()

            with (
                patch.object(backup_service, "BACKUP_DIRECTORY", backup_directory),
                patch.object(
                    backup_service,
                    "BACKUP_LOCK_PATH",
                    backup_directory / ".backup.lock",
                ),
                patch.object(backup_service, "sqlite_file_name", str(database_path)),
            ):
                backup = backup_service.create_database_backup()
                with closing(sqlite3.connect(database_path)) as connection:
                    connection.execute("DELETE FROM admin")
                    connection.execute("INSERT INTO admin VALUES ('after')")
                    connection.commit()

                safety_backup = backup_service.restore_database_backup(backup.name)

                with closing(sqlite3.connect(database_path)) as connection:
                    restored_value = connection.execute(
                        "SELECT value FROM admin"
                    ).fetchone()[0]
                with closing(sqlite3.connect(safety_backup)) as connection:
                    safety_value = connection.execute(
                        "SELECT value FROM admin"
                    ).fetchone()[0]

        self.assertEqual(restored_value, "before")
        self.assertEqual(safety_value, "after")

    def test_restore_rejects_path_traversal(self):
        with self.assertRaises(FileNotFoundError):
            backup_service.resolve_backup_path("..\\outside.db")

    def test_restore_rejects_sqlite_file_without_application_schema(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            backup_directory = root / "backups"
            backup_directory.mkdir()
            invalid_backup = backup_directory / "sunsync-backup-invalid.db"
            with closing(sqlite3.connect(invalid_backup)) as connection:
                connection.execute("CREATE TABLE unrelated (value TEXT)")
                connection.commit()

            with (
                patch.object(backup_service, "BACKUP_DIRECTORY", backup_directory),
                patch.object(
                    backup_service,
                    "BACKUP_LOCK_PATH",
                    backup_directory / ".backup.lock",
                ),
            ):
                with self.assertRaisesRegex(ValueError, "required Sun Sync tables"):
                    backup_service.restore_database_backup(invalid_backup.name)


if __name__ == "__main__":
    unittest.main()
