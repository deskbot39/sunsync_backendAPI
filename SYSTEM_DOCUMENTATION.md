# Sun Sync System Documentation

## 1. System Overview

Sun Sync is an administrator dashboard for monitoring and managing a solar-powered Wi-Fi and device charging system. It has two cooperating parts:

- A FastAPI backend that owns authentication, persistence, business rules, reporting, and backups.
- A static HTML/CSS/JavaScript frontend that displays dashboard panels and calls the backend through JSON and CSV APIs.

The system uses SQLite for persistence and SQLModel for database models and queries.

## 2. Project Structure

```text
SunSync/
|-- dashboard.html              Main authenticated dashboard
|-- index.html                  Public landing/status page
|-- login.html                  Administrator login page
|-- main.py                     FastAPI application and startup lifecycle
|-- config.py                   Environment-based configuration
|-- db.py                      SQLite engine, migrations, and seed data
|-- models.py                  SQLModel database and API schemas
|-- backup_service.py           SQLite backup creation and scheduler
|-- routers/
|   |-- auth.py                 Login, JWT validation, password changes
|   |-- backups.py              Backup schedule, history, creation, download
|   |-- devices.py              Device registration
|   |-- overview.py              Overview, health, solar data, CSV export
|   |-- parameters.py            Wi-Fi and charging session summaries
|   |-- sales.py                 Transaction endpoints
|   `-- settings.py              System settings endpoints
|-- resources/
|   |-- css/                    Page styles and theme styles
|   `-- js/                     Page behavior and API calls
|-- backups/                    Generated SQLite backup files
|-- test.db                     Local SQLite database
`-- SYSTEM_DOCUMENTATION.md     This document
```

The `.venv` directory is the local Python environment and should not be deployed as application source.

## 3. Runtime Architecture

### Backend startup

`main.py` creates the FastAPI application and registers the application lifespan. On startup it:

1. Creates or migrates database tables.
2. Creates default system settings if none exist.
3. Creates the default backup schedule if none exists.
4. Creates the default administrator if none exists.
5. Creates dummy data when `DEBUG=true`.
6. Starts the asynchronous backup scheduler.

On shutdown, the scheduler task is cancelled cleanly.

### Request flow

1. The browser loads a static HTML page.
2. JavaScript selects the API base URL from `window.SUNSYNC_API_BASE`, or defaults to `http://localhost:8000`.
3. Login sends form-encoded credentials to `/auth/login`.
4. The backend returns a JWT access token.
5. The browser stores the token in `localStorage` or `sessionStorage`.
6. Authenticated requests send `Authorization: Bearer <token>`.
7. The backend validates the token and confirms that the administrator is active.
8. The router reads or changes data in SQLite and returns JSON, CSV, or a backup file.

## 4. Authentication

Authentication is implemented in `routers/auth.py`.

### Login

`POST /auth/login` accepts OAuth2 form fields:

- `username`
- `password`

A successful response contains:

```json
{
  "access_token": "...",
  "token_type": "bearer"
}
```

JWT tokens expire after 30 minutes by default. Passwords are hashed with `pwdlib` and are never returned by an API response.

### Protected routes

Most administrative routes use `get_current_admin`. This dependency:

1. Decodes and validates the JWT.
2. Reads the username from the `sub` claim.
3. Finds the administrator in SQLite.
4. Rejects missing, expired, invalid, inactive, or unknown users.

### Password changes

`PATCH /auth/change-password` requires the current password and a new password of at least twelve characters. The current password must match before the stored hash is changed.

### Frontend login behavior

`resources/js/login.js` validates:

- Required username
- Username length of at least three characters
- Required password
- Password length of at least six characters

The backend remains the authority for actual credential validation. The frontend displays inline error and success messages and redirects successful users to the dashboard.

## 5. Database Design

The database is SQLite and is configured in `db.py`. The default database file is `test.db`.

### SystemSettings

Stores operational and pricing configuration:

- Wi-Fi SSID
- Wi-Fi password internally
- Wi-Fi rate per coin
- Charging rate per coin
- Minimum battery cutoff
- Maximum load in watts
- Last update time

