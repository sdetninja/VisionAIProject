# M1 – Vision Pipeline

Ingestion → Detection (YOLO11) → Tracking (ByteTrack) in one process, with a rolling evidence frame buffer,
JSONL/callback outputs, an HTTP API and Prometheus metrics.

```
Webcam/RTSP/MP4 ─► CameraReader thread ─► bounded queue (drop-oldest) ─► CameraWorker thread
                                                                          ├─ JPEG ► FrameBuffer (30 s)
                                                                          ├─ YOLO (shared, locked) [+ hazard model]
                                                                          ├─ ByteTrack (per camera) → anonymous IDs
                                                                          └─ TrackResult ► sinks (callback / JSONL) + API + metrics
```

## Setup (Mac)
```bash
cd visionguard/services/vision_pipeline
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -q                         # 11 tests; they use a fake detector: no model/GPU needed
```
Verified 2026-11-08 on macOS / Python 3.12: torch 2.15, ultralytics 8.4, supervision 0.29.1; YOLO runs on `mps` at ~16–20 ms/frame.

> `supervision` is pinned `<0.30`: `sv.ByteTrack` was deprecated in 0.28 and removed in 0.30+.
> 0.29 still works but logs a one-time `FutureWarning`. Long-term fix: port `tracker.py` to Roboflow's `trackers` package.

## Run
```bash
# 1. Headless/desktop demo with an OpenCV window (q to quit). macOS will ask for camera permission.
python -m vision_pipeline --config configs/pipeline.webcam.yaml --show --print

# 2. Service with API + metrics
CONFIG_PATH=configs/pipeline.webcam.yaml uvicorn vision_pipeline.app:app --port 8001
open http://localhost:8001/cameras/CAM_001/stream      # annotated MJPEG preview (debug only)

# 3. Replay a recorded clip deterministically (no frame drops)
python -m vision_pipeline --config configs/pipeline.video.yaml --print
# 4. Combined PPE + fire/smoke model from Milestone 0 (pair with M2's configs/rules.combined.yaml)
python -m vision_pipeline --config configs/pipeline.combined.yaml --show --print
```
`pipeline.combined.yaml` runs on `cpu` so it can share the machine with a training run; switch `device` to `mps` afterwards.
Step-by-step M1 + M2 tests (clip and webcam) and the full stack in the UI: `visionguard/MANUAL_TESTING.md`.
To replay a file instead: `--config configs/pipeline.combined.clip.yaml` (plays `samples/combined_demo.mp4` in real time, a 36 s clip of
held-out test images: 0-9 s fire, 9-18 s smoke, 18-24 s missing helmets, 24-30 s compliant workers, 30-36 s no hazard).
Point its `source:` at your own clip to test other footage.
Verified 2026-11-09 with `combined-yolo-v2` (M1 + M2 on the demo clip): fire alarm, smoke alarm, 3 explicit helmet violations,
no events for compliant workers; smoke-only scenes with tiny distant plumes are missed and a storm cloud scores as smoke
(see `milestone0/reports/combined_model_report.md`). About 8 fps on CPU; `device` auto-picks mps. Clips in `samples/` are git-ignored.

`samples/factory.mp4` is not in the repo: drop any clip there (or point `source:` at another file) before using
`pipeline.video.yaml`. For the API with a file source, set `realtime: true` and `loop: true` so the camera stays up.

First run downloads `yolo11n.pt`. With COCO weights only `person` is detected; switch `model.weights` to
`runs/ppe-yolo-v0/weights/best.pt` (Milestone 0) and delete `keep_classes` to get PPE classes.

**Per-class confidence (`model.class_conf`).** `conf` is the default minimum for every class; `class_conf` lowers (or raises)
it for named classes, e.g. `class_conf: {fire: 0.25, smoke: 0.35}`. The model runs at the lowest of these values and each
detection is then filtered by its own class threshold, so weak fire/smoke boxes reach the rule engine without flooding
the tracker with weak PPE boxes. Keep each value at or below the matching rule-engine threshold (anything filtered here
never reaches M2). Values come from `milestone0/scripts/09_tune_hazard_thresholds.py`.

Docker: `docker build -t vg-vision .` works for RTSP/files; a laptop webcam is not reachable from Docker on macOS, so run the webcam demo natively.

## API
| Endpoint | Purpose |
|---|---|
| `GET /health` | `ok` if any camera is running, plus per-camera status |
| `GET /cameras` | state, captured/dropped/processed counts, fps, latest frame id |
| `GET /cameras/{id}/latest` | latest `TrackResult` |
| `GET /cameras/{id}/results?since_frame_id=N` | recent `TrackResult`s (polling) |
| `GET /cameras/{id}/frames/{frame_id}` | evidence JPEG from the rolling buffer (used by M3) |
| `GET /cameras/{id}/stream` | annotated MJPEG preview |
| `GET /metrics` | Prometheus |

## Output contract (`TrackResult`, schema 1.0)
```json
{"schema_version":"1.0","camera_id":"CAM_001","frame_id":1024,"timestamp":"2026-10-31T10:30:00.123Z",
 "model_version":"ppe-yolo-v0","frame_width":1280,"frame_height":720,"inference_ms":18.4,
 "tracks":[{"track_id":"CAM_001-T017","class":"person","confidence":0.97,"bbox":[120,80,350,600],"age_frames":42}],
 "ppe_detections":[{"class":"helmet","confidence":0.94,"bbox":[180,90,230,140]}],
 "hazard_detections":[]}
```
`tracks` = classes in `tracker.track_classes`; everything else is `ppe_detections` and is associated to persons by M2.
Track IDs are anonymous and scoped to camera + session.

## Hooking up M2 (Rule Engine)
In-process: `pipeline.add_sink(CallbackSink(rule_engine.on_track_result))`. Later: replace with a bus/gRPC sink implementing `ResultSink.emit`.
Evidence: M3 calls `FrameBuffer.get(camera_id, frame_id)`; swap `InMemoryFrameBuffer` for a Redis implementation when M3 is a separate process.

## Troubleshooting
| Symptom | Cause / fix |
|---|---|
| `AttributeError: module 'supervision' has no attribute 'ByteTrack'` | supervision ≥ 0.30 installed. `pip install "supervision>=0.21,<0.30"` |
| `cannot open 'samples/factory.mp4', retrying…` forever | Sample clip missing, see *Run* above |
| Webcam: `cannot open 0` | Grant camera access to your terminal app (System Settings → Privacy & Security → Camera), then restart the terminal |
| Only `person` detected | Using COCO `yolo11n.pt` with `keep_classes: [person]`. Switch to the M0 PPE weights |
| `/health` returns non-ok | No camera is in `running` state. Check `GET /cameras` for `reconnecting` / `finished` |

## Design notes / known limits
- Live sources drop the **oldest** queued frame when the consumer is slow (counted in `vg_frames_dropped_total`); files with `realtime: false` block instead.
- Unreadable sources reconnect with exponential backoff (1 s → 30 s); a failed frame is logged and counted, never fatal.
- One shared YOLO instance is serialised by a lock; if several cameras starve, batch inference or run one worker process per GPU.
- A single ByteTrack instance handles all tracked classes per camera (see `tracker.py` docstring).
- Sampling at `target_fps` happens before inference; tracker `frame_rate` is set to the same value.
