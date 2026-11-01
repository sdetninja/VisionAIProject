import time

from fastapi.testclient import TestClient

from vision_pipeline.app import create_app
from vision_pipeline.config import CameraConfig, PipelineConfig
from vision_pipeline.pipeline import VisionPipeline


def test_api_endpoints(synthetic_video, fake_detector, tmp_path):
    cfg = PipelineConfig(cameras=[CameraConfig(camera_id="CAM_T", source=synthetic_video,
                                               target_fps=10, realtime=False, loop=True)])
    with TestClient(create_app(VisionPipeline(cfg, detector=fake_detector))) as c:
        for _ in range(50):
            if c.get("/cameras/CAM_T/results").json():
                break
            time.sleep(0.1)
        assert c.get("/health").status_code == 200
        res = c.get("/cameras/CAM_T/results").json()
        assert res and res[0]["camera_id"] == "CAM_T"
        latest = c.get("/cameras/CAM_T/latest").json()
        fr = c.get(f"/cameras/CAM_T/frames/{latest['frame_id']}")
        assert fr.status_code == 200 and fr.headers["content-type"] == "image/jpeg"
        assert c.get("/cameras/NOPE/latest").status_code == 404
        assert c.get("/cameras/CAM_T/frames/999999").status_code == 404
        assert b"vg_frames_processed_total" in c.get("/metrics").content
