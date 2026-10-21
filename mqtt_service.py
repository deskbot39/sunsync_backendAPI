from __future__ import annotations

import json
import logging
import threading

from sqlmodel import Session, select

from config import (
    MQTT_ENABLED, MQTT_HOST, MQTT_PASSWORD, MQTT_PORT, MQTT_TLS,
    MQTT_TOPIC_PREFIX, MQTT_USERNAME,
)
from db import engine
from models import Ina219Reading, SystemStatus
from observability import logger

SOLAR_SENSOR_ID = 1
BATTERY_SENSOR_ID = 2
# 3 x 18650 Li-ion cells in series (3S): 3.0 V empty to 4.2 V full per cell.
BATTERY_CELLS = 3
BATTERY_EMPTY_V = BATTERY_CELLS * 3.0
BATTERY_FULL_V = BATTERY_CELLS * 4.2
MAX_VOLTAGE_V = 40.0
MAX_CURRENT_A = 50.0

_client = None


def ina219_topic_filter() -> str:
    return f"{MQTT_TOPIC_PREFIX}/ina219/+"


def parse_ina219_message(topic: str, payload: bytes) -> tuple[int, float, float] | None:
    """Return (sensor_id, voltage_v, current_a) or None if the message is invalid."""
    parts = topic.split("/")
    if len(parts) < 3 or parts[-2] != "ina219" or parts[-1] not in {"1", "2"}:
        return None
    try:
        data = json.loads(payload)
        voltage = float(data["voltage_v"])
        current = float(data["current_a"])
    except (ValueError, KeyError, TypeError):
        return None
    if not (0 <= voltage <= MAX_VOLTAGE_V and abs(current) <= MAX_CURRENT_A):
        return None
    return int(parts[-1]), voltage, current


def battery_percentage(voltage: float) -> float:
    span = BATTERY_FULL_V - BATTERY_EMPTY_V
    return round(min(100.0, max(0.0, (voltage - BATTERY_EMPTY_V) / span * 100)), 1)


def store_ina219_reading(sensor_id: int, voltage: float, current: float) -> None:
    with Session(engine) as session:
        session.add(Ina219Reading(
            sensor_id=sensor_id, voltage_v=voltage, current_a=current,
            power_w=round(voltage * current, 3),
        ))
        if sensor_id in (SOLAR_SENSOR_ID, BATTERY_SENSOR_ID):
            previous = session.exec(
                select(SystemStatus).order_by(SystemStatus.timestamp.desc())
            ).first()
            is_solar = sensor_id == SOLAR_SENSOR_ID
            session.add(SystemStatus(
                battery_pct=(previous.battery_pct if previous else 0)
                if is_solar else battery_percentage(voltage),
                solar_voltage=voltage if is_solar else (previous.solar_voltage if previous else 0),
                solar_current=current if is_solar else (previous.solar_current if previous else 0),
                wifi_strength=previous.wifi_strength if previous else 0,
                uptime_seconds=previous.uptime_seconds if previous else 0,
            ))
        session.commit()


def handle_message(topic: str, payload: bytes) -> bool:
    parsed = parse_ina219_message(topic, payload)
    if parsed is None:
        logger.warning("Ignored invalid MQTT message",
                       extra={"event": "mqtt_invalid_message", "topic": topic})
        return False
    store_ina219_reading(*parsed)
    return True


def start_mqtt() -> None:
    global _client
    if not MQTT_ENABLED or _client is not None:
        return
    import paho.mqtt.client as mqtt

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    if MQTT_TLS:
        client.tls_set()

    def on_connect(c, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            c.subscribe(ina219_topic_filter(), qos=1)
            logger.info("MQTT connected", extra={"event": "mqtt_connected"})

    def on_message(c, userdata, msg):
        try:
            handle_message(msg.topic, msg.payload)
        except Exception:
            logger.exception("MQTT message handling failed",
                             extra={"event": "mqtt_handler_error"})

    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()
    _client = client


def stop_mqtt() -> None:
    global _client
    if _client is not None:
        _client.loop_stop()
        _client.disconnect()
        _client = None
