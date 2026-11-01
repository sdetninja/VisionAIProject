# M9 - Platform, Security & Observability

Makes the safety path **visible**: if a camera goes blind, an outbox row dies, or a notification never gets delivered, someone
is paged. A silent system looks identical to a safe factory, so the alerts here focus on silence and dead letters first.

## Status: what is and isn't verified
Verified (sandbox, no Docker/network):
- 28 tests: metrics exposition (counters, histograms, escaping, cardinality cap, broken-collector isolation), ASGI middleware
  (hand-driven), log redaction, collectors against the real M3 DB, and static guards on the deploy files
  (every metric used in alerts/dashboard is actually emitted; only the gateway is published and only on loopback; containers
  hardened; non-root image).
- Existing suites still pass after my edits: M2 30, M3 12, M4 11, M5 13, M6 13 (custom runner; run `pytest -q` yourself).
- Smoke-ran the real CLI workers (rule engine, incident, agent, notification) with `--metrics-port` and scraped live output.
- All YAML parses.

NOT verified: `docker compose up` (no Docker here), the Dockerfile build, Prometheus rule syntax (`promtool` runs in the CI job I
wrote), Grafana dashboard rendering, the FastAPI `instrument()` path with a real app (FastAPI absent in the sandbox; middleware
tested as pure ASGI), the GitHub workflow, tracing/Loki/Vault/Kubernetes (not built - see gaps).

## What's here
- `vg_obs/` - stdlib-only helpers: `metrics.py` (Prometheus text format), `http.py` (ASGI middleware: request id, RED metrics,
  `/metrics`; plus `serve_metrics(port)` for CLI workers), `logs.py` (JSON logs, context binding, redaction), `collectors.py`
  (domain metrics for M2-M5, derived from the DB at scrape time).
- `deploy/` - compose file, one parametrised Dockerfile, Prometheus config + alert rules, Grafana dashboard, `.env.example`.
- `ci/github-workflow.yml` - tests per service, dashboard build, `promtool` + `compose config` lint, Trivy scan, gitleaks.

## How it's wired (changes to earlier modules)
Each API (`M2-M6`) calls `instrument(app, name)` inside `try/except ImportError`, so services still run without this package.
Each worker CLI gained `--metrics-port N`. M6 forwards `X-Request-ID` to upstream calls, so one user action can be traced across
service logs. SQLite stores now create their parent directory (a fresh volume crashed M3/M4/M5 on first start - found by the smoke
test). M3 gained `metrics_snapshot()`.
M1 already exposes its own `prometheus_client` metrics at `/metrics`; it is not in compose (needs camera/GPU) - add its scrape target.

## Run
```
cd visionguard/ops/deploy
cp .env.example .env            # set VG_JWT_SECRET and VG_METRICS_TOKEN; put the same token in prometheus.yml
mkdir secrets && openssl rand -hex 16 > secrets/grafana_admin.txt
docker compose up --build
```
Gateway: 127.0.0.1:8000, Prometheus: :9090, Grafana: :3001 (loopback only). Compose reads M1's output from
`/data/results/CAM_001.jsonl` on the shared volume - run M1 on the host and mount/copy its JSONL there.

## Alerts (deploy/prometheus/alerts.yml)
Critical: CameraDown, PipelineNotProcessingFrames, ServiceDown, OutboxDeadLetters, NotificationsDead.
Warning: FrameDropRateHigh, InferenceSlow (80 ms budget), OutboxBacklog, IncidentsUnreviewed (>30 min), GatewayErrors,
GatewayAuthFailuresSpike, LowReviewPrecision (>50% false positives, needs 20+ reviews), LLMFallbackHigh.
There is no Alertmanager yet: alerts show in Prometheus/Grafana but do not page anyone until you add one with a receiver.
Thresholds are initial guesses.

## Security notes
- Only the gateway is published (loopback); other services have no ports and live on an `internal: true` network with **no
  internet egress**. Slack/email/Anthropic calls will fail until you add an egress proxy or attach those workers to a network with
  outbound access. This is deliberate, but surprising.
- Containers: non-root, read-only rootfs, all capabilities dropped, no-new-privileges.
- `/metrics` accepts a bearer token (`VG_METRICS_TOKEN`); if unset it is open. The gateway serves `/metrics` on its user-facing
  port, so set the token or block the path at your reverse proxy.
- Logs redact `rtsp://user:pass@` URLs and keys like token/secret/password/authorization/api_key/rtsp/cookie.
- The Prometheus token is in a config file here for convenience; use `credentials_file` + secrets in production.

## Production gaps (deliberately not built)
- Single host: SQLite on a shared volume and a JSONL bus. Multiple hosts need Postgres + Kafka + MinIO (swap points exist in M3).
- No TLS termination, Kubernetes/Helm/ArgoCD, Loki, OpenTelemetry tracing, Vault, or GPU (DCGM) monitoring.
- No Alertmanager (see above), no backup/restore of the DB and evidence volume, no retention job for evidence (privacy policy
  requires one: 30-90 days in the architecture doc).
- Worker counters are per-process; DB-derived gauges (incidents, outbox, notifications) are the reliable ones.
- Metrics inherit M1's `camera_id` label; the cardinality cap (500 series/metric) protects against runaway labels.
