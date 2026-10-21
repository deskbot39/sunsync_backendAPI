import hmac

from fastapi import Header, HTTPException, Request

from config import DEVICE_API_KEY
from observability import logger


def require_device_key(request: Request,
                       x_device_key: str | None = Header(default=None)) -> None:
    if (not DEVICE_API_KEY or not x_device_key or
            not hmac.compare_digest(x_device_key.encode(), DEVICE_API_KEY.encode())):
        logger.warning(
            "Device authentication failed",
            extra={
                "event": "device_auth_failed",
                "path": request.url.path,
                "client_host": request.client.host if request.client else None,
            },
        )
        raise HTTPException(status_code=401, detail="Invalid or missing device key")
