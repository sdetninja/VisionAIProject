import time

from vision_pipeline.config import CameraConfig
from vision_pipeline.ingestion import CameraReader


def _drain(reader, timeout=10):
    got, t0 = [], time.time()
    while time.time() - t0 < timeout:
        p = reader.get(0.2)
        if p:
            got.append(p)
        elif reader.state in ("finished", "stopped"):
            break
    return got


def test_file_sampling_blocking_mode(synthetic_video):
    r = CameraReader(CameraConfig(camera_id="CAM_T", source=synthetic_video, target_fps=10, realtime=False))
    r.start()
    pkts = _drain(r)
    r.stop()
    assert 28 <= len(pkts) <= 30                      # 90 frames @30fps sampled to 10fps -> ~30 frames
    ids = [p.frame_id for p in pkts]
    assert ids == sorted(ids) and ids[0] == 1
    assert r.dropped == 0                              # blocking mode never drops
    assert pkts[0].width == 640 and pkts[0].timestamp.endswith("Z")


def test_unopenable_source_reconnects_without_crashing():
    r = CameraReader(CameraConfig(camera_id="CAM_BAD", source="/nonexistent/video.mp4"))
    r.start()
    time.sleep(1.5)
    assert r.state in ("reconnecting", "starting") and r.is_alive()
    r.stop()
    r.join(3)
    assert not r.is_alive()
