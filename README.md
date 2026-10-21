# Sun Sync

## Local test deployment

1. Activate the project virtual environment.
2. Start the API:
   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000
   ```
3. Open the frontend pages directly from the project folder or serve them with a lightweight static server.
4. The frontend pages are configured for port `8000`. If you use a different backend port, set this before loading the frontend pages:
   ```html
   <script>
     window.SUNSYNC_API_BASE = 'http://localhost:8000';
   </script>
   ```
   Place it before the page script includes.
   When a phone loads the pages from the ESP32, `localhost` refers to the
   phone, not the backend PC. Set this value to the backend host's LAN address
   and port before enabling service sessions. If the backend cannot be reached,
   the page will not start a session or activate the charging relay.

## Environment settings

A `.env` file is supported for local development. Keep it out of source control:

```env
SECRET_KEY=
INITIAL_ADMIN_USERNAME=admin
INITIAL_ADMIN_PASSWORD=
DEBUG=false
ALLOWED_ORIGINS=http://localhost,http://localhost:8000
```

Generate a signing key with:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
```

Copy the generated key into `SECRET_KEY` and set a unique administrator
password of at least 12 characters before starting the backend.
The backend refuses to start without a valid `SECRET_KEY`. On a new database,
`INITIAL_ADMIN_PASSWORD` is required to create the initial administrator. The
known legacy `admin123` password is rotated to that configured value on startup;
the legacy password is never used to create a new account. Existing
non-default administrator credentials are not changed automatically.

Login, device registration, public device-session actions, and backup
create/restore endpoints have in-process rate limits: login is capped at 5
attempts per account and 20 per source IP every five minutes; device
registration at 10/minute per IP; unauthenticated device-session lookup at
30/minute per device and 60/minute per IP; unauthenticated session actions at
20/minute per device and 60/minute per IP; public session start at 10/minute
per device and 30/minute per IP; backup creation at 2/minute per IP;
restore at 1/5 minutes per IP; and backup downloads at 10/minute per IP. HTTP
access and authentication/backup events are written as JSON logs without
request bodies, passwords, bearer tokens, or query strings. Rate-limit state
is intentionally local to this process and resets on restart; it is suitable
as a development abuse guard, not a distributed/public-edge rate limiter.

`POST /sessions/start` creates a timed Wi-Fi or charging session for a
registered device using the configured service rate and requested credit
count. The ESP32 remains authoritative for checking and consuming its
in-memory coin credits before it activates the charging relay. This backend
endpoint records sessions but does not by itself activate hardware or prove
payment.

## MQTT (INA219 telemetry)

The backend subscribes to INA219 readings published by the ESP32. Enable it in `.env`:

```
MQTT_ENABLED=true
MQTT_HOST=localhost
MQTT_PORT=1883
MQTT_USERNAME=
MQTT_PASSWORD=
MQTT_TLS=false
MQTT_TOPIC_PREFIX=sunsync
```

Topics: `<prefix>/ina219/1` (solar input) and `<prefix>/ina219/2` (second sensor). Payload (JSON, QoS 1):

```
{"voltage_v": 12.5, "current_a": 2.0}
```

Every valid message is stored in `ina219_reading`; sensor 1 also adds a `SystemStatus` row so the dashboard solar data stays live. Invalid or out-of-range messages are logged and dropped. Use a broker with authentication (e.g. Mosquitto) and enable TLS before exposing it publicly.
