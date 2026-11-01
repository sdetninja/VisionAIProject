from __future__ import annotations
import logging
import time

from .config import RANK, Config
from .store import Store

log = logging.getLogger("notification_service")
ACKED = {"UNDER_REVIEW", "CONFIRMED"}
CLOSED = {"FALSE_POSITIVE", "RESOLVED"}


def _match(rule_match: dict, snap: dict, severity: str) -> bool:
    m = rule_match or {}
    if m.get("severity") and severity not in m["severity"]:
        return False
    if m.get("min_severity") and RANK.get(severity, 2) < RANK[m["min_severity"]]:
        return False
    for k in ("event_type", "camera_id", "zone"):
        if m.get(k) and snap.get(k) not in m[k]:
            return False
    if "needs_review" in m and bool(snap.get("needs_review")) != bool(m["needs_review"]):
        return False
    return True


class NotificationService:
    def __init__(self, cfg: Config, store: Store, channels: dict, clock=time.time):
        self.cfg, self.store, self.channels, self.clock = cfg, store, channels, clock
        self.counters = {"messages": 0, "sent": 0, "failed": 0, "dead": 0, "deferred": 0}

    # ------------------------------------------------------------ inbound
    def handle_message(self, msg: dict):
        topic, p = msg.get("topic", ""), msg.get("payload") or {}
        now = self.clock()
        self.counters["messages"] += 1
        if topic in ("incident.created.v1", "incident.updated.v1"):
            inc = p["incident"]
            self.store.upsert_snapshot(inc["incident_id"], inc, now)
            self._apply_status(inc["incident_id"], inc.get("status"))
        elif topic == "incident.analyzed.v1":
            iid = p["incident_id"]
            if not self.store.get_state(iid):
                self.store.upsert_snapshot(iid, {"incident_id": iid}, now)   # analysis raced ahead of creation
            self.store.set_state(iid, severity=p["severity"], analyzed=1)
            st = self.store.get_state(iid)
            st["snapshot"].setdefault("summary", p.get("summary"))
            st["snapshot"]["summary"] = p.get("summary")
            st["snapshot"]["recommended_actions"] = p.get("recommended_actions", [])
            self.store.upsert_snapshot(iid, st["snapshot"], now)
        elif topic == "incident.status_changed.v1":
            self._apply_status(p["incident"]["incident_id"], p.get("to"))
        self.tick(now)

    def _apply_status(self, iid, status):
        if status in ACKED:
            self.store.set_state(iid, acked=1)
            self.store.cancel_pending(iid, "escalation")
        if status in CLOSED:
            self.store.set_state(iid, acked=1, closed=1)
            self.store.cancel_pending(iid, "escalation")
            self.store.cancel_pending(iid, "initial")   # not yet delivered -> pointless now

    # ------------------------------------------------------------ scheduling
    def _msg(self, st, kind, extra=""):
        s = st["snapshot"]
        sev = st["severity"] or "UNRATED"
        what = s.get("event_type", "incident").replace("_", " ").lower()
        title = f"{'ESCALATION: ' if kind.startswith('escalation') else ''}{sev} {what} - {s.get('camera_id', '?')}/{s.get('zone', '?')}"
        lines = [s.get("summary") or f"{what} detected ({', '.join(s.get('missing_ppe') or []) or 'details in dashboard'})."]
        if s.get("needs_review"):
            lines.append("Evidence uncertain - needs human review.")
        if s.get("recommended_actions"):
            lines.append("Suggested: " + "; ".join(s["recommended_actions"][:2]))
        if extra:
            lines.append(extra)
        link = f"{self.cfg.dashboard_url.rstrip('/')}/incidents/{st['incident_id']}" if self.cfg.dashboard_url else ""
        return {"incident_id": st["incident_id"], "severity": sev, "kind": kind, "title": title,
                "text": "\n".join(lines), "link": link}

    def _enqueue_rules(self, rules, st, kind, key_extra, now, extra=""):
        sev = st["severity"] or "UNRATED"
        n = 0
        msg = self._msg(st, kind, extra)
        for r in rules:
            if not _match(r.get("match"), st["snapshot"], sev):
                continue
            for ch in r["channels"]:
                key = f"{st['incident_id']}|{kind}|{key_extra}|{r['name']}|{ch}"
                n += self.store.enqueue(key, st["incident_id"], kind, ch, msg, now)
        return n

    def tick(self, now=None) -> int:
        now = self.clock() if now is None else now
        for st in self.store.all_states("closed=0"):
            iid, sev = st["incident_id"], st["severity"]
            if st["notified_at"] is None:
                if st["analyzed"] or now - st["first_seen"] >= self.cfg.analysis_wait_sec:
                    self._enqueue_rules(self.cfg.routes, st, "initial", "", now)
                    self.store.set_state(iid, notified_at=now, notified_sev=sev or "UNRATED")
            elif sev and RANK.get(sev, 2) > RANK.get(st["notified_sev"] or "LOW", 0):
                # severity went up since first notice (late analysis or repeat pattern): tell people again
                self._enqueue_rules(self.cfg.routes, st, "upgrade", sev, now, f"Severity raised from {st['notified_sev']} to {sev}.")
                self.store.set_state(iid, notified_sev=sev)
            if st["notified_at"] is not None and not st["acked"]:
                for e in self.cfg.escalations:
                    if now - st["notified_at"] >= e["after_min"] * 60:
                        self._enqueue_rules([e], st, "escalation", e["name"], now,
                                            f"Not acknowledged after {e['after_min']} min.")
        return self.deliver(now)

    # ------------------------------------------------------------ delivery
    def deliver(self, now=None) -> int:
        now = self.clock() if now is None else now
        sent = 0
        for n in self.store.due(now):
            ch = self.channels.get(n["channel"])
            if ch is None:
                self.store.mark(n["id"], "DEAD", error="unknown channel")
                continue
            if self.store.sent_since(n["channel"], now - 60) >= self.cfg.rate_limit_per_min:
                self.counters["deferred"] += 1
                self.store.mark(n["id"], "PENDING", next_at=now + 30)
                continue
            try:
                ch.send(n["message"])
                self.store.mark(n["id"], "SENT", now=now, attempts_inc=1)
                self.counters["sent"] += 1
                sent += 1
            except Exception as e:
                att = n["attempts"] + 1
                self.counters["failed"] += 1
                if att >= self.cfg.max_attempts:
                    self.counters["dead"] += 1
                    self.store.mark(n["id"], "DEAD", error=repr(e), attempts_inc=1)
                    log.error("notification %s dead: %r", n["id"], e)
                else:
                    self.store.mark(n["id"], "PENDING", error=repr(e), attempts_inc=1,
                                    next_at=now + self.cfg.backoff_base_sec * 2 ** (att - 1))
        return sent

    def stats(self):
        return {**self.counters, "queue": self.store.stats()}
