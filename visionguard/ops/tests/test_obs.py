import asyncio
import io
import json
import logging
import sys
import urllib.request
from datetime import datetime, timezone

import pytest

sys.path.insert(0, "../services/incident_service")

from vg_obs import logs
from vg_obs.collectors import register_agent, register_incident, register_notification
from vg_obs.http import ObsMiddleware, normalize_path, serve_metrics
from vg_obs.metrics import Counter, Gauge, Histogram, CallbackMetric, Registry
import vg_obs.metrics as metrics_mod


# ---------- metrics
def test_counter_gauge_render():
    r = Registry()
    c = Counter("t_total", "doc", ("a",), r); g = Gauge("t_g", "g", registry=r)
    c.labels("x").inc(); c.labels("x").inc(2); g.set(3.5)
    out = r.render()
    assert 't_total{a="x"} 3' in out and "t_g 3.5" in out
    assert "# TYPE t_total counter" in out and "# TYPE t_g gauge" in out


def test_counter_cannot_decrease():
    with pytest.raises(ValueError):
        Counter("x_total", "d", registry=Registry()).inc(-1)


def test_histogram_cumulative_and_inf():
    r = Registry(); h = Histogram("lat", "d", buckets=(0.1, 1.0), registry=r)
    for v in (0.05, 0.5, 5): h.observe(v)
    out = r.render()
    assert 'lat_bucket{le="0.1"} 1' in out and 'lat_bucket{le="1.0"} 2' in out and 'lat_bucket{le="+Inf"} 3' in out
    assert "lat_count 3" in out and "lat_sum 5.55" in out


def test_label_escaping():
    r = Registry(); g = Gauge("e", "d", ("v",), r); g.labels('a"b\\c\nd').set(1)
    assert 'e{v="a\\"b\\\\c\\nd"} 1' in r.render()


def test_cardinality_cap(monkeypatch):
    monkeypatch.setattr(metrics_mod, "MAX_SERIES", 3)
    r = Registry(); c = Counter("c_total", "d", ("k",), r)
    for i in range(10): c.labels(str(i)).inc()
    out = r.render()
    assert out.count("c_total{") == 3 and "vg_obs_dropped_series_total 7" in out


def test_duplicate_registration_rejected():
    r = Registry(); Gauge("d", "x", registry=r)
    with pytest.raises(ValueError):
        Gauge("d", "x", registry=r)


def test_wrong_label_count():
    with pytest.raises(ValueError):
        Counter("w_total", "d", ("a", "b"), Registry()).labels("only-one")


def test_broken_collector_does_not_break_scrape():
    r = Registry(); Gauge("ok", "d", registry=r).set(1)
    CallbackMetric("bad", "d", "gauge", lambda: 1 / 0, r)
    out = r.render()
    assert "ok 1" in out and 'vg_obs_collector_errors_total{collector="bad"} 1' in out


# ---------- ASGI middleware (hand-driven; no FastAPI needed)
def call(mw, path="/x", method="GET", headers=()):
    sent = []

    async def receive(): return {"type": "http.request"}
    async def send(m): sent.append(m)
    asyncio.run(mw({"type": "http", "path": path, "method": method, "headers": list(headers)}, receive, send))
    return sent


def make_app(status=200, boom=False):
    async def app(scope, receive, send):
        if boom: raise RuntimeError("kaboom")
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b"hi"})
    return app


def test_middleware_records_and_sets_request_id():
    r = Registry(); mw = ObsMiddleware(make_app(), "svc", r)
    sent = call(mw, "/incidents/12345")
    hdr = dict(sent[0]["headers"])
    assert len(hdr[b"x-request-id"]) == 32
    out = r.render()
    assert 'vg_http_requests_total{service="svc",method="GET",route="/incidents/:id",status="200"} 1' in out
    assert 'vg_http_in_flight_requests{service="svc"} 0' in out


def test_request_id_accepted_when_sane_replaced_when_not():
    r = Registry(); mw = ObsMiddleware(make_app(), "svc", r)
    assert dict(call(mw, headers=[(b"x-request-id", b"abc-12345678")])[0]["headers"])[b"x-request-id"] == b"abc-12345678"
    bad = dict(call(mw, headers=[(b"x-request-id", b"bad id\r\ninjected: 1")])[0]["headers"])[b"x-request-id"]
    assert b"injected" not in bad and len(bad) == 32


def test_exception_counts_as_500_and_propagates():
    r = Registry(); mw = ObsMiddleware(make_app(boom=True), "svc", r)
    with pytest.raises(RuntimeError):
        call(mw)
    assert 'status="500"' in r.render() and 'vg_http_in_flight_requests{service="svc"} 0' in r.render()


def test_metrics_endpoint_and_token():
    r = Registry(); mw = ObsMiddleware(make_app(), "svc", r, metrics_token="s3cret-token")
    assert call(mw, "/metrics")[0]["status"] == 401
    ok = call(mw, "/metrics", headers=[(b"authorization", b"Bearer s3cret-token")])
    assert ok[0]["status"] == 200 and b"vg_service_start_time_seconds" in ok[1]["body"]
    open_mw = ObsMiddleware(make_app(), "svc2", Registry(), metrics_token="")
    assert call(open_mw, "/metrics")[0]["status"] == 200