The Wi-Fi password is intentionally excluded from API responses and CSV exports.

### SystemStatus

Stores solar and health readings:

- System name
- Battery percentage
- Solar voltage
- Solar current
- Wi-Fi signal strength
- Online status
- Router uptime
- Last ping time
- Status timestamp

The dashboard uses the newest status record for the health card and overview metrics. Solar charts use recent status records.

### Devices

Stores registered device identifiers and connection information:

- Device ID
- Online status
- Last seen time

Device IDs are unique.

### Sessions

Stores Wi-Fi and charging activity:

- Device ID
- Session type
- Session target
- Downloaded data
- Charging port output
- Active state
- Duration
- Pause state and paused duration
- Expiration time
- Start and end times

Expired sessions are closed when their session detail is requested.

### Transactions

Stores business transactions:

- Device ID
- Coin denomination
- Coin count
- Total amount
- Transaction type
- Timestamp

### Admin

Stores administrator accounts:

- Username
- Password hash
- Active state
- Creation time

### BackupSchedule

Stores automatic backup configuration:

- Frequency: `manual`, `daily`, `weekly`, or `monthly`
- Last backup time

## 6. API Reference

All routes below are relative to the backend base URL.

### Public routes

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Confirms that the backend is running. |
| `GET` | `/health` | Returns service status and debug state. |
| `POST` | `/auth/login` | Authenticates an administrator. |
| `GET` | `/settings/public/{id}` | Returns public pricing values. |
| `GET` | `/devices/{device_id}/active` | Returns a device's active session when available. |
| `POST` | `/devices/register` | Registers or refreshes a device. |

### Authenticated settings routes

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/settings/{id}` | Reads non-sensitive system settings. |
| `PATCH` | `/settings/{id}` | Updates allowed system settings. |
| `PATCH` | `/auth/change-password` | Changes the current administrator password. |

### Authenticated monitoring routes

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/status/system-health` | Returns the newest system health record. |
| `GET` | `/status/overview-summary` | Returns dashboard overview metrics. |
| `GET` | `/status/solar-summary` | Returns solar chart data and summary values. |
| `GET` | `/status/{id}` | Reads a specific system status record. |
| `POST` | `/status/uptime/{id}` | Updates uptime and last ping for a status record. |
| `GET` | `/sessions/wifi-summary` | Returns Wi-Fi totals and client sessions. |
| `GET` | `/sessions/charging-summary` | Returns charging totals and load data. |

### Transaction routes

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/transactions/?limit=100` | Lists recent transactions. |
| `GET` | `/transactions/{id}` | Reads one transaction. |
| `POST` | `/transactions/` | Creates a transaction and calculates its total amount. |

### Backup routes

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/backups/schedule` | Reads the configured backup frequency. |
| `PATCH` | `/backups/schedule` | Sets `manual`, `daily`, `weekly`, or `monthly`. |
| `GET` | `/backups/status` | Returns schedule state, next run, last run, and backup history. |
| `POST` | `/backups/` | Creates a manual database backup. |
| `POST` | `/backups/restore` | Validates and restores a selected database backup. |
| `GET` | `/backups/{filename}` | Downloads an existing authenticated backup. |
| `GET` | `/status/export.csv` | Downloads all non-sensitive business data as CSV. |

## 7. Dashboard Panels

The dashboard is defined in `dashboard.html` and controlled by `resources/js/dashboard.js`.

### Overview

The overview panel displays:

- Current Wi-Fi and charging rates
- Battery level
- Active Wi-Fi users
- Active charging users
- Total sales
- Registered devices
- Active sessions
- Recent system activity
- System health

The activity table is paginated and uses responsive stacked rows on small screens.

### System health

The health panel displays:

- Router uptime
- Charge Controller: `Data unavailable` until telemetry is connected
- System Temperature: `Data unavailable` until telemetry is connected

The diagnostic button refreshes only the system health endpoint. It does not reload sales, sessions, settings, or other dashboard data.

### Solar status

Displays current output, battery storage, peak output, and a solar output history chart.

### Wi-Fi status

Displays active clients, total sessions, downloaded data, and a paginated responsive client table.

### Charging status

