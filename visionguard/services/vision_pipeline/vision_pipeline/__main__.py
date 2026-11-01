"""CLI: run the pipeline headless / with an OpenCV window, no HTTP server needed.

  python -m vision_pipeline --config configs/pipeline.webcam.yaml --show
  python -m vision_pipeline --config configs/pipeline.video.yaml --print
"""
import argparse
import logging
import signal
import threading

import cv2

from .annotate import annotate
from .config import load_config
from .pipeline import VisionPipeline
from .sinks import CallbackSink


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--show", action="store_true", help="show annotated window (q to quit)")
    ap.add_argument("--print", action="store_true", help="print one summary line per frame")
    ap.add_argument("--log-level", default="INFO")
    a = ap.parse_args()
    logging.basicConfig(level=a.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    p = VisionPipeline(load_config(a.config))
    if a.print:
        p.add_sink(CallbackSink(lambda r: print(
            f"{r.camera_id} f={r.frame_id} tracks={[t.track_id for t in r.tracks]} "
            f"ppe={[d.class_name for d in r.ppe_detections]} {r.inference_ms}ms")))
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    p.start()
    try:
        while not stop.is_set():
            if a.show:
                for cid, w in p.workers.items():
                    snap = w.snapshot()
                    if snap:
                        cv2.imshow(cid, annotate(snap[0], snap[1], w.fps))
                if cv2.waitKey(30) & 0xFF == ord("q"):
                    break
            else:
                stop.wait(0.5)
            if not any(w.is_alive() for w in p.workers.values()):
                break
    finally:
        p.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
