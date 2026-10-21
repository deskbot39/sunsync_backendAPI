from __future__ import annotations

import os
from pathlib import Path

_ENV_FILE = Path(__file__).resolve().parent / ".env"


def _load_dotenv() -> None:
    if not _ENV_FILE.exists():
        return

    for raw_line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        os.environ.setdefault(key, value)


_load_dotenv()


def get_env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    return value


SECRET_KEY = get_env("SECRET_KEY", "") or ""
DEBUG = str(get_env("DEBUG", "false")).strip().lower() in {"1", "true", "yes", "on"}
INITIAL_ADMIN_USERNAME = get_env("INITIAL_ADMIN_USERNAME", "admin") or "admin"
INITIAL_ADMIN_PASSWORD = get_env("INITIAL_ADMIN_PASSWORD")
ALLOWED_ORIGINS = get_env(
    "ALLOWED_ORIGINS",
    "http://localhost,http://localhost:8080,http://localhost:8000,http://localhost:5500,http://127.0.0.1:5500",
)


def validate_security_configuration() -> None:
    if (not SECRET_KEY or len(SECRET_KEY) < 32 or
            SECRET_KEY == "change-me-in-production"):
        raise RuntimeError(
            "Set SECRET_KEY to a random value of at least 32 characters."
        )

MQTT_ENABLED = str(get_env("MQTT_ENABLED", "false")).strip().lower() in {"1", "true", "yes", "on"}
MQTT_HOST = get_env("MQTT_HOST", "localhost") or "localhost"
MQTT_PORT = int(get_env("MQTT_PORT", "1883") or 1883)
MQTT_USERNAME = get_env("MQTT_USERNAME")
MQTT_PASSWORD = get_env("MQTT_PASSWORD")
MQTT_TLS = str(get_env("MQTT_TLS", "false")).strip().lower() in {"1", "true", "yes", "on"}
MQTT_TOPIC_PREFIX = (get_env("MQTT_TOPIC_PREFIX", "sunsync") or "sunsync").strip("/")
