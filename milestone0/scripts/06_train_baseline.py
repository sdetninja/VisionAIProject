"""Train a baseline YOLO model (Mac-friendly defaults: yolo11n, MPS if available).

  python scripts/06_train_baseline.py --data data/processed/ppe/data.yaml --name ppe-yolo-v0 --epochs 50
  # smoke test first (2 minutes): --epochs 1 --fraction 0.05

For bigger runs use Google Colab (T4 GPU): upload data/processed/<x> as a zip, same command with --device 0.
Experiments are logged to MLflow if MLFLOW_TRACKING_URI is set (e.g. `mlflow ui` then http://127.0.0.1:5000).
"""
import argparse
import os

import torch


def pick_device():
    if torch.cuda.is_available():
        return 0
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--model", default="yolo11n.pt")
    ap.add_argument("--epochs", type=int, default=50)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--fraction", type=float, default=1.0)
    ap.add_argument("--device", default=None)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()

    from ultralytics import YOLO, settings
    if os.environ.get("MLFLOW_TRACKING_URI"):
        settings.update({"mlflow": True})
        os.environ.setdefault("MLFLOW_EXPERIMENT_NAME", "visionguard-baseline")

    device = a.device if a.device is not None else pick_device()
    print("Device:", device)
    model = YOLO(a.model)
    model.train(
        data=a.data, epochs=a.epochs, imgsz=a.imgsz, batch=a.batch, device=device,
        project="runs", name=a.name, fraction=a.fraction, workers=a.workers,
        patience=15, seed=42, deterministic=True, plots=True,
        # augmentations suited to CCTV: lighting/colour jitter, mosaic, mild scale, NO vertical flip
        hsv_h=0.015, hsv_s=0.6, hsv_v=0.4, degrees=5, translate=0.1, scale=0.5,
        fliplr=0.5, flipud=0.0, mosaic=1.0, close_mosaic=10,
    )
    print(f"Best weights: runs/{a.name}/weights/best.pt")


if __name__ == "__main__":
    main()
