from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from paper_agent.web.context import current_context


_SECRET_VALUE = re.compile(
    r"(?i)\b(api[_-]?key|authorization|credential|password|passwd|secret|token)\b"
    r"\s*[:=]\s*(?:bearer\s+)?[^\s,;]+"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_SENSITIVE_NAMES = frozenset({
    "api_key", "apikey", "authorization", "credential", "password",
    "passwd", "secret", "token",
})


def redact(value: object) -> object:
    if isinstance(value, str):
        return _BEARER.sub("Bearer [REDACTED]", _SECRET_VALUE.sub(r"\1=[REDACTED]", value))
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if str(key).casefold() in _SENSITIVE_NAMES else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


class RedactingContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        context = current_context()
        record.request_id = context.request_id
        record.run_id = context.run_id
        record.worker_id = context.worker_id
        record.msg = redact(record.getMessage())
        record.args = ()
        for name in tuple(record.__dict__):
            if name.casefold() in _SENSITIVE_NAMES:
                setattr(record, name, "[REDACTED]")
            elif name not in _STANDARD_RECORD_FIELDS:
                setattr(record, name, redact(getattr(record, name)))
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": redact(record.getMessage()),
            "request_id": getattr(record, "request_id", None),
            "run_id": getattr(record, "run_id", None),
            "worker_id": getattr(record, "worker_id", None),
        }
        for name in ("code", "error_kind", "attempt"):
            value = getattr(record, name, None)
            if value is not None:
                payload[name] = redact(value)
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


_STANDARD_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__) | {
    "message", "asctime", "request_id", "run_id", "worker_id",
}


def configure_structured_logging(logger: logging.Logger) -> None:
    if any(getattr(handler, "_momo_structured", False) for handler in logger.handlers):
        return
    handler = logging.StreamHandler()
    handler._momo_structured = True  # type: ignore[attr-defined]
    handler.addFilter(RedactingContextFilter())
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
