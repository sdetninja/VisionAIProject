"""Dataset integrity: content hashes, frozen-test manifest, leakage checks."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

IMG = {".jpg", ".jpeg", ".png"}


def sha256_file(p, chunk=1 << 20) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while b := f.read(chunk):
            h.update(b)
    return h.hexdigest()


def split_files(root, split):
    d = Path(root) / "images" / split
    return sorted(p for p in d.glob("*") if p.suffix.lower() in IMG) if d.exists() else []


def split_manifest(root, split) -> dict:
    """{filename: {'img': sha256, 'lbl': sha256|None}} - labels are part of identity: relabelling changes the hash."""
    out = {}
    for p in split_files(root, split):
        lp = Path(root) / "labels" / split / f"{p.stem}.txt"
        out[p.name] = {"img": sha256_file(p), "lbl": sha256_file(lp) if lp.exists() else None}
    return out


def manifest_hash(m: dict) -> str:
    return hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest()


def dataset_summary(root) -> dict:
    ms = {s: split_manifest(root, s) for s in ("train", "val", "test")}
    return {"hashes": {s: manifest_hash(m) for s, m in ms.items()}, "counts": {s: len(m) for s, m in ms.items()},
            "dataset_hash": manifest_hash({s: manifest_hash(m) for s, m in ms.items()}), "manifests": ms}


def leakage_report(ms: dict) -> dict:
    """Same image content in train and val/test = inflated metrics. Also flags synthetic images outside train."""
    seen = {}
    for s in ("train", "val", "test"):
        for name, h in ms.get(s, {}).items():
            seen.setdefault(h["img"], []).append((s, name))
    leaks = [v for v in seen.values() if len({s for s, _ in v}) > 1]
    synth = [(s, n) for s in ("val", "test") for n in ms.get(s, {}) if n.startswith(("synth_", "synthetic"))]
    return {"cross_split_duplicates": leaks, "synthetic_outside_train": synth, "ok": not leaks and not synth}
