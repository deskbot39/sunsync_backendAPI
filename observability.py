import json
import logging
from datetime import datetime, timezone


class StructuredJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "event": getattr(record, "event", record.getMessage()),
        }
        for field in (
            "request_id",
            "method",
            "path",
            "status_code",
            "duration_ms",
            "client_host",
            "scope",
            "outcome",
            "actor",
            "backup_name",
        ):
            value = getattr(record, field, None)
            if value is not None:
                entry[field] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, separators=(",", ":"))


logger = logging.getLogger("sunsync")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredJsonFormatter())
    logger.addHandler(handler)
