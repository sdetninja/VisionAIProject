# Hazard thresholds: combined-yolo-v1

Weights: `/Users/raj/Desktop/VisionAIProject/runs/detect/runs/combined-yolo-v1/weights/best.pt`. Debounce: 3 of 5 frames. Chosen on val, reported on test. N-of-M columns assume independent frames (see script docstring).

## fire: threshold **0.25** (val false-alarm budget 1.0%)

Test: 163 frames with fire, 1028 without.

| conf | val recall | val false alarm | test recall | test false alarm | test alarm P(3/5) | test false alarm P(3/5) |
|---|---|---|---|---|---|---|
| 0.05 | 95% | 3.7% | 94% | 3.2% | 100% | 0.03% |
| 0.10 | 93% | 2.6% | 93% | 1.4% | 100% | 0.00% |
| 0.15 | 88% | 1.7% | 91% | 0.4% | 99% | 0.00% |
| 0.20 | 86% | 1.3% | 83% | 0.2% | 96% | 0.00% |
| 0.25 ← | 74% | 0.8% | 79% | 0.2% | 93% | 0.00% |
| 0.30 | 68% | 0.5% | 69% | 0.2% | 83% | 0.00% |
| 0.35 | 59% | 0.3% | 58% | 0.2% | 65% | 0.00% |
| 0.40 | 51% | 0.1% | 53% | 0.2% | 55% | 0.00% |
| 0.45 | 44% | 0.1% | 43% | 0.1% | 37% | 0.00% |
| 0.50 | 38% | 0.0% | 34% | 0.0% | 23% | 0.00% |
| 0.55 | 20% | 0.0% | 28% | 0.0% | 14% | 0.00% |
| 0.60 | 14% | 0.0% | 18% | 0.0% | 4% | 0.00% |

## smoke: threshold **0.35** (val false-alarm budget 2.0%)

Test: 307 frames with smoke, 884 without.

| conf | val recall | val false alarm | test recall | test false alarm | test alarm P(3/5) | test false alarm P(3/5) |
|---|---|---|---|---|---|---|
| 0.05 | 96% | 6.0% | 93% | 6.1% | 100% | 0.21% |
| 0.10 | 91% | 4.3% | 86% | 4.5% | 98% | 0.09% |
| 0.15 | 86% | 3.5% | 79% | 4.0% | 94% | 0.06% |
| 0.20 | 80% | 3.0% | 73% | 3.4% | 87% | 0.04% |
| 0.25 | 74% | 2.5% | 65% | 2.9% | 76% | 0.02% |
| 0.30 | 68% | 2.3% | 58% | 2.6% | 65% | 0.02% |
| 0.35 ← | 63% | 1.7% | 51% | 1.7% | 53% | 0.00% |
| 0.40 | 58% | 1.3% | 47% | 1.5% | 45% | 0.00% |
| 0.45 | 52% | 1.2% | 45% | 1.2% | 40% | 0.00% |
| 0.50 | 48% | 0.8% | 42% | 0.8% | 35% | 0.00% |
| 0.55 | 44% | 0.6% | 38% | 0.6% | 28% | 0.00% |
| 0.60 | 40% | 0.4% | 33% | 0.5% | 21% | 0.00% |

## Config

```yaml
rule_engine (R-FLOOR-HAZARD):
  class_conf: &id001
    fire: 0.25
    smoke: 0.35
vision_pipeline (model):
  class_conf: *id001
```
