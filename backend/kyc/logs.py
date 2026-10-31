"""Logging for the API and the worker.

JSON lines in production (KYC_ENV=production, or KYC_LOG_FORMAT=json), readable text otherwise. Every
record carries the service name and, inside an API request, the request id, so one request's lines can
be found together; fields passed with extra={...} become JSON keys.
"""
from __future__ import annotations

import json
import logging
import os
from contextvars import ContextVar
from datetime import datetime, timezone

from . import config

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

# attributes every LogRecord has; anything else on a record came from extra={...}
_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime", "service", "request_id"}


class _Context(logging.Filter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def filter(self, record: logging.LogRecord) -> bool:
        record.service = self.service
        record.request_id = request_id.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
               "level": record.levelname.lower(), "service": record.service, "logger": record.name,
               "msg": record.getMessage()}
        if record.request_id:
            out["request_id"] = record.request_id
        out |= {k: v for k, v in vars(record).items() if k not in _STANDARD}
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


class TextFormatter(logging.Formatter):
    def __init__(self):
        super().__init__("%(asctime)s %(levelname)-7s %(service)s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        extras = {k: v for k, v in vars(record).items() if k not in _STANDARD}
        if record.request_id:
            extras = {"rid": record.request_id} | extras
        return line + ("  " + " ".join(f"{k}={v}" for k, v in extras.items()) if extras else "")


def setup(service: str) -> None:
    fmt = os.getenv("KYC_LOG_FORMAT") or ("json" if config.ENV == "production" else "text")
    handler = logging.StreamHandler()
    handler.addFilter(_Context(service))
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(os.getenv("KYC_LOG_LEVEL", "INFO").upper())
    for name in ("uvicorn", "uvicorn.error"):  # uvicorn's own lines in the same format
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    logging.getLogger("uvicorn.access").disabled = True  # the API writes its own access line, with user and request id
