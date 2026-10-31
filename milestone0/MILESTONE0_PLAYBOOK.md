# Milestone 0 – Dataset & Baseline Playbook

Goal: before any service is built, prove that a YOLO model can detect the classes the rule engine depends on, on data that resembles **your** cameras. Output is a go/no-go table per class.

**Exit criteria**
1. Versioned dataset (DVC) with a **frozen test split** that is never trained on.
2. `reports/baseline_ppe.md` and `reports/baseline_hazard.md` with per-class precision/recall/mAP versus the targets in `configs/targets.yaml`.
3. A written decision per class: **GO** (rule enabled), **DEFER** (rule disabled until more data), or **REWORK** (higher image size, crop stage, or better camera placement).

---

## 1. Folder layout

```
milestone0/
├── configs/        sources_ppe.yaml · sources_hazard.yaml · targets.yaml
├── scripts/        01_capture_frames.py … 07_evaluate_baseline.py
├── data/           raw/ · processed/ · synthetic/ · backgrounds/   (DVC-tracked, not in git)
├── runs/           training output (ultralytics)
└── reports/        audit + baseline reports (commit these)
```

Setup (Mac):

```bash
cd milestone0
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
git init && dvc init                      # data/ goes into DVC, code into git
echo "data/\nruns/" >> .gitignore
```

---

## 2. Class taxonomy

| id | class | Why it exists |
|----|-------|---------------|
| 0 | person | Anchor for tracking and PPE association |
| 1 | helmet | Positive evidence of head protection |
| 2 | no_helmet | Explicit "bare head" is more reliable than inferring absence |
| 3 | vest | |
| 4 | no_vest | |
| 5 | gloves | Small object, hardest class |
| 6 | no_gloves | Bare hands |
| 7 | goggles | Small object |
| 8 | mask | |
| 9 | forklift | Heavy-machinery risk factor |
| 10 | truck | |

Fire and smoke are a **separate model** (`sources_hazard.yaml`): different data, different thresholds, and different cost of a miss.

The rule engine will use both signals: `no_helmet` (explicit) and "person with no `helmet` over N of M frames" (inferred). Where the two disagree, the incident is marked for human review.

---

## 3. Data sources

Always check the license on each dataset page, and record name, URL, license, version and download date in `data/SOURCES.md`. Source class names differ between datasets, so **open each dataset's `data.yaml` and fix the `map:` block in the config** before running the merge. I wrote the names in the configs from memory and they must be verified.

| Source | What it gives | How to get it |
|--------|---------------|---------------|
| **SH17** (17-class manufacturing PPE dataset) | person, helmet, vest, gloves, glasses, face masks, in industrial settings | Download from its GitHub/Kaggle page; unzip to `data/raw/sh17` (convert to YOLO format if it ships in another format) |
| **Ultralytics Construction-PPE** | helmet, vest, gloves, boots, goggles and explicit "no_" classes | Ultralytics docs → Datasets → Construction-PPE; unzip to `data/raw/construction_ppe` |
| **Roboflow Universe PPE sets** | helmet/no-helmet, vest/no-vest, mask; many variants | `export ROBOFLOW_API_KEY=...` then `python scripts/02_download_roboflow.py --workspace WS --project PROJ --version N --out data/raw/roboflow_ppe_1`. Pick 2-3 datasets with 2k+ images and clear licenses |
| **D-Fire** (fire/smoke) | ~20k images with fire and smoke boxes | `github.com/gaiasd/DFireDataset`; unzip to `data/raw/d_fire` |
| **Your own cameras** | The only data that tells you what accuracy you will really get | Section 4 |
| **Synthetic** | Fills rare classes | Section 5 |

Rules for merging public data:
- Public datasets are mostly construction sites and stock photos. Treat them as a way to pretrain; **do not judge success on them**.
- The merge script de-duplicates by file hash, but many Roboflow sets are copies of each other with different augmentations. Pick sources that don't overlap, and check a sample by eye.
- Beware label-semantics drift: some sets label only the *head* region as `person`/`head`; others label the full body. Only map classes that mean the same thing.

---

## 4. Collecting and labeling your own data (the most valuable step)

