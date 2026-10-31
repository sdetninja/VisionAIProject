"""Download a Roboflow Universe dataset in YOLO format.

Needs a free API key: export ROBOFLOW_API_KEY=...
Find workspace/project/version in the dataset URL: universe.roboflow.com/<workspace>/<project>/dataset/<version>

  python scripts/02_download_roboflow.py --workspace WS --project PROJ --version 1 --out data/raw/roboflow_ppe_1

Always check the license shown on the dataset page before using it, and keep a record in
data/SOURCES.md (name, URL, license, date, version).
Other sources are manual downloads (see MILESTONE0_PLAYBOOK.md): SH17, Construction-PPE, D-Fire.
"""
import argparse
import os
import shutil
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--version", type=int, required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    from roboflow import Roboflow
    key = os.environ.get("ROBOFLOW_API_KEY")
    if not key:
        raise SystemExit("Set ROBOFLOW_API_KEY")
    ds = Roboflow(api_key=key).workspace(a.workspace).project(a.project).version(a.version).download("yolov8")
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        shutil.rmtree(out)
    shutil.move(ds.location, out)
    print("Downloaded to", out)


if __name__ == "__main__":
    main()
