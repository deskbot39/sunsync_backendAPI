import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine
from starlette.requests import Request

import rate_limits
from models import (
    Devices,
    ServiceSessionCreate,
    Sessions,
    SystemSettings,
    SystemSettingsUpdate,
)
from routers.parameters import start_device_session
from routers.settings import update_settings


class ServiceSessionStartTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            f"sqlite:///{Path(self.temporary_directory.name) / 'sessions.db'}"
        )
        SQLModel.metadata.create_all(
            self.engine,
            tables=[
                Devices.__table__,
                Sessions.__table__,
                SystemSettings.__table__,
            ],
        )
        with Session(self.engine) as session:
            session.add(Devices(device_id="test-device"))
            session.add(SystemSettings(
                id=1,
                wifi_rate_per_coin=120,
                charging_rate_per_coin=60,
            ))
            session.commit()
        with rate_limits._LOCK:
            rate_limits._WINDOWS.clear()

    def tearDown(self):
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def request(self):
        return Request({
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/sessions/start",
            "raw_path": b"/sessions/start",
            "query_string": b"",
            "headers": [],
            "client": ("192.0.2.20", 12345),
            "server": ("testserver", 80),
        })

    def test_charging_session_uses_rate_and_credit_count(self):
        with Session(self.engine) as session:
            result = start_device_session(
                ServiceSessionCreate(
                    device_id="test-device",
                    session_type="charging",
                    credits=3,
                ),
                self.request(),
                session,
            )

        self.assertEqual(result.duration_seconds, 180)
        self.assertEqual(result.session_target, "charger")
        self.assertEqual(
            int((result.expires_at - result.session_start).total_seconds()),
            180,
        )

    def test_active_session_prevents_a_second_session(self):
        with Session(self.engine) as session:
            start_device_session(
                ServiceSessionCreate(
                    device_id="test-device",
                    session_type="wifi",
                    credits=1,
                ),
                self.request(),
                session,
            )
        with Session(self.engine) as session:
            with self.assertRaises(HTTPException) as raised:
                start_device_session(
                    ServiceSessionCreate(
                        device_id="test-device",
                        session_type="charging",
                        credits=1,
                    ),
                    self.request(),
                    session,
                )

        self.assertEqual(raised.exception.status_code, 409)

    def test_settings_always_match_charging_rate_to_wifi_rate(self):
        with Session(self.engine) as session:
            updated = update_settings(
                1,
                SystemSettingsUpdate(charging_rate_per_coin=15),
                session,
                current_admin=None,
            )

        self.assertEqual(updated.wifi_rate_per_coin, 120)
        self.assertEqual(updated.charging_rate_per_coin, 120)

    def test_changing_wifi_rate_also_changes_charging_rate(self):
        with Session(self.engine) as session:
            updated = update_settings(
                1,
                SystemSettingsUpdate(
                    wifi_rate_per_coin=90,
                    charging_rate_per_coin=15,
                ),
                session,
                current_admin=None,
            )

        self.assertEqual(updated.wifi_rate_per_coin, 90)
        self.assertEqual(updated.charging_rate_per_coin, 90)


if __name__ == "__main__":
    unittest.main()
