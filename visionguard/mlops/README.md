# M8 - MLOps (dataset integrity, model registry, promotion gates, feedback, drift)

Goal: no model reaches the factory floor unless it beat the bar on a **frozen, leak-free test set**, a human approved it,
and it can be rolled back in one command. Reviewer verdicts improve the system without ever auto-training it.

## Status: what is and isn't verified
- Verified: 31 unit/CLI tests pass (datasets, gates, registry, promotion, feedback, drift, CLI end-to-end). The sandbox had no
  pytest, so I ran them with a small custom runner; please run `pytest -q` yourself.
- NOT verified: `evaluate.py` (needs ultralytics + weights), `dvc.yaml` (paths assume the milestone0 script names and flags; check them
  against your copies), MLflow (not integrated - the SQLite registry is the MVP; swap later).

## Layout
- `vg_mlops/datasets.py` - sha256 manifests (image + label), per-split and whole-dataset hash, leakage check
  (same image in train and val/test; `synth_*` outside train).
- `vg_mlops/registry.py` - SQLite registry, stages candidate -> staging -> production -> archived (no skipping, archived is terminal),
  weights copied and sha256-pinned, frozen test hash, audit events, atomic serving pointer, `load_pointer()` integrity check.
- `vg_mlops/gates.py` - pure-function gates: frozen test set, per-class targets (milestone0 `targets.yaml` format), min test instances,
  no-regression vs production (tighter for critical classes), latency budget.
- `vg_mlops/promote.py` - policy: staging automatic when gates pass; production re-runs gates + named approver + staging soak
  (default 24h). `--force --reason` can waive the soak only (audited); it can never waive failed metric gates.
- `vg_mlops/feedback.py` - per-rule precision from M3 labels; threshold suggestions; work queues.
- `vg_mlops/drift.py` - PSI on confidence histograms and detections/frame change; needs >= 200 samples per class.
- `vg_mlops/evaluate.py` - ultralytics val on the test split -> registry metrics JSON.

## Workflow
```
pip install -r requirements.txt
python -m vg_mlops audit-data --data-root ../milestone0/data/processed/ppe        # exit 2 on leakage
python -m vg_mlops freeze-test --name ppe --data-root ... --actor you             # once; rebasing needs a reason
python -c "from vg_mlops.evaluate import metrics_from_ultralytics as m, json; ..." # produce metrics.json (see evaluate.py)
python -m vg_mlops register --name ppe --weights best.pt --metrics metrics.json --data-root ...
python -m vg_mlops stage   --name ppe --version 1 --group ppe --targets ../milestone0/configs/targets.yaml
python -m vg_mlops promote --name ppe --version 1 --group ppe --approver you      # after soak
python -m vg_mlops rollback --name ppe --actor you --reason "..."
python -m vg_mlops feedback --labels labels.json                                  # writes relabel / hard_negatives / confirmed
python -m vg_mlops drift --baseline base.json --current cur.json                  # exit 3 if attention needed
```
Hazard (`fire`/`smoke`) is a separate group: use `--group hazard` and register it under its own `--name`.

## Design rules worth knowing
1. **Frozen test set.** Candidates evaluated on a different test hash are not comparable and fail the gate. Changing the test set
   (`rebase_test`) needs a reason and invalidates in-flight candidates.
2. **PPE false positives are never auto-trained on.** They may be a missed detection or a rule bug, so they go to a human relabel queue.
   Only hazard false positives (steam, welding glare) become hard negatives.
3. **Confirmed incidents are not auto-added to the test set** - annotate first, and rebase deliberately.
4. **Serving integrity.** M1 should call `load_pointer(registry/serving/<name>.json)`; on failure keep the current model.
   (M1 does not do this yet - a small follow-up wiring change.)
5. Only classes whose rules are enabled should be in `required_classes` (see `configs/gate.example.yaml`); deferred classes would
   otherwise block every promotion.

## Licensing
Ultralytics YOLO is AGPL-3.0. Fine for internal evaluation; get legal/commercial-license review before shipping it inside a product.

## Known limits
Single-writer SQLite; no MLflow/DVC remote; drift needs you to export baseline/current stats from M1 (no exporter yet);
thresholds in `GateConfig` are sensible defaults, not validated against your data.
