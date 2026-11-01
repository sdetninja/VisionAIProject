"""Structured JSON logging with correlation context and secret redaction (stdlib only).
RTSP/camera credentials and tokens must never reach logs: values under sensitive keys are masked, and any
scheme://user:pass@host in a message is rewritten."""
from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import sys
import time
from contextlib import contextmanager

_ctx: contextvars.ContextVar = contextvars.ContextVar("vg_log_ctx", default={})
SENSITIVE = re.compile(r"secret|token|passw|authorization|api[_-]?key|credential|rtsp|cookie", re.I)
URL_CREDS = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@")
_STD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}
CONTEXT_KEYS = ("request_id", "camera_id", "incident_id", "model_version")


def redact_text(s: str) -> str:
    return URL_CREDS.sub(r"\1***@", s)


def redact(key: str, value):
    if SENSITIVE.search(key):
        return "[REDACTED]"
    if isinstance(value, str):
        return redact_text(value)
    return value


def get_context() -> dict:
    return dict(_ctx.get())


@contextmanager
def bind(**kw):
    """with bind(camera_id="CAM_001", incident_id=...): every log line inside carries these fields."""
    tok = _ctx.set({**_ctx.get(), **{k: v for k, v in kw.items() if v is not None}})
    try:
        yield
    finally:
        _ctx.reset(tok)


def set_request_id(rid: str):
    return _ctx.set({**_ctx.get(), "request_id": rid})


def reset(token):
    _ctx.reset(token)


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, r: logging.LogRecord) -> str:
        d = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(r.created)) + f".{int(r.msecs):03d}Z",
             "level": r.levelname, "service": self.service, "logger": r.name, "msg": redact_text(r.getMessage())}
        d.update({k: v for k, v in _ctx.get().items() if k in CONTEXT_KEYS})
        for k, v in r.__dict__.items():
            if k not in _STD and not k.startswith("_"):
                d[k] = redact(k, v)
        if r.exc_info:
            d["exc"] = redact_text(self.formatException(r.exc_info))[-2000:]
        return json.dumps(d, default=str)


def setup_logging(service: str, level: str | None = None, fmt: str | None = None) -> None:
    """VG_LOG_FORMAT=json (default) | text ; VG_LOG_LEVEL=INFO"""
    fmt = fmt or os.environ.get("VG_LOG_FORMAT", "json")
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(JsonFormatter(service) if fmt == "json" else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    for old in list(root.handlers):      # replace basicConfig()/previous handlers: no duplicate or unredacted output
        root.removeHandler(old)
    h._vg = True
    root.addHandler(h)
    root.setLevel((level or os.environ.get("VG_LOG_LEVEL", "INFO")).upper())
