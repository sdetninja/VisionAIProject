# Combined PPE + fire/smoke model (YOLO11n, 10 classes)

**Use:** `runs/detect/runs/combined-yolo-v2/weights/best.pt` (5.5 MB, repo root), alarm thresholds fire 0.35 / smoke 0.35.
Research / internal demo only (SH17 is CC BY-NC-SA; Ultralytics is AGPL-3.0).

## Data (`data/processed/combined`, built by `scripts/08_build_combined.py --config configs/sources_combined.yaml`)
Images: train 8,032 / val 993 / test 1,191. Every source keeps its own splits, so test never shares images with training.
Sources: construction-ppe (all), SH17 (2,600 train, PPE-rich sample, shuffled class names fixed by `relabel`),
hf_ppe ds1 (1,900 train), D-Fire (2,400 train, 15% fire/smoke-free). Missing labels are filled by teacher models
(person from yolo11l; vest/gloves from ppe-yolo-v0) and derived (`no_helmet`/`no_gloves` from SH17 heads/hands).
Per-class counts and label provenance: `combined_summary.md`.

## Training (Apple M4, MPS, imgsz 640, batch 16)
| run | how | epochs | real compute |
|---|---|---|---|
| combined-yolo-v1 | from `yolo11n.pt` (COCO), `--time 4.3` | 9 | ~75 min (Mac slept twice: lid closed on battery) |
| combined-yolo-v2 | from v1 `best.pt`, `--epochs 18 --close-mosaic 5` | 18 | 2.5 h (~8.5 min/epoch) |

v2 val mAP50 rose every few epochs to the last one (0.689 at epoch 18), so more epochs would still help.

## Test split (held out, 1,191 images)
| class | v1 mAP50 | **v2 mAP50** | v2 mAP50-95 | v2 precision | v2 recall |
|---|---|---|---|---|---|
| **all** | 0.636 | **0.732** | 0.434 | 0.787 | 0.668 |
| person | 0.782 | 0.820 | 0.594 | 0.786 | 0.848 |
| helmet | 0.850 | 0.930 | 0.560 | 0.878 | 0.865 |
| no_helmet | 0.821 | 0.878 | 0.592 | 0.886 | 0.819 |
| vest | 0.765 | 0.810 | 0.501 | 0.813 | 0.720 |
| gloves | 0.504 | 0.588 | 0.313 | 0.763 | 0.526 |
| no_gloves | 0.567 | 0.691 | 0.399 | 0.805 | 0.607 |
| goggles | 0.405 | 0.522 | 0.195 | 0.655 | 0.503 |
| mask | 0.602 | 0.831 | 0.521 | 0.865 | 0.756 |
| fire | 0.514 | 0.573 | 0.290 | 0.665 | 0.490 |
| smoke | 0.551 | 0.675 | 0.378 | 0.754 | 0.549 |

Mask has only 34 test boxes: its number moves a lot between runs. Speed: ~1.6 ms inference per image on MPS.

## Alarm quality (frame level, `hazard_thresholds_combined-yolo-v2.md`)
Thresholds chosen on val (lowest with per-frame false alarms <= 1% fire, <= 2% smoke), reported on test:

| class | threshold | frames caught | with 3-of-5 confirm* | false alarms per frame |
|---|---|---|---|---|
| fire | 0.35 | 83% (v1 at same budget: 79%) | 96% | 0.4% |
| smoke | 0.35 | 68% (v1: 51%) | 81% | 1.7% |

\* assumes independent frames; real video is correlated, so this is optimistic for false alarms from a persistent
lookalike (fog, steam, sunset). Hazard events stay flagged for human review.

## Error analysis (v2, 2026-11-09)
**Per source (test mAP50):** construction_ppe 0.625, sh17 0.639, hf_ppe 0.716, d_fire 0.634.

| source | weak class | mAP50 | cause (checked on rendered images) |
|---|---|---|---|
| construction_ppe | no_helmet / no_gloves | 0.16 / 0.07 | **Box convention clash**: construction-ppe boxes only the top of the head (where a helmet sits); hf_ppe/SH17 (~90% of no_helmet labels) box the whole head. The model finds the person but its whole-head box fails IoU 0.5. Not a deployment miss: the rule engine's helmet region covers both. Fix if needed: drop or re-box construction-ppe `no_helmet`. |
| sh17 | vest | 0.19 | Tiny distant workers (street/scaffold shots); close-up vests are found. Small-object problem: imgsz 800 / yolo11s. |
| hf_ppe | gloves | 0.23 | Test labels are teacher pseudo-labels (110 boxes); the number is unreliable, not the model. |
| d_fire | person | 0.65 | Same: person labels are pseudo-labels from yolo11l. |

**Confusion matrix** (test, conf 0.25; `runs/detect/runs/combined-yolo-v2-test/confusion_matrix.png`): classes are almost never
confused with each other (helmet<->no_helmet 1%, gloves<->no_gloves 2%, vest->person 4%). Errors are **misses**
(gloves 40%, goggles 44%, fire 43%, smoke 37%, no_gloves 34%) and extra boxes on background; many of the 466 "false" person
boxes are real people that the pseudo-labelling (teacher conf >= 0.5) did not label.

**Rule-engine thresholds** (val precision / recall per frame):

| class | 0.30 | 0.40 | 0.50 | rule use |
|---|---|---|---|---|
| person | 85 / 87 | 90 / 85 | 93 / 81 | `min_person_conf` 0.50: keep |
| helmet | 87 / 89 | 92 / 87 | 95 / 84 | `pos_conf` 0.40: keep |
| no_helmet | 86 / 77 | 92 / 75 | 94 / 72 | `neg_conf` 0.40: keep |
| vest | 76 / 72 | 84 / 67 | 90 / 60 | `pos_conf` 0.40: keep (vest absence is inferred, 8-of-10 confirm) |
| no_gloves | 76 / 57 | 84 / 51 | 89 / 46 | **`neg_conf` 0.50 for R-GLOVES** (explicit-only rule, precision first) |
| goggles | 62 / 55 | 75 / 49 | 86 / 36 | no goggles rule yet; use >= 0.50 if one is added |

Rule-engine fix (2026-11-09): vest absence was inferred for head-and-shoulders shots (hf_ppe close-ups) where the torso
is cut off by the frame bottom. The engine now treats a vest as UNKNOWN when the person box touches the bottom edge
(`BOTTOM_EDGE_ITEMS`, mirroring `TOP_EDGE_ITEMS` for helmets); explicit `no_vest` evidence still counts. On the demo clip
this removed 3 false vest events; full-body workers without vests are still reported.

## Known weaknesses / next steps (in order of expected gain)
1. Gloves, goggles: small objects at 640 px. Try `imgsz 800` or yolo11s (about 1.6-2x compute; a CUDA GPU is 3-5x faster than MPS here).
2. Smoke: misses thin/distant smoke and fires on fog/haze. Use more of D-Fire (2,400 of 14,122 train images used) and
   add site-specific hard negatives from reviewed false alarms (M8 feedback queue).
3. Fire box mAP is low partly because D-Fire draws many small boxes per fire; frame-level recall above is the alarm metric.
4. Validate on footage from the real cameras before trusting any of these numbers on site.
