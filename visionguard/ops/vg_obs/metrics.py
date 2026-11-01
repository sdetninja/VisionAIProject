"""Minimal, dependency-free Prometheus metrics (text exposition format 0.0.4).

Why not prometheus_client? The workers/APIs of M2-M5 should not need extra deps just to be scraped, and this keeps the
exposition format unit-testable. M1 keeps its own prometheus_client metrics; both are scraped as separate targets.
Safety properties: thread-safe, label-cardinality cap per metric (a runaway label can't exhaust memory), a failing
collector never breaks a scrape."""
from __future__ import annotations

import math
import threading
import time

DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
MAX_SERIES = 500


def _esc_label(v) -> str:
    return str(v).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _esc_help(v: str) -> str:
    return v.replace("\\", "\\\\").replace("\n", "\\n")


def _num(v: float) -> str:
    if v != v:
        return "NaN"
    if v in (math.inf, -math.inf):
        return "+Inf" if v > 0 else "-Inf"
    return repr(float(v)) if not float(v).is_integer() else str(int(v))


def _fmt(name, labels: dict, value) -> str:
    if labels:
        body = ",".join(f'{k}="{_esc_label(v)}"' for k, v in labels.items())
        return f"{name}{{{body}}} {_num(value)}"
    return f"{name} {_num(value)}"


class _Metric:
    kind = "untyped"

    def __init__(self, name, doc, labelnames=(), registry=None):
        self.name, self.doc, self.labelnames = name, doc, tuple(labelnames)
        self._lock = threading.Lock()
        self._series: dict = {}
        self.dropped = 0
        (registry or REGISTRY).register(self)

    def labels(self, *values, **kw):
        if kw:
            values = tuple(kw[k] for k in self.labelnames)
        if len(values) != len(self.labelnames):
            raise ValueError(f"{self.name}: expected labels {self.labelnames}")
        key = tuple(str(v) for v in values)
        with self._lock:
            ch = self._series.get(key)
            if ch is None:
                if len(self._series) >= MAX_SERIES:
                    self.dropped += 1
                    return _NULL
                ch = self._series[key] = self._new_child()
            return ch

    def _lbl(self, key):
        return dict(zip(self.labelnames, key))

    # unlabelled convenience
    def _default(self):
        return self.labels()

    def render(self) -> list[str]:
        out = [f"# HELP {self.name} {_esc_help(self.doc)}", f"# TYPE {self.name} {self.kind}"]
        with self._lock:
            items = sorted(self._series.items())
        for key, ch in items:
            out.extend(self._lines(key, ch))
        return out


class _Val:
    def __init__(self):
        self.v = 0.0
        self.l = threading.Lock()

    def inc(self, n=1.0):
        with self.l:
            self.v += n

    def dec(self, n=1.0):
        self.inc(-n)

    def set(self, v):
        with self.l:
            self.v = float(v)


class _Null:
    def inc(self, *a, **k): pass
    dec = set = observe = inc


_NULL = _Null()


class Counter(_Metric):
    kind = "counter"

    def _new_child(self):
        return _Val()

    def inc(self, n=1.0):
        if n < 0:
            raise ValueError("counters only go up")
        self._default().inc(n)

    def _lines(self, key, ch):
        return [_fmt(self.name, self._lbl(key), ch.v)]


class Gauge(_Metric):
    kind = "gauge"

    def _new_child(self):
        return _Val()

    def set(self, v): self._default().set(v)
    def inc(self, n=1.0): self._default().inc(n)
    def dec(self, n=1.0): self._default().dec(n)

    def _lines(self, key, ch):
        return [_fmt(self.name, self._lbl(key), ch.v)]


class _Hist:
    def __init__(self, buckets):
        self.b, self.counts, self.sum, self.n = buckets, [0] * len(buckets), 0.0, 0
        self.l = threading.Lock()

    def observe(self, v):
        with self.l:
            self.sum += v
            self.n += 1
            for i, ub in enumerate(self.b):
                if v <= ub:
                    self.counts[i] += 1      # stored per-bucket; cumulated at render time


class Histogram(_Metric):
    kind = "histogram"

    def __init__(self, name, doc, labelnames=(), buckets=DEFAULT_BUCKETS, registry=None):
        self.buckets = tuple(sorted(buckets))
        super().__init__(name, doc, labelnames, registry)

    def _new_child(self):
        return _Hist(self.buckets)

    def observe(self, v): self._default().observe(v)

    def _lines(self, key, ch):
        base, out = self._lbl(key), []
        with ch.l:
            counts, s, n = list(ch.counts), ch.sum, ch.n
        # each observation was counted in EVERY bucket whose bound >= v, so counts are already cumulative
        for ub, c in zip(self.buckets, counts):
            out.append(_fmt(self.name + "_bucket", {**base, "le": _num(ub) if ub != int(ub) else f"{ub:.1f}"}, c))
        out.append(_fmt(self.name + "_bucket", {**base, "le": "+Inf"}, n))
        out.append(_fmt(self.name + "_sum", base, s))
        out.append(_fmt(self.name + "_count", base, n))
        return out


class CallbackMetric:
    """Values computed at scrape time (e.g. from a DB). fn() -> iterable of (labels_dict, value)."""

    def __init__(self, name, doc, kind, fn, registry=None):
        assert kind in ("gauge", "counter")
        self.name, self.doc, self.kind, self.fn = name, doc, kind, fn
        (registry or REGISTRY).register(self)

    def render(self):
        out = [f"# HELP {self.name} {_esc_help(self.doc)}", f"# TYPE {self.name} {self.kind}"]
        for labels, value in self.fn():
            out.append(_fmt(self.name, labels, value))
        return out


class Registry:
    def __init__(self):
        self._metrics, self._lock = {}, threading.Lock()
        self.collector_errors = {}

    def register(self, m):
        with self._lock:
            if m.name in self._metrics:
                raise ValueError(f"duplicate metric {m.name}")
            self._metrics[m.name] = m

    def render(self) -> str:
        with self._lock:
            ms = list(self._metrics.values())
        lines = []
        for m in ms:
            try:
                lines.extend(m.render())
            except Exception:           # a broken collector must not take down the whole scrape
                self.collector_errors[m.name] = self.collector_errors.get(m.name, 0) + 1
        lines.append("# HELP vg_obs_collector_errors_total Collectors that raised during a scrape")
        lines.append("# TYPE vg_obs_collector_errors_total counter")
        for k, v in sorted(self.collector_errors.items()):
            lines.append(_fmt("vg_obs_collector_errors_total", {"collector": k}, v))
        dropped = sum(getattr(m, "dropped", 0) for m in ms)
        lines += ["# HELP vg_obs_dropped_series_total Label sets refused by the cardinality cap",
                  "# TYPE vg_obs_dropped_series_total counter", _fmt("vg_obs_dropped_series_total", {}, dropped)]
        return "\n".join(lines) + "\n"


REGISTRY = Registry()
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
START = time.time()
