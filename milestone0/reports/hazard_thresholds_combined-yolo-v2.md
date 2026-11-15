# Hazard thresholds: combined-yolo-v2

Weights: `/Users/raj/Desktop/VisionAIProject/runs/detect/runs/combined-yolo-v2/weights/best.pt`. Debounce: 3 of 5 frames. Chosen on val, reported on test. N-of-M columns assume independent frames (see script docstring).

## fire: threshold **0.35** (val false-alarm budget 1.0%)

Test: 163 frames with fire, 1028 without.

| conf | val recall | val false alarm | test recall | test false alarm | test alarm P(3/5) | test false alarm P(3/5) |
|---|---|---|---|---|---|---|
| 0.05 | 98% | 2.7% | 98% | 2.4% | 100% | 0.01% |
| 0.10 | 97% | 1.7% | 96% | 2.1% | 100% | 0.01% |
| 0.15 | 95% | 1.5% | 94% | 1.7% | 100% | 0.00% |
| 0.20 | 91% | 1.3% | 93% | 1.3% | 100% | 0.00% |
| 0.25 | 89% | 1.2% | 91% | 1.1% | 99% | 0.00% |
| 0.30 | 85% | 1.0% | 88% | 0.8% | 98% | 0.00% |
| 0.35 ← | 83% | 0.8% | 83% | 0.4% | 96% | 0.00% |
| 0.40 | 75% | 0.7% | 78% | 0.2% | 92% | 0.00% |
| 0.45 | 70% | 0.2% | 73% | 0.2% | 87% | 0.00% |
| 0.50 | 62% | 0.2% | 68% | 0.1% | 81% | 0.00% |
| 0.55 | 59% | 0.2% | 60% | 0.0% | 68% | 0.00% |
| 0.60 | 47% | 0.1% | 49% | 0.0% | 48% | 0.00% |

## smoke: threshold **0.35** (val false-alarm budget 2.0%)

Test: 307 frames with smoke, 884 without.

| conf | val recall | val false alarm | test recall | test false alarm | test alarm P(3/5) | test false alarm P(3/5) |
|---|---|---|---|---|---|---|
| 0.05 | 97% | 5.8% | 95% | 6.8% | 100% | 0.28% |
| 0.10 | 94% | 4.3% | 93% | 4.8% | 100% | 0.10% |
| 0.15 | 89% | 3.2% | 86% | 4.1% | 98% | 0.06% |
| 0.20 | 87% | 3.1% | 81% | 3.5% | 95% | 0.04% |
| 0.25 | 84% | 2.6% | 79% | 2.9% | 93% | 0.02% |
| 0.30 | 81% | 2.1% | 74% | 2.1% | 88% | 0.01% |
| 0.35 ← | 77% | 1.7% | 68% | 1.7% | 81% | 0.00% |
| 0.40 | 72% | 1.2% | 61% | 1.4% | 70% | 0.00% |
| 0.45 | 65% | 0.8% | 55% | 0.9% | 60% | 0.00% |
| 0.50 | 61% | 0.8% | 52% | 0.7% | 53% | 0.00% |
| 0.55 | 56% | 0.5% | 47% | 0.6% | 44% | 0.00% |
| 0.60 | 47% | 0.4% | 43% | 0.5% | 37% | 0.00% |

## Config

```yaml
rule_engine (R-FLOOR-HAZARD):
  class_conf:
    fire: 0.35
    smoke: 0.35
vision_pipeline (model):
  class_conf:
    fire: 0.35
    smoke: 0.35
```
