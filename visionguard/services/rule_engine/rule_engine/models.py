"""Input/output contracts. Pure dataclasses (no third-party deps) so the engine is trivially testable.

Input  = M1 `TrackResult` (dict or any object with .model_dump(by_alias=True)).
Output = `ViolationEvent` dicts (schema 1.0) consumed by M3 Incident Service.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

SCHEMA_VERSION = "1.0"
BBox = tuple  # (x1, y1, x2, y2) pixels


def parse_ts(s: Optional[str]) -> float:
    """ISO-8601 -> epoch seconds. Event time (from the frame), never wall-clock, so replays are deterministic."""
    if not s:
        return datetime.now(timezone.utc).timestamp()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return datetime.now(timezone.utc).timestamp()


@dataclass(frozen=True)
class Det:
    cls: str
    conf: float
    bbox: BBox


@dataclass(frozen=True)
class TrackObj:
    track_id: str
    cls: str
    conf: float
    bbox: BBox
    age_frames: int = 1


@dataclass
class FrameInput:
    camera_id: str
    frame_id: int
    timestamp: str
    ts: float
    width: int
    height: int
    model_version: str
    tracks: list[TrackObj] = field(default_factory=list)
    ppe: list[Det] = field(default_factory=list)
    hazards: list[Det] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FrameInput":
        def c(x):
            return str(x.get("class", x.get("class_name"))).lower()

        def box(x):
            return tuple(float(v) for v in x["bbox"])

        ts = d.get("timestamp", "")
        return cls(
            camera_id=d["camera_id"], frame_id=int(d["frame_id"]), timestamp=ts, ts=parse_ts(ts),
            width=int(d.get("frame_width") or 0), height=int(d.get("frame_height") or 0),
            model_version=d.get("model_version", "unknown"),
            tracks=[TrackObj(t["track_id"], c(t), float(t.get("confidence", 1.0)), box(t),
                             int(t.get("age_frames", 1))) for t in d.get("tracks", [])],
            ppe=[Det(c(x), float(x.get("confidence", 1.0)), box(x)) for x in d.get("ppe_detections", [])],
            hazards=[Det(c(x), float(x.get("confidence", 1.0)), box(x)) for x in d.get("hazard_detections", [])],
        )

    @classmethod
    def coerce(cls, obj: Any) -> "FrameInput":
        if isinstance(obj, FrameInput):
            return obj
        if hasattr(obj, "model_dump"):
            obj = obj.model_dump(by_alias=True)
        return cls.from_dict(obj)