Displays ports in use, current load, average session time, and a port distribution chart.

### Sales status

Displays today's revenue, transaction count, monthly revenue, and a paginated responsive transaction table.

### Settings

The settings panel opens a modal form. The form loads settings from the backend and submits a `PATCH` request. Successful and failed changes display inline status text and toast notifications.

## 8. Business CSV Export

The CSV export is generated by the backend so it includes all database records instead of only the rows currently visible in the dashboard.

The columns are:

```text
Section,Record ID,Description,Value,Unit,Recorded At
```

The export includes:

- Summary metrics
- Non-sensitive system settings
- All devices
- All Wi-Fi and charging sessions
- Session lifecycle information
- All transactions
- All system status records

The export excludes:

- Wi-Fi passwords
- Administrator password hashes
- JWT tokens
- Other authentication secrets

The frontend downloads the response as `sunsync-business-data.csv` and displays a toast after completion.

## 9. Database Backups

### Manual backups

The dashboard's `Backup Database Now` button calls `POST /backups/`. The backend:

1. Acquires the backup lock.
2. Uses SQLite's online backup API.
3. Writes a timestamped file under `backups/`.
4. Removes backups beyond the newest seven files.
5. Updates `last_backup_at`.
6. Returns the filename and authenticated download URL.

The frontend downloads the generated SQLite file and refreshes backup status.

### Automatic backups

The scheduler checks the stored frequency every 60 seconds.

- `manual`: no automatic backup
- `daily`: one backup after 24 hours
- `weekly`: one backup after 7 days
- `monthly`: one backup on the corresponding next calendar month

The scheduler re-reads the schedule while holding the backup lock. This prevents duplicate backups when multiple application processes notice the same overdue schedule.

### Backup safety

The lock is an atomic file created with exclusive creation. A lock older than one hour is treated as stale and removed. A second backup request receives a conflict response while another backup is active.

The SQLite online backup API produces a consistent database snapshot while the application is running. The latest backup should still be checked with SQLite integrity tools as part of operational monitoring.

### Backup status

`GET /backups/status` returns:

- Current frequency
- Last backup timestamp
- Next expected backup timestamp
- Recent backup filenames
- File sizes
- Creation timestamps
- Download URLs

### Database recovery

The Settings panel includes a **Recover Database** section. An administrator can select one of the retained backup files and choose **Recover Selected Backup**.

Recovery requires an explicit browser confirmation. The backend then:

1. Validates that the selected filename refers to a generated Sun Sync backup inside the backup directory.
2. Runs SQLite `PRAGMA integrity_check` against the selected file.
3. Creates a new safety backup of the current database without pruning the selected source file.
4. Restores the selected database into the live SQLite database using SQLite's online backup API.
5. Applies backup retention after the restore completes.
6. Reloads the dashboard so the interface reflects the recovered data.

The recovery endpoint is:

```text
POST /backups/restore
Content-Type: application/json
Authorization: Bearer <administrator-token>

{"filename":"sunsync-backup-20260921-130951.db"}
```

The response identifies the safety backup created before recovery. Recovery is restricted to active administrators, rejects path traversal and unknown files, and returns a conflict if another backup operation is already running. Because recovery replaces current application data, administrators should use the confirmation prompt carefully and verify the dashboard after the reload.

## 10. Configuration

Configuration is read from environment variables and an optional `.env` file.

```env
SECRET_KEY=
INITIAL_ADMIN_USERNAME=admin
INITIAL_ADMIN_PASSWORD=
DEBUG=false
ALLOWED_ORIGINS=http://localhost:8000,http://127.0.0.1:5500
```

### SECRET_KEY

Used to sign JWT tokens. The backend requires a random value of at least 32
characters and will not start if it is missing or uses the former placeholder.
Generate it with `python -c "import secrets; print(secrets.token_hex(32))"`.
Keep it in the local `.env` file or a secret manager; never commit it.

### Initial administrator

`INITIAL_ADMIN_PASSWORD` is required when creating the first administrator and
must be at least 12 characters. The old `admin` / `admin123` bootstrap is
removed. If the stored `admin` account still verifies against that legacy
password, startup rotates it to the configured initial password; other
existing administrator passwords are left unchanged.

