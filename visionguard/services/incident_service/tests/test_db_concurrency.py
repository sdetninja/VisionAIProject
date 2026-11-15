"""Regression: concurrent reads on the shared SQLite connection must never see an empty result.
Before the fix `_q` returned the live cursor and released the lock before fetching, so parallel API requests
(the dashboard loads evidence images in parallel) intermittently got None -> 404 for an existing incident."""
import threading

from incident_service.db import SqliteRepository


def _incident(i):
    return {"event_type": "HAZARD_DETECTED", "camera_id": "CAM_001", "zone": "Z", "hazard_class": "fire",
            "missing_ppe": [], "rule_ids": ["R"], "status": "OPEN", "needs_review": True, "confidence": 0.5,
            "first_event_time": "t", "last_event_time": "t", "last_event_ts": float(i), "evidence_uris": [],
            "evidence_status": "ok"}


def test_parallel_reads_while_writing_never_miss(tmp_path):
    repo = SqliteRepository(str(tmp_path / "x.db"))
    iid = repo.create_incident(_incident(0))
    misses, stop = [], threading.Event()

    def reader():
        for _ in range(300):
            if repo.get_incident(iid) is None:
                misses.append(1)

    def writer():
        i = 1
        while not stop.is_set():
            repo.create_incident(_incident(i)); i += 1

    w = threading.Thread(target=writer); w.start()
    rs = [threading.Thread(target=reader) for _ in range(8)]
    [r.start() for r in rs]; [r.join() for r in rs]
    stop.set(); w.join()
    assert misses == []
    assert repo.list_incidents(limit=5)[1] > 1          # writer really ran concurrently
