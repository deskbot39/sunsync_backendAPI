# Sun Sync Backend

FastAPI backend for the Sun Sync kiosk, deployed on Fly.io. Design notes are in
[SYSTEM_DOCUMENTATION.md](SYSTEM_DOCUMENTATION.md) and
[FUTURE_DEVELOPMENT_RECOMMENDATIONS.md](FUTURE_DEVELOPMENT_RECOMMENDATIONS.md); these
date from 2026-10-21 and predate later features (telemetry, MQTT, kiosk-confirmed sales),
so this README is the current reference for those.

## Tests

```
pip install -r requirements.txt pytest httpx
python -m pytest tests
```

The tests are not shipped in the Docker image (see `.dockerignore`).

## First deploy

```sh
fly launch --no-deploy --copy-config      # pick/confirm the app name
fly volumes create sunsync_data --size 1 --region sin
fly secrets set SECRET_KEY=<64 hex chars> DEVICE_API_KEY=<64 hex chars> INITIAL_ADMIN_PASSWORD=<12+ chars> \
  MQTT_HOST=<broker host> MQTT_USERNAME=<user> MQTT_PASSWORD=<password>
fly deploy
```

Generate a key with `python -c "import secrets; print(secrets.token_hex(32))"`.

## Notes

- The SQLite database and backups live on the `/data` volume (`DATA_DIR`).
  On first start the database is seeded from `clean.db` (admin user plus default
  wifi/charging rates). The default `admin123` password is rotated to
  `INITIAL_ADMIN_PASSWORD` on startup.
- Keep a single machine: SQLite, the in-process rate limiter and the live kiosk
  snapshot (`/devices/live`) are not shared between machines.
- The admin pages (`/login.html`, `/dashboard.html`, assets under `/resources`) are
  served from `web/` by the backend itself, so the dashboard and API share one origin.
  `ALLOWED_ORIGINS` is only needed for other frontends.
- Health check: `GET /health`.
- The ESP32 must send the `X-Device-Key: <DEVICE_API_KEY>` header on device
  endpoints (`/devices/register`, `/devices/telemetry`, `/settings/public/{id}`,
  `/sessions/start`, `/sessions/{id}/confirm`, `/sessions/device/{id}/active`, and session
  pause/resume/end).
- `POST /devices/telemetry` is sent by the kiosk every ~15 s with its uptime, upstream
  state, connected Wi-Fi clients and `local_session_ids` (every session it is serving).
  The admin dashboard reads the latest snapshot from `GET /devices/live` (kept in memory;
  the kiosk counts as offline after 45 s). The reply lists the active session ids so the
  kiosk ends sessions an admin stopped here; active sessions the kiosk does not list are
  closed after 60 s.
- Sales: `/sessions/start` holds the credits as `pending_credits`; the sale is recorded once
  the kiosk confirms it started the session (`POST /sessions/{id}/confirm`, the session
  appearing in telemetry, or a usage report). Starts the kiosk refuses book no sale.
- Capacity: at most `MAX_WIFI_USERS` (5) active wifi sessions and
  `MAX_CHARGING_USERS` (1) active charging session; further starts return `409`. The
  firmware's `MAX_WIFI_SESSIONS` must match.

## MQTT (INA219 telemetry)

The backend subscribes to `sunsync/ina219/+` on an **external** MQTT broker
(Fly does not run one). Set `MQTT_HOST`, `MQTT_USERNAME` and `MQTT_PASSWORD` as
secrets; `fly.toml` defaults to TLS on port 8883. The firmware publishes
`{"voltage_v": <V>, "current_a": <A>}` to `sunsync/ina219/1` (solar panel) and
`sunsync/ina219/2` (battery, 3S 18650 pack) every 2 seconds. The ESP32 and the
backend must use the same broker and `MQTT_TOPIC_PREFIX`. The firmware's
`PubSubClient` uses a plain TCP connection, so either point it at a broker
listener that accepts it or switch the firmware to `WiFiClientSecure` for TLS.
Set `MQTT_ENABLED=false` to turn the subscriber off.
