"""Versioned data contracts produced by the vision pipeline (consumed by M2 Rule Engine)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@dataclass
class FramePacket:
    """Internal (in-process) frame hand-off from the reader to the pipeline."""
    camera_id: str
    frame_id: int
    timestamp: str
    image: np.ndarray            # BGR
    captured_monotonic: float = field(default=0.0)

    @property
    def height(self) -> int:
        return int(self.image.shape[0])

    @property
    def width(self) -> int:
        return int(self.image.shape[1])


class Detection(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    class_name: str = Field(alias="class")
    confidence: float
    bbox: list[float]            # [x1, y1, x2, y2] in pixels


class Track(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    track_id: str                # anonymous, scoped to camera + session, e.g. CAM_001-T017
    class_name: str = Field(alias="class")
    confidence: float
    bbox: list[float]
    age_frames: int              # processed frames since the track first appeared


class DetectionResult(BaseModel):
    schema_version: str = SCHEMA_VERSION
    camera_id: str
    frame_id: int
    timestamp: str
    model_version: str
    inference_ms: float
    detections: list[Detection]
    hazard_detections: list[Detection] = []


class TrackResult(BaseModel):
    """Main output of M1. M2 uses `tracks` (persons/machines) + `ppe_detections` for association."""
    schema_version: str = SCHEMA_VERSION
    camera_id: str
    frame_id: int
    timestamp: str
    model_version: str
    frame_width: int
    frame_height: int
    inference_ms: float
    tracks: list[Track]
    ppe_detections: list[Detection]      # all non-tracked classes (helmet, gloves, ...)
    hazard_detections: list[Detection] = []
