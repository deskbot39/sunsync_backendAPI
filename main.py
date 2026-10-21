from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncio
import logging
from time import perf_counter
from uuid import uuid4

from backup_service import backup_scheduler_loop
from config import ALLOWED_ORIGINS, DEBUG, SECRET_KEY, validate_security_configuration
from db import (
    create_admin_user,
    create_db_and_tables,
    create_dummy_data,
    create_initial_settings,
    create_initial_backup_schedule,
)
from observability import logger
from routers import auth, backups, devices, overview, parameters, sales, settings

@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_security_configuration()
    logger.info("Backend startup", extra={"event": "backend_startup"})
    create_db_and_tables()
    create_initial_settings()
    create_initial_backup_schedule()
    create_admin_user()
    if DEBUG:
        create_dummy_data()
    backup_task = asyncio.create_task(backup_scheduler_loop())
    try:
        yield
    finally:
        backup_task.cancel()
        await asyncio.gather(backup_task, return_exceptions=True)
    logger.info("Backend shutdown", extra={"event": "backend_shutdown"})

app = FastAPI(
    title="Sun Sync Backend",
    version="0.0.1",
    lifespan=lifespan,
    docs_url="/docs" if DEBUG else None,
    redoc_url="/redoc" if DEBUG else None,
)

@app.middleware("http")
async def log_http_request(request: Request, call_next):
    request_id = uuid4().hex
    started_at = perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        log_level = (
            logging.ERROR if status_code >= 500
            else logging.WARNING if status_code >= 400
            else logging.INFO
        )
        logger.log(
            log_level,
            "HTTP request completed",
            extra={
                "event": "http_request",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
                "duration_ms": round((perf_counter() - started_at) * 1000, 2),
                "client_host": request.client.host if request.client else None,
            },
        )

origins = [origin.strip() for origin in ALLOWED_ORIGINS.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(settings.router)
app.include_router(backups.router)
app.include_router(overview.router)
app.include_router(sales.router)
app.include_router(parameters.router)
app.include_router(auth.router)
app.include_router(devices.router)

@app.get("/")
async def index() -> dict[str, str]:
    return {"message": "Sun Sync Backend is running!"}

@app.get("/health")
async def health_check() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "debug": DEBUG,
        "service": "Sun Sync Backend",
    }

app.state.secret_key = SECRET_KEY
app.state.debug = DEBUG