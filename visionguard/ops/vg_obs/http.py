"""Pure-ASGI middleware (works with FastAPI/Starlette, no import of either) + a tiny stand-alone metrics server for workers."""
from __future__ import annotations

import hmac
import logging
import os
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import logs
from .metrics import CONTENT_TYPE, REGISTRY, Counter, Gauge, Histogram, Registry

log = logging.getLogger("vg.http")
RID_OK = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_ID_LIKE = re.compile(r"^(\d+|[0-9a-fA-F-]{16,}|.*\d.*[A-Za-z].*\d.*)$")   # numbers, uuids/hex, mixed alnum ids
QUIET = ("/metrics", "/health", "/healthz")
MAX_ROUTES = 200


def normalize_path(path: str) -> str:
    return "/" + "/".join(":id" if _ID_LIKE.match(s) else s for s in path.strip("/").split("/")) if path.strip("/") else "/"


def _bearer_ok(headers, token: str) -> bool:
    for k, v in headers:
        if k == b"authorization":
            return hmac.compare_digest(v.decode("latin1"), f"Bearer {token}")
    return False


class ObsMiddleware:
    def __init__(self, app, service: str, registry=None, metrics_path: str = "/metrics", metrics_token: str | None = None):
        r = registry if registry is not None else Registry()
        self.app, self.service, self.path = app, service, metrics_path
        self.token = metrics_token if metrics_token is not None else os.environ.get("VG_METRICS_TOKEN")
        self.reg = r
        self.req = Counter("vg_http_requests_total", "HTTP requests", ("service", "method", "route", "status"), r)
        self.dur = Histogram("vg_http_request_duration_seconds", "HTTP latency (to last body byte)", ("service", "method", "route"), registry=r)
        self.inflight = Gauge("vg_http_in_flight_requests", "Requests currently being served", ("service",), r)
        self.up = Gauge("vg_service_start_time_seconds", "Unix start time", ("service",), r)
        self.up.labels(service).set(time.time())
        self._routes: set = set()

    def _route(self, scope) -> str:
        route = scope.get("route")
        p = getattr(route, "path", None) or getattr(route, "path_format", None)
        if p:
            return p
        r = normalize_path(scope.get("path", "/"))
        if r not in self._routes:
            if len(self._routes) >= MAX_ROUTES:
                return "other"
            self._routes.add(r)
        return r

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope["path"] == self.path and scope["method"] == "GET":
            return await self._metrics(scope, send)
        hdrs = dict(scope.get("headers") or [])
        rid = (hdrs.get(b"x-request-id") or b"").decode("latin1")
        if not RID_OK.match(rid):
            rid = uuid.uuid4().hex
        tok = logs.set_request_id(rid)
        t0, status, done = time.perf_counter(), 500, False
        self.inflight.labels(self.service).inc()

        async def _send(msg):
            nonlocal status, done
            if msg["type"] == "http.response.start":
                status = msg["status"]
                msg = {**msg, "headers": list(msg.get("headers", [])) + [(b"x-request-id", rid.encode())]}
            elif msg["type"] == "http.response.body" and not msg.get("more_body"):
                done = True
            await send(msg)

        try:
            await self.app(scope, receive, _send)
        finally:
            self.inflight.labels(self.service).dec()
            dt, route, m = time.perf_counter() - t0, self._route(scope), scope["method"]
            self.req.labels(self.service, m, route, str(status)).inc()
            self.dur.labels(self.service, m, route).observe(dt)
            if scope["path"] not in QUIET or status >= 500:
                log.log(logging.ERROR if status >= 500 else logging.INFO, "request",
                        extra={"method": m, "route": route, "status": status, "duration_ms": round(dt * 1000, 1), "completed": done})
            logs.reset(tok)

    async def _metrics(self, scope, send):
        if self.token and not _bearer_ok(scope.get("headers") or [], self.token):
            body, code = b"unauthorized", 401
        else:
            body, code = self.reg.render().encode(), 200
        await send({"type": "http.response.start", "status": code,
                    "headers": [(b"content-type", CONTENT_TYPE.encode()), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def instrument(app, service: str, registry=None):
    """One-liner for FastAPI apps:  reg = instrument(app, "incident")  ->  /metrics + request ids + JSON access logs.
    Each app gets its OWN registry (several apps can live in one test process); returns it so domain collectors can attach."""
    registry = registry if registry is not None else Registry()
    app.add_middleware(ObsMiddleware, service=service, registry=registry)
    return registry


def serve_metrics(port: int, registry=None, host: str = "0.0.0.0"):
    """For CLI workers (no web server). GET /metrics and /healthz on a daemon thread. Returns the server (call .shutdown())."""
    reg = registry or REGISTRY
    token = os.environ.get("VG_METRICS_TOKEN")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def do_GET(self):
            if self.path == "/healthz":
                body, code, ct = b"ok", 200, "text/plain"
            elif self.path == "/metrics":
                if token and not hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {token}"):
                    body, code, ct = b"unauthorized", 401, "text/plain"
                else:
                    body, code, ct = reg.render().encode(), 200, CONTENT_TYPE
            else:
                body, code, ct = b"not found", 404, "text/plain"
            self.send_response(code)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer((host, port), H)
    threading.Thread(target=srv.serve_forever, daemon=True, name="vg-metrics").start()
    return srv
