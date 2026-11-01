import json

from vision_pipeline.config import CameraConfig, PipelineConfig
from vision_pipeline.pipeline import VisionPipeline
from vision_pipeline.sinks import CallbackSink


def _cfg(video, tmp_path):
    return PipelineConfig(
        cameras=[CameraConfig(camera_id="CAM_T", source=video, target_fps=10, realtime=False)],
        jsonl_dir=str(tmp_path / "out"))


def test_end_to_end_tracking_and_outputs(synthetic_video, fake_detector, tmp_path):
    results = []
    p = VisionPipeline(_cfg(synthetic_video, tmp_path), detector=fake_detector, sinks=[CallbackSink(results.append)])
    p.start()
    assert p.wait_until_finished(timeout=60)
    p.stop()

    assert len(results) >= 25
    person_ids = {t.track_id for r in results for t in r.tracks if t.class_name == "person"}
    assert len(person_ids) == 1, f"one walking person must keep ONE track id, got {person_ids}"
    assert next(iter(person_ids)).startswith("CAM_T-T")
    # PPE detections pass through un-tracked
    assert all(d.class_name == "helmet" for r in results for d in r.ppe_detections)
    assert any(r.ppe_detections for r in results)
    # frame ids strictly increasing, schema fields present
    ids = [r.frame_id for r in results]
    assert ids == sorted(ids) and results[0].schema_version == "1.0" and results[0].model_version == "fake-v0"


def test_evidence_retrievable_by_camera_and_frame_id(synthetic_video, fake_detector, tmp_path):
    p = VisionPipeline(_cfg(synthetic_video, tmp_path), detector=fake_detector)
    p.start()
    p.wait_until_finished(60)
    p.stop()
    latest = p.buffer.latest_id("CAM_T")
    jpg = p.buffer.get("CAM_T", latest)
    assert jpg is not None and jpg[:2] == b"\xff\xd8"          # valid JPEG


def test_jsonl_sink_writes_valid_records(synthetic_video, fake_detector, tmp_path):
    p = VisionPipeline(_cfg(synthetic_video, tmp_path), detector=fake_detector)
    p.start()
    p.wait_until_finished(60)
    p.stop()
    lines = (tmp_path / "out" / "CAM_T.jsonl").read_text().splitlines()
    rec = json.loads(lines[-1])
    assert rec["camera_id"] == "CAM_T" and "tracks" in rec and "class" in rec["tracks"][0]


def test_detector_exception_does_not_kill_camera(synthetic_video, fake_detector, tmp_path):
    class Flaky:
        model_version = "flaky"
        n = 0

        def infer(self, f):
            Flaky.n += 1
            if Flaky.n % 5 == 0:
                raise RuntimeError("boom")
            return fake_detector.infer(f)

    p = VisionPipeline(_cfg(synthetic_video, tmp_path), detector=Flaky())
    p.start()
    p.wait_until_finished(60)
    p.stop()
    w = p.workers["CAM_T"]
    assert w.errors >= 3 and w.processed >= 20
