# M5 – Notification Service

Turns incidents into alerts people actually act on: **routing by severity, de-duplication, retries, rate limits, and escalation if nobody acknowledges.**

```
M3 bus (incident.created/updated/status_changed) ┐
M4 bus (incident.analyzed)                       ┴─► state ─► routes ─► queue (SQLite) ─► channels (console/jsonl/Slack/email/webhook)
                                                               └► escalations (unacknowledged after N min)
```

## Behaviour
- **Waits for M4's severity** (up to `analysis_wait_sec`, default 30s) so alerts are routed by real severity. If analysis never arrives it sends as `UNRATED`, which is routed like HIGH (fail loud, not silent).
- **Routing**: every matching route fires. Match on `severity`, `min_severity`, `event_type`, `camera_id`, `zone`, `needs_review`.
- **Idempotent**: dedupe key = incident | kind | route | channel, so bus redelivery never double-sends. Severity rising after the first notice sends one `upgrade` message.
- **Escalation**: if the incident isn't acknowledged (UNDER_REVIEW/CONFIRMED in M3) within `after_min`, the escalation rule fires. Acknowledging, rejecting or resolving cancels pending escalations; a false positive reviewed before delivery cancels the initial alert too.
- **Delivery**: per-notification retry with exponential backoff, dead-letter after `max_attempts` (`POST /notifications/requeue-dead`). One channel failing never blocks another. Per-channel rate limit **defers** excess (never drops).
- **Secrets** come from env vars (`SLACK_WEBHOOK_URL`, `SMTP_USER`, `SMTP_PASS`, `NOTIFY_WEBHOOK_URL`), never the YAML.

## Run
```bash
pip install -r requirements.txt && pytest -q
python -m notification_service --config configs/notify.example.yaml \
   --bus ../incident_service/data/bus/events.jsonl --bus ../agent_service/data/analyses.jsonl [--follow]
NOTIFY_CONFIG=configs/notify.example.yaml uvicorn notification_service.app:create_app --factory --port 8005
```

## Limits
- Alerts contain no images (privacy); they link to the dashboard (`dashboard_url`). Evidence stays behind M6 auth.
- Not built: quiet hours / on-call rotas, SMS/Teams/push, per-user preferences, message templates per channel, alert grouping/digests.
- Slack, email and webhook channels are written but **untested** (no network here). The FastAPI app and its tick loop are also unrun.
- `--bus` file mode re-reads from the start each run; state in `db_path` makes that safe (dedupe), but use `--follow` or a real consumer group in production.
- Tested: 13 tests (routing, wait/unrated fallback, dedupe, upgrade, escalation, ack, cancel, retry/DLQ, rate limit, config validation) pass here with a minimal runner; run real `pytest -q` on your Mac.
