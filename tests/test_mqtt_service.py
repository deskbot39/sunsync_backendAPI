import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlmodel import Session, SQLModel, create_engine, select

import mqtt_service
from models import Ina219Reading, SystemStatus


class Ina219MqttTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.tmp.name) / 'mqtt.db'}")
        SQLModel.metadata.create_all(
            self.engine, tables=[Ina219Reading.__table__, SystemStatus.__table__]
        )
        with Session(self.engine) as session:
            session.add(SystemStatus(
                battery_pct=80, solar_voltage=0, solar_current=0,
                wifi_strength=-50, uptime_seconds=10,
            ))
            session.commit()
        patcher = patch.object(mqtt_service, "engine", self.engine)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.engine.dispose)

    def test_solar_reading_is_stored_and_updates_status(self):
        ok = mqtt_service.handle_message(
            "sunsync/ina219/1", b'{"voltage_v": 12.5, "current_a": 2.0}')
        self.assertTrue(ok)
        with Session(self.engine) as session:
            reading = session.exec(select(Ina219Reading)).one()
            self.assertEqual(reading.power_w, 25.0)
            latest = session.exec(
                select(SystemStatus).order_by(SystemStatus.id.desc())).first()
            self.assertEqual(latest.solar_voltage, 12.5)
            self.assertEqual(latest.battery_pct, 80)

    def test_second_sensor_does_not_create_status(self):
        mqtt_service.handle_message(
            "sunsync/ina219/2", b'{"voltage_v": 5.0, "current_a": 1.0}')
        with Session(self.engine) as session:
            self.assertEqual(len(session.exec(select(SystemStatus)).all()), 1)
            self.assertEqual(session.exec(select(Ina219Reading)).one().sensor_id, 2)

    def test_invalid_messages_are_rejected(self):
        for topic, payload in [
            ("sunsync/ina219/3", b'{"voltage_v": 5, "current_a": 1}'),
            ("sunsync/ina219/1", b"not json"),
            ("sunsync/ina219/1", b'{"voltage_v": 5}'),
            ("sunsync/ina219/1", b'{"voltage_v": 999, "current_a": 1}'),
        ]:
            self.assertFalse(mqtt_service.handle_message(topic, payload))


if __name__ == "__main__":
    unittest.main()
