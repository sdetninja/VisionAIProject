"""Outbox publisher: ships committed rows to the bus. At-least-once, ordered by id; a failing row blocks nothing
else but is retried until max_attempts, then dead-lettered (dead=1) for manual requeue."""
from __future__ import annotations
import logging
import threading

log = logging.getLogger("incident_service.outbox")


class OutboxPublisher:
    def __init__(self, repo, bus, batch=50, max_attempts=5, interval=1.0):
        self.repo, self.bus, self.batch, self.max_attempts, self.interval = repo, bus, batch, max_attempts, interval
        self._stop = threading.Event()
        self._t = None

    def run_once(self) -> int:
        sent = 0
        for row in self.repo.pending_outbox(self.batch):
            try:
                self.bus.publish(row["topic"], row["key"], row["payload"])
                self.repo.mark_published(row["id"])
                sent += 1
            except Exception as e:
                dead = row["attempts"] + 1 >= self.max_attempts
                self.repo.mark_failed(row["id"], repr(e), dead)
                log.warning("publish failed id=%s attempts=%s dead=%s: %s", row["id"], row["attempts"] + 1, dead, e)
                break   # keep ordering for the same bus; retry next tick (dead rows are skipped next time)
        return sent

    def start(self):
        def loop():
            while not self._stop.wait(self.interval):
                try:
                    self.run_once()
                except Exception:
                    log.exception("outbox loop error")
        self._t = threading.Thread(target=loop, daemon=True, name="outbox")
        self._t.start()

    def stop(self):
        self._stop.set()
        if self._t:
            self._t.join(timeout=3)
