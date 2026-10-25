import ipaddress
import os
from hashlib import sha256
from threading import Lock
from time import monotonic

from fastapi import HTTPException, Request, status

from observability import logger

_WINDOWS: dict[str, tuple[float, int]] = {}
_LOCK = Lock()
_MAX_TRACKED_KEYS = 10000
# Fly.io sets FLY_APP_NAME on its machines. There, every request arrives through Fly's proxy,
# so the socket address is an internal proxy IP and the proxy-set Fly-Client-IP header holds
# the real client. Elsewhere (local runs) the header is client-controlled and is ignored.
_BEHIND_FLY_PROXY = bool(os.getenv("FLY_APP_NAME"))


def client_ip(request: Request) -> str:
    if _BEHIND_FLY_PROXY:
        forwarded = request.headers.get("fly-client-ip", "").strip()
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return request.client.host if request.client else "unknown"


def enforce_rate_limit(
    request: Request,
    scope: str,
    identity: str = "",
    limit: int = 30,
    window_seconds: int = 60,
) -> None:
    client_host = client_ip(request)
    digest = sha256(
        f"{client_host}\0{identity}".encode("utf-8")
    ).hexdigest()
    key = f"{scope}:{digest}"
    now = monotonic()

    with _LOCK:
        window_start, count = _WINDOWS.get(key, (now, 0))
        if now - window_start >= window_seconds:
            window_start, count = now, 0
        if count >= limit:
            logger.warning(
                "Request rate limit exceeded",
                extra={"event": "rate_limit_exceeded", "scope": scope},
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please try again later.",
                headers={"Retry-After": str(max(
                    1, int(window_seconds - (now - window_start))
                ))},
            )
        _WINDOWS[key] = (window_start, count + 1)

        if len(_WINDOWS) > _MAX_TRACKED_KEYS:
            expired = [
                stored_key for stored_key, (start, _) in _WINDOWS.items()
                if now - start >= window_seconds
            ]
            for stored_key in expired:
                del _WINDOWS[stored_key]
            if len(_WINDOWS) > _MAX_TRACKED_KEYS:
                del _WINDOWS[key]
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Rate limiter is at capacity. Please try again later.",
                    headers={"Retry-After": str(window_seconds)},
                )