### 4.1 Capture
Collect at least **300-500 frames** for the first pass, ideally 1,000+.

```bash
# Webcam, 1 frame every 1.5s, skips blurry/near-duplicate frames, pre-labels persons for you
python scripts/01_capture_frames.py --source 0 --out data/raw/own_camera_unlabeled --every-sec 1.5 --max 400 --prelabel
# From a recorded clip or RTSP
python scripts/01_capture_frames.py --source clip.mp4 --out data/raw/own_camera_unlabeled --prelabel
```

Cover the variation the production system will see:
- distances (near, mid, far), angles (overhead, side), lighting (bright, dim, backlit), occlusion (behind a table or machine)
- people **with and without** each PPE item (use real helmets and gloves; act out violations deliberately)
- several people per frame, people walking, bending, and carrying things
- empty scenes (these are your **backgrounds** for Section 5 and your negatives)
- for hazards: steam, welding arcs, orange vests, bright lamps, dust, which are all false-positive traps. Use `data/raw/own_hazard_negatives/`.

Privacy: capture only with consent, inform people, and plan for face blurring and a retention limit even for training data.

### 4.2 Label with CVAT (or Label Studio)
1. Install CVAT (Docker) or use cvat.ai. Create a task with the 11 classes above.
2. Import the pre-labeled `person` boxes ("YOLO 1.1" format) and correct them.
3. Add PPE boxes. **Use the labeling guide below**, and have a second person review 10% of the frames.
4. Export as **"YOLO 1.1"** into `data/raw/own_camera/` (needs `data.yaml` with class names, plus `images/` and `labels/`).

Labeling guide (write this into your CVAT task description):
- `person`: tight box around the visible body. Label people who are at least ~40px tall; skip fragments cut off at the frame edge by more than 70%.
- `helmet` / `no_helmet`: box only the head region. A helmet carried in the hand or sitting on a table is **not** `helmet` (leave it unlabeled).
- `gloves` / `no_gloves`: box each hand individually. If a hand is not visible, don't label it. This is how the system later separates "missing" from "not visible".
- `vest` / `no_vest`: box the torso region of the person.
- Occluded items: label if more than ~50% is visible.
- Be consistent. A model trained on inconsistent labels learns noise, and the per-class ceiling is set by label quality.

Time estimate: roughly 1-2 minutes per frame for 3-4 people. 500 frames is about 10-15 hours, which is why pre-labeling and a smaller first pass help. An auto-annotation loop helps later: train the baseline, predict on new frames, correct the predictions.

### 4.3 Why the split matters
`own_camera` is configured `split: test_heavy` in `sources_ppe.yaml`: 50% goes to test, 30% to val and 20% to train. Your metrics therefore measure performance on your own domain, not on easy public images. Once the test split exists it is **frozen**. Never add test images to training. If you re-run the merge after adding data, check that the split stays consistent (the seed keeps it deterministic only for the same inputs; for long-term freezing, commit `data.yaml` and a `test_manifest.txt` listing test file names).

---

## 5. Synthetic and augmented data

Synthetic data supplements real data. It must be **train-only**.

| Technique | Where | Notes |
|-----------|-------|-------|
| On-the-fly augmentation | `06_train_baseline.py` (ultralytics: mosaic, HSV jitter, scale, flip) | Free and always on. No vertical flip for people |
| **Person-level copy-paste** | `scripts/04_synthetic_copy_paste.py` | Crops whole people with their PPE labels and pastes them on empty factory backgrounds. Over-samples crops that contain rare classes (gloves, no_gloves, goggles, no_helmet) |
| Camera degradation | same script | Blur and JPEG quality 60-95 to mimic CCTV compression |
| Generative images (diffusion) | optional, manual | Useful for fire/smoke or rare PPE scenes. Label-check by eye, keep train-only, cap at ~20% of training data |
| Simulation (Unity/Unreal/Isaac Sim) | optional, later | Gives exact labels and controllable lighting, but needs a domain-gap check |

