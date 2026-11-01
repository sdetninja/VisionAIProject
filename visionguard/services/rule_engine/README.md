# M2 – Safety Rule Engine

Deterministic, configurable, auditable. Consumes M1 `TrackResult`s, emits confirmed `ViolationEvent`s for M3.
Pure Python (only `pyyaml` required; `fastapi` for the optional HTTP wrapper). Driven by frame timestamps, not wall-clock, so
replaying the same video gives byte-identical events.

```
TrackResult ─► zone test (foot-point) ─► PPE association ─► observation ─► state machine ─► ViolationEvent
                                           (per person)     PRESENT/ABSENT/UNKNOWN   N-of-M · clear · cooldown · dedup
```

## Run
```bash
pip install -r requirements.txt && pytest -q
python examples/make_demo_jsonl.py out/demo.jsonl                       # synthetic M1 output (no camera/model needed)
python -m rule_engine --config configs/rules.example.yaml --input out/demo.jsonl --output out/events.jsonl
# with real M1 output:  --input ../vision_pipeline/out/results/CAM_001.jsonl --follow
CONFIG_PATH=configs/rules.example.yaml uvicorn rule_engine.app:app --port 8002     # optional HTTP service
```
In-process with M1: `pipeline.add_sink(RuleEngineSink(engine, on_event=incident_service.ingest))`.

## How a decision is made
1. **Zone**: a person is in a zone if the **foot-point** (bottom-centre of the box) is inside the polygon (pixel or normalised; `reference_size` rescales a polygon drawn on another resolution). A zone with no polygon covers the frame; `camera_id: "*"` applies to all cameras.
2. **Association**: each PPE detection is assigned to **one** person whose expected body region contains its centre (`ppe_regions`, fractions of the person box; ties go to the closest to the region centre). Two overlapping workers cannot share one helmet.
3. **Observation** per person per frame:
   - `PRESENT`: item detected on the person.
   - `ABSENT`: explicit `no_<item>` detected, **or** (only for items with `inferred_absence: true`, default helmet/vest) the person is reliably visible and nothing was detected. Inferred evidence is capped at 0.65 confidence.
   - `UNKNOWN`: person too small, too young a track, low confidence, head cut off by the frame edge, or gloves/goggles/mask with no detection. Unknown observations are ignored: they neither confirm nor clear.
   - Both present and `no_` detected: `on_conflict` (default unknown).
4. **State machine** per (camera, track, zone, rule): confirm when ≥ N of the last M usable observations are violating; **one event per episode**; the episode closes after `clear_frames` consecutive compliant observations (or when the person leaves the zone); `cooldown_s` blocks quick re-reports; a spatial de-dup (IoU ≥ 0.3, same camera/zone/rule, within cooldown) stops a **tracker ID switch** from raising a second incident for the same person.
5. **Review flag**: `needs_review: true` when mean confidence < `review_below` or most evidence was inferred. This feeds the human-in-the-loop queue.

## Output (`ViolationEvent`, schema 1.0)
```json
{"schema_version":"1.0","event_id":"<uuid5 of idempotency_key>","idempotency_key":"CAM_001|CAM_001-T002|WELDING|R-WELD-HELMET|60",
 "event_type":"PPE_VIOLATION","rule_id":"R-WELD-HELMET","camera_id":"CAM_001","track_id":"CAM_001-T002","zone":"WELDING",
 "missing_ppe":["helmet"],"hazard_class":null,"confidence":0.9,"evidence_basis":{"explicit":8,"inferred":0},
 "needs_review":false,"review_reasons":[],"window":{"n":8,"m":10,"violating":8,"observed":8},
 "first_seen_frame":60,"confirmed_frame":67,"evidence_frames":[60,64,67],"bbox":[520,270,640,650],
 "first_seen_at":"2026-10-31T10:30:06.000Z","timestamp":"2026-10-31T10:30:06.700Z","model_version":"ppe-yolo-v0","config_version":1}
```
Event types: `PPE_VIOLATION`, `RESTRICTED_AREA_ENTRY`, `HAZARD_DETECTED` (`track_id` null). `evidence_frames` are M1 frame ids: M3 fetches the JPEGs from the frame buffer by `(camera_id, frame_id)`. `event_id` is deterministic, so M3 can de-duplicate on redelivery. One event is emitted per rule; M3 should group events sharing a `track_id`/`zone` within a few seconds into a single incident (e.g. helmet + gloves).

## Tuning notes
- Thresholds in `rules.example.yaml` are starting points. Tune `confirm`, `ppe_regions`, and confidence limits against Milestone 0 recall/precision and recorded footage; don't tune on the test set.
- Hazard rules read `hazard_detections` (from the separate fire/smoke model) and use their own thresholds/cooldowns.
- Config errors (unknown type, n > m, bad polygon, duplicate ids) fail at load time. `PUT /rules` validates, hot-reloads, writes the file, and logs an `AUDIT` line; put real auth/RBAC in front of it (planned for the backend API).

## Known limits
- State is in-memory: restarting the service forgets open episodes and cooldowns (Redis-backed state is the planned upgrade for multi-replica use).
- A person fully hidden behind another is not detected as occluded; the spatial association is geometric only.
- De-dup can merge two different people who stand in nearly the same place inside one cooldown window.
