# Sun Sync Backend - Fly.io deployment

Deployment-only repository for the Sun Sync FastAPI backend.

## First deploy

```sh
fly launch --no-deploy --copy-config      # pick/confirm the app name
fly volumes create sunsync_data --size 1 --region sin
fly secrets set SECRET_KEY=<64 hex chars> INITIAL_ADMIN_PASSWORD=<12+ chars>
fly deploy
```

Generate a key with `python -c "import secrets; print(secrets.token_hex(32))"`.

## Notes

- The SQLite database and backups live on the `/data` volume (`DATA_DIR`).
  On first start the database is seeded from `clean.db` (admin user plus default
  wifi/charging rates). The default `admin123` password is rotated to
  `INITIAL_ADMIN_PASSWORD` on startup.
- Keep a single machine: SQLite and the in-process rate limiter are not shared.
- Set `ALLOWED_ORIGINS` in `fly.toml` to the origins of your frontend.
- Health check: `GET /health`.