### DEBUG

When enabled:

- Dummy data is created when tables are empty.
- FastAPI documentation routes are available.

When disabled, dummy data is not created and `/docs` and `/redoc` are disabled.

### ALLOWED_ORIGINS

Comma-separated browser origins allowed by CORS. Production should list only the actual frontend origin or origins.

### Rate limiting and logs

Login, device registration, unauthenticated device-session actions, and
administrator backup creation/restoration use conservative in-process
rate-limit windows: login allows 5 attempts per account and 20 per source IP
per five minutes; device registration 10/minute/IP; unauthenticated device
session lookup 30/minute/device and 60/minute/IP; unauthenticated session
actions 20/minute/device and 60/minute/IP; backup creation 2/minute/IP; and
restore 1/5 minutes/IP; backup downloads 10/minute/IP. These are a local
development safeguard: limits reset when the backend restarts and are not
shared between processes. Do not treat them as a substitute for a shared
limiter or trusted edge controls if deployment topology later changes.

HTTP requests and authentication/backup events are logged as JSON with a
request ID, path, status, elapsed time, and operation outcome. Request bodies,
query strings, passwords, bearer tokens, and secret values are not logged.

### Backup and restore safeguards

Backup listing, download, creation, and restoration require administrator
authentication. Creation and restore are rate limited. Restore rejects paths
outside the backup directory, checks SQLite integrity and required Sun Sync
tables, and creates a safety backup before replacing the database. Local
retention keeps up to seven generated backup files. Periodically test restore
using a known backup and verify the application data afterward.

### Frontend API base

When frontend and backend are on different hosts or ports, define the API base before the page scripts:

```html
<script>
  window.SUNSYNC_API_BASE = 'http://localhost:8001';
</script>
```

## 11. Local Development

From PowerShell in the project directory:

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000
```

Open `login.html` and sign in with the configured administrator account.

For a different API port, define `window.SUNSYNC_API_BASE` in the HTML page before the page JavaScript is loaded.

## 12. Validation Checklist

Before deployment, verify:

1. `DEBUG=false`.
2. `SECRET_KEY` is a random value of at least 32 characters.
3. `INITIAL_ADMIN_PASSWORD` is unique and at least 12 characters.
4. `ALLOWED_ORIGINS` contains only trusted frontend origins.
5. No administrator uses the old `admin123` password.
6. The backend starts and `/health` returns `status: ok`.
7. Login succeeds with valid credentials and fails with invalid credentials.
8. Repeated login attempts receive `429` with a retry interval.
9. Unauthenticated requests to administrative routes return `401`.
10. Settings can be read and updated by an administrator.
11. CSV export contains business data and no passwords or tokens.
12. Manual backup creates a file and the file passes SQLite integrity checking.
13. Restore rejects invalid files and creates a safety backup before replacement.
14. The selected automatic backup frequency is visible in `/backups/status`.
15. Backup retention does not exceed seven generated files.
16. JSON request and security-event logs contain no credentials or query strings.
17. The frontend works at the configured API host and port.
18. Tables and health controls remain usable on mobile widths.

## 13. Known Limitations

- Charge Controller telemetry is not connected.
- System Temperature telemetry is not connected.
- The application uses SQLite, which is appropriate for a small deployment but may need a server database for higher concurrency.
- Backup files are stored locally. Production deployments should copy backups to separate storage or another host.
- The scheduler runs inside the application process. The lock prevents duplicate backup work, but an external scheduler is preferable for a multi-instance production deployment.
- There is no full automated test suite yet; the validation checklist should be converted into integration tests before a high-risk production deployment.

## 14. Operational Recommendations

- Add `.gitignore` entries for `.env`, `.venv/`, `test.db`, `backups/`, and Python cache directories.
- Provide a `.env.example` without real credentials.
- Use a production WSGI/ASGI process manager and a single scheduler owner.
- Store backups outside the application directory or upload them to durable object storage.
- Monitor backup failures and disk usage.
- Rotate the JWT secret if it is ever exposed.
- Replace the default administrator password immediately.
- Restrict CORS methods and headers to those used by the application.
