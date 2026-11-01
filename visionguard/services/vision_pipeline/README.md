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
pytest -q                         # tests use a fake detector: no model/GPU needed
```

## Run
```bash
# 1. Headless/desktop demo with an OpenCV window (q to quit). macOS will ask for camera permission.
python -m vision_pipeline --config configs/pipeline.webcam.yaml --show --print

# 2. Service with API + metrics
CONFIG_PATH=configs/pipeline.webcam.yaml uvicorn vision_pipeline.app:app --port 8001
open http://localhost:8001/cameras/CAM_001/stream      # annotated MJPEG preview (debug only)

# 3. Replay a recorded clip deterministically (no frame drops)
python -m vision_pipeline --config configs/pipeline.video.yaml --print
```
First run downloads `yolo11n.pt`. With COCO weights only `person` is detected; switch `model.weights` to
`runs/ppe-yolo-v0/weights/best.pt` (Milestone 0) and delete `keep_classes` to get PPE classes.
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

## Design notes / known limits
- Live sources drop the **oldest** queued frame when the consumer is slow (counted in `vg_frames_dropped_total`); files with `realtime: false` block instead.
- Unreadable sources reconnect with exponential backoff (1 s → 30 s); a failed frame is logged and counted, never fatal.
- One shared YOLO instance is serialised by a lock; if several cameras starve, batch inference or run one worker process per GPU.
- A single ByteTrack instance handles all tracked classes per camera (see `tracker.py` docstring).
- Sampling at `target_fps` happens before inference; tracker `frame_rate` is set to the same value.
