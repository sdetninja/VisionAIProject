"""Capture training frames from a webcam / RTSP / video file.

Skips blurry and near-duplicate frames. Optionally pre-labels `person` using a pretrained
COCO model so you only have to correct boxes and add PPE classes in CVAT.

Examples:
  python scripts/01_capture_frames.py --source 0 --out data/raw/own_camera_unlabeled --every-sec 1.5 --max 400
  python scripts/01_capture_frames.py --source clip.mp4 --out data/raw/own_camera_unlabeled --prelabel
Keys (webcam preview): q = quit, space = force-save current frame.
"""
import argparse
import time
from pathlib import Path

import cv2
import numpy as np


def sharpness(img):
    return cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()


def signature(img):
    return cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (32, 18)).astype(np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="0", help="webcam index, RTSP url, or video path")
    ap.add_argument("--out", required=True)
    ap.add_argument("--every-sec", type=float, default=1.0)
    ap.add_argument("--max", type=int, default=300)
    ap.add_argument("--min-sharpness", type=float, default=60.0)
    ap.add_argument("--min-diff", type=float, default=8.0, help="mean abs diff vs last saved (near-dup filter)")
    ap.add_argument("--prelabel", action="store_true", help="write YOLO .txt with class 0=person")
    ap.add_argument("--prelabel-model", default="yolo11n.pt")
    ap.add_argument("--no-preview", action="store_true")
    a = ap.parse_args()

    out = Path(a.out)
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "labels").mkdir(parents=True, exist_ok=True)
    src = int(a.source) if a.source.isdigit() else a.source
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        raise SystemExit(f"Cannot open source {a.source}")

    model = None
    if a.prelabel:
        from ultralytics import YOLO
        model = YOLO(a.prelabel_model)

    is_file = not isinstance(src, int) and not str(src).startswith("rtsp")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    step = max(1, int(fps * a.every_sec)) if is_file else None
    prefix = f"{int(time.time())}"
    saved, idx, last_sig, last_t = 0, 0, None, 0.0

    while saved < a.max:
        ok, frame = cap.read()
        if not ok:
            break
        idx += 1
        now = time.time()
        due = (idx % step == 0) if is_file else (now - last_t >= a.every_sec)
        key = -1
        if not a.no_preview:
            cv2.imshow("capture (q quit, space save)", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
        force = key == ord(" ")
        if not (due or force):
            continue
        last_t = now
        if not force:
            if sharpness(frame) < a.min_sharpness:
                continue
            sig = signature(frame)
            if last_sig is not None and np.abs(sig - last_sig).mean() < a.min_diff:
                continue
        last_sig = signature(frame)
        name = f"{prefix}_{saved:05d}"
        cv2.imwrite(str(out / "images" / f"{name}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if model is not None:
            h, w = frame.shape[:2]
            lines = []
            for b in model.predict(frame, classes=[0], conf=0.35, verbose=False)[0].boxes:
                x1, y1, x2, y2 = b.xyxy[0].tolist()
                lines.append(f"0 {(x1+x2)/2/w:.6f} {(y1+y2)/2/h:.6f} {(x2-x1)/w:.6f} {(y2-y1)/h:.6f}")
            (out / "labels" / f"{name}.txt").write_text("\n".join(lines))
        saved += 1
        print(f"saved {saved}/{a.max}", end="\r")

    cap.release()
    cv2.destroyAllWindows()
    print(f"\nDone: {saved} frames in {out/'images'}")


if __name__ == "__main__":
    main()