def test_route_cardinality_is_bounded():
    r = Registry(); mw = ObsMiddleware(make_app(), "svc", r)
    for i in range(500): call(mw, f"/some/path{i}x")           # attacker-style unique paths
    assert r.render().count("vg_http_requests_total{") <= 201


def test_normalize_path():
    assert normalize_path("/incidents/INC-20261031-0a1b") == "/incidents/:id"
    assert normalize_path("/cameras/CAM_001/latest") == "/cameras/CAM_001/latest"
    assert normalize_path("/") == "/"
    assert normalize_path("/events/550e8400-e29b-41d4-a716-446655440000") == "/events/:id"


def test_lifespan_passthrough():
    seen = []
    async def app(scope, receive, send): seen.append(scope["type"])
    asyncio.run(ObsMiddleware(app, "s", Registry())({"type": "lifespan"}, None, None))
    assert seen == ["lifespan"]


# ---------- standalone server
def test_serve_metrics_for_workers():
    r = Registry(); Gauge("w_up", "d", registry=r).set(1)
    srv = serve_metrics(0, r, host="127.0.0.1")
    port = srv.server_address[1]
    try:
        assert b"w_up 1" in urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=3).read()
        assert urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=3).read() == b"ok"
    finally:
        srv.shutdown()


# ---------- logging
def render(msg="hello", **extra):
    h = logging.StreamHandler(io.StringIO()); h.setFormatter(logs.JsonFormatter("svc"))
    lg = logging.Logger("t"); lg.addHandler(h); lg.setLevel(10)
    lg.info(msg, extra=extra)
    return json.loads(h.stream.getvalue())


def test_json_log_has_context():
    with logs.bind(camera_id="CAM_001", incident_id="I1"):
        d = render()
    assert d["service"] == "svc" and d["camera_id"] == "CAM_001" and d["incident_id"] == "I1"
    assert "camera_id" not in render()                        # context does not leak out


def test_log_redacts_secrets():
    d = render("connect rtsp://admin:hunter2@10.0.0.5/stream failed", api_token="abc", rtsp_url="rtsp://u:p@h/x", status=3)
    assert "hunter2" not in json.dumps(d) and d["api_token"] == "[REDACTED]" and d["rtsp_url"] == "[REDACTED]" and d["status"] == 3


def test_exception_redacted():
    h = logging.StreamHandler(io.StringIO()); h.setFormatter(logs.JsonFormatter("svc"))
    lg = logging.Logger("t"); lg.addHandler(h)
    try: raise RuntimeError("cannot open rtsp://root:pw123@cam/1")
    except RuntimeError: lg.exception("x")
    assert "pw123" not in h.stream.getvalue()


# ---------- collectors against the real services
def test_incident_collector_with_real_service():
    from incident_service.db import SqliteRepository
    from incident_service import models as M
    repo = SqliteRepository(":memory:")
    class S: counters = {"created": 2, "merged": 1, "duplicates": 3, "rejected": 0}
    S.repo = repo
    r = Registry(); register_incident(S, r)
    out = r.render()
    assert 'vg_incidents{status="OPEN"} 0' in out and 'vg_incident_outbox{state="dead"} 0' in out
    assert 'vg_incident_events_total{outcome="duplicate"} 3' in out
    assert 'vg_review_labels_total{label="false_positive"} 0' in out


def test_oldest_open_age_uses_open_incidents_only():
    from incident_service.db import SqliteRepository
    repo = SqliteRepository(":memory:")
    cols = "event_type,camera_id,zone,status,first_event_time,last_event_time,last_event_ts,created_at,updated_at,incident_id"
    def ins(iid, status, created):
        repo._q(f"INSERT INTO incidents({cols}) VALUES('PPE_VIOLATION','c','z',?,?,?,0,?,?,?)", (status, created, created, created, created, iid))
    ins("a", "OPEN", "2026-10-31T10:00:00.000Z"); ins("b", "OPEN", "2026-10-31T11:00:00.000Z"); ins("c", "CONFIRMED", "2026-01-01T00:00:00.000Z")
    now = datetime(2026, 10, 31, 10, 30, tzinfo=timezone.utc)
    s = repo.metrics_snapshot(now)
    assert s["oldest_open_age_s"] == 1800 and s["by_status"]["OPEN"] == 2 and s["by_status"]["CONFIRMED"] == 1


def test_agent_and_notify_collectors():
    class A: counters = {"analyzed": 4, "cached": 1, "fallback": 2, "skipped": 0}
    class St:
        def stats(self): return {"PENDING": 2, "DEAD": 1}
    class N: counters = {"sent": 5, "dead": 1}; store = St()
    r = Registry(); register_agent(A, r); register_notification(N, r)
    out = r.render()
    assert 'vg_agent_events_total{kind="fallback"} 2' in out and 'vg_notifications{status="DEAD"} 1' in out
