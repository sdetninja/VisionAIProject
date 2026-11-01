# M3 – Incident Service

Turns M2 `ViolationEvent`s into durable, reviewable **incidents** with evidence images, and publishes them reliably.

```
M2 events ──► ingest (idempotent) ─► group ─► evidence (M1 frames → annotate → store)
                         └── one DB transaction: incident + event + outbox row ──► OutboxPublisher ─► bus
Reviewer ──► POST /incidents/{id}/review ──► status history + labels (feedback for M8) + outbox
```

## Behaviour
- **Idempotent**: same `idempotency_key` → `duplicate`, no new rows.
- **Grouping**: same camera + zone + track (or hazard class) + event type, status OPEN/UNDER_REVIEW, within `merge_window_sec` of the last event → merged (union of missing PPE and rules, max confidence, `needs_review` sticky). Closed incidents are never reopened; a new one is created.
- **Lifecycle**: OPEN → UNDER_REVIEW → CONFIRMED | FALSE_POSITIVE; CONFIRMED → RESOLVED. Invalid transitions → 409. Every change is in `history`.
- **Evidence**: up to `max_evidence_frames` evenly spaced from `evidence_frames`, fetched from M1 (`GET /cameras/{id}/frames/{frame_id}` or an in-process buffer via `buffer_fetcher`), box drawn, optional best-effort face blur. Missing/expired frames never block the incident (`evidence_status: missing`).
- **Outbox**: topics `incident.created.v1`, `incident.updated.v1`, `incident.status_changed.v1`. At-least-once; consumers dedupe on `incident_id`. After `outbox_max_attempts` a row is dead-lettered; `POST /outbox/requeue-dead` retries.
- **Labels**: confirm/reject stores labelled events + evidence URIs (`GET /labels`) for retraining and rule-threshold tuning.

## Run
```bash
pip install -r requirements.txt && pytest -q
# from M2 output
python -m incident_service --config configs/incident.example.yaml --input ../rule_engine/events.jsonl
# HTTP API
INCIDENT_CONFIG=configs/incident.example.yaml uvicorn incident_service.app:create_app --factory --port 8003
```
In-process with M2: `RuleEngineSink(engine, on_event=IncidentEventSink(service))`.

## Notes / limits
- **Auth is not here**; M6 must front it (RBAC/OIDC). Do not expose publicly.
- SQLite is for dev/MVP (single process). Production: a PostgreSQL repository with the same methods (`db.py` is the interface), MinIO (`MinioEvidenceStore`), Kafka (`KafkaBus`). The MinIO/Kafka/HTTP-fetch paths are written but untested.
- Grouping is serialised by an in-process lock; running several replicas needs DB-level locking (Postgres advisory lock or unique partial index).
- Evidence retention/purge job and incident auto-close timers are not implemented yet.
- Tested: 12 service tests (grouping, idempotency, lifecycle, outbox retry/DLQ, rollback, path traversal) pass in my sandbox with a minimal runner; `tests/test_api.py` (FastAPI) has **not been run** – please run `pytest -q` on your Mac.
