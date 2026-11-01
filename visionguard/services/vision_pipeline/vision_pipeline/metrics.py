"""Prometheus metrics (defined once at module level to avoid duplicate registration)."""
from prometheus_client import Counter, Gauge, Histogram

_L = ["camera_id"]
FRAMES_CAPTURED = Counter("vg_frames_captured_total", "Frames sampled from the source", _L)
FRAMES_DROPPED = Counter("vg_frames_dropped_total", "Frames dropped (consumer too slow)", _L)
FRAMES_PROCESSED = Counter("vg_frames_processed_total", "Frames fully processed", _L)
PROCESSING_ERRORS = Counter("vg_processing_errors_total", "Frames that raised an error", _L)
DETECTIONS = Counter("vg_detections_total", "Detections by class", ["camera_id", "class_name"])
INFERENCE_SECONDS = Histogram("vg_inference_seconds", "Detector latency", _L,
                              buckets=(.005, .01, .02, .05, .1, .2, .5, 1, 2))
PIPELINE_LATENCY = Histogram("vg_pipeline_latency_seconds", "Capture -> TrackResult latency", _L,
                             buckets=(.02, .05, .1, .2, .5, 1, 2, 5))
ACTIVE_TRACKS = Gauge("vg_active_tracks", "Tracks in the latest frame", _L)
CAMERA_UP = Gauge("vg_camera_up", "1 if the camera is delivering frames", _L)
PROCESSING_FPS = Gauge("vg_processing_fps", "Smoothed processed frames per second", _L)
