import itertools
import numpy as np

_n = itertools.count(1)


def ev(**kw):
    n = next(_n)
    base = {"schema_version": "1.0", "event_id": f"00000000-0000-0000-0000-{n:012d}",
            "idempotency_key": f"CAM_001|CAM_001-T001|FLOOR|PPE_HELMET|{n}", "event_type": "PPE_VIOLATION",
            "rule_id": "PPE_HELMET", "camera_id": "CAM_001", "track_id": "CAM_001-T001", "zone": "FLOOR",
            "missing_ppe": ["helmet"], "confidence": 0.8, "needs_review": False,
            "first_seen_at": "2026-01-01T10:00:00.000Z", "timestamp": "2026-01-01T10:00:02.000Z",
            "bbox": [10, 10, 60, 120], "evidence_frames": [1, 2, 3]}
    base.update(kw)
    return base


def fake_fetch(camera_id, frame_id):
    return None if frame_id == 99 else np.full((200, 200, 3), 120, np.uint8)