```bash
# Extract backgrounds: frames from your cameras with nobody in them, copied to data/backgrounds/
python scripts/04_synthetic_copy_paste.py --data data/processed/ppe/data.yaml \
    --backgrounds data/backgrounds --out data/synthetic/ppe --n 600 \
    --rare gloves no_gloves goggles no_helmet no_vest
# Then re-run the merge so that "synthetic_copy_paste" is included (train_only), and audit again.
```

Look at 30 generated images before training. If gloves float in odd places or boxes don't line up, reduce the count or fix the source labels. Check one more thing: when you evaluate, synthetic images must never appear in val/test. The `train_only` flag guarantees this.

---

## 6. Run order

```bash
# 1. merge public + own + synthetic into one YOLO dataset
python scripts/03_prepare_dataset.py --config configs/sources_ppe.yaml

# 2. audit: class balance, small-object share, warnings
python scripts/05_audit_dataset.py --data data/processed/ppe/data.yaml --out reports/audit_ppe.md

# 3. smoke test the training loop (a couple of minutes)
python scripts/06_train_baseline.py --data data/processed/ppe/data.yaml --name smoke --epochs 1 --fraction 0.05

# 4. real baseline (Mac: expect hours on yolo11n; for speed use Colab T4, same command with --device 0)
mlflow ui &                                   # optional: export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
python scripts/06_train_baseline.py --data data/processed/ppe/data.yaml --name ppe-yolo-v0 --epochs 50 --imgsz 640

# 5. evaluate on the frozen test split vs targets
python scripts/07_evaluate_baseline.py --weights runs/ppe-yolo-v0/weights/best.pt \
    --data data/processed/ppe/data.yaml --group ppe

# 6. repeat 1-5 for the hazard model
python scripts/03_prepare_dataset.py --config configs/sources_hazard.yaml
python scripts/06_train_baseline.py --data data/processed/hazard/data.yaml --name hazard-yolo-v0
python scripts/07_evaluate_baseline.py --weights runs/hazard-yolo-v0/weights/best.pt --data data/processed/hazard/data.yaml --group hazard

# 7. version everything
dvc add data/raw data/processed data/synthetic
git add . && git commit -m "Milestone 0: dataset v1 + baseline v0"
```

Compute notes for a Mac: use `yolo11n.pt` (or `yolo11s.pt`), `imgsz 640`, batch 8-16. `device=mps` is picked automatically. If training is slow or hits MPS issues, set `PYTORCH_ENABLE_MPS_FALLBACK=1` or use `--device cpu` for the smoke test and Colab for the full run. Small objects (gloves, goggles) often need `imgsz 960` or more; compare in a second run.

---

## 7. Interpreting results

| Finding | Action |
|---------|--------|
| Person, helmet, vest pass | GO. Proceed to Milestone 1 |
| Gloves/goggles recall low, mostly small boxes | Try `imgsz 960/1280`; add a second-stage detector on person crops; move or zoom the camera; or **DEFER** these rules and start with helmet/vest/zone rules |
| `no_*` classes noisy | Rely on inferred absence (persistence over N of M frames) and mark as lower confidence |
| Great on public test, poor on own-camera test | Domain shift. Label more own-camera data, over-sample it, fine-tune from the baseline |
| Fire false alarms (welding arcs, lamps) | Add hard negatives to `own_hazard_negatives`, raise the confidence threshold, require persistence |
| Class has fewer than 30 test instances | Metrics are unreliable; label more data before deciding |

Decisions are recorded in `reports/DECISIONS.md` (class, metric, decision, date, reason). If a target changes, note why.

---

## 8. Checklist

- [ ] `data/SOURCES.md` with licenses
- [ ] Verified every `map:` against each source's own class list
- [ ] 300+ own-camera frames labeled, 10% double-reviewed
- [ ] Frozen test split committed (`data.yaml` + test file list)
- [ ] Audit report with no unresolved critical warnings
- [ ] Baseline trained, tracked in MLflow, weights in DVC
- [ ] `baseline_ppe.md` and `baseline_hazard.md` produced
- [ ] Go / defer / rework decision per class
- [ ] Measured inference time (ms/frame) of the baseline on your target hardware, which sets the camera-count budget for Milestone 1
