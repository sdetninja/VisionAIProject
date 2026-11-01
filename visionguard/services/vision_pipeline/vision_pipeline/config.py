from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

import yaml
from pydantic import BaseModel, Field


class CameraConfig(BaseModel):
    camera_id: str
    source: Union[int, str]            # webcam index, RTSP url, or video file path
    target_fps: float = 10.0           # sampling rate fed to the detector
    enabled: bool = True
    loop: bool = False                 # files only: restart at the end
    realtime: bool = True              # files only: pace at native fps (False = as fast as consumed, no drops)
    max_width: Optional[int] = None    # downscale wide frames to bound compute


class ModelConfig(BaseModel):
    weights: str = "yolo11n.pt"
    version: Optional[str] = None      # reported in outputs; defaults to the weights file stem
    conf: float = 0.30
    iou: float = 0.50
    imgsz: int = 640
    device: Optional[str] = None       # None = auto (cuda -> mps -> cpu)
    half: bool = False                 # FP16, CUDA only
    keep_classes: Optional[list[str]] = None   # filter model output by class name


class TrackerConfig(BaseModel):
    track_classes: list[str] = ["person", "forklift", "truck"]
    activation_threshold: float = 0.25
    lost_buffer: int = 30              # processed frames a lost track is kept alive
    match_threshold: float = 0.8


class BufferConfig(BaseModel):
    seconds: float = 30.0              # rolling evidence window per camera
    jpeg_quality: int = 90


class PipelineConfig(BaseModel):
    cameras: list[CameraConfig]
    model: ModelConfig = ModelConfig()
    hazard_model: Optional[ModelConfig] = None    # separate fire/smoke model (optional)
    tracker: TrackerConfig = TrackerConfig()
    buffer: BufferConfig = BufferConfig()
    results_history: int = 300         # recent TrackResults kept per camera for polling
    jsonl_dir: Optional[str] = None    # if set, every TrackResult is appended to <dir>/<camera_id>.jsonl


def load_config(path: Union[str, Path]) -> PipelineConfig:
    return PipelineConfig.model_validate(yaml.safe_load(Path(path).read_text()))
