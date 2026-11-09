# M6 – Backend API (gateway)

The **only** service the dashboard talks to. Internal services (M1, M3, M4, M5) have no auth and must not be exposed.

```
Dashboard ─HTTPS+JWT─► backend_api ─► M1 cameras · M3 incidents/review/evidence · M4 analyses · M5 notifications
                         authn → RBAC → camera scope → audit → rate limit
```

## Security model
| Concern | How |
|---|---|
| Authn | Bearer JWT. `HS256Verifier` (dev, secret ≥32 chars in `VG_JWT_SECRET`) or `OidcVerifier` (RS256 via your IdP JWKS; needs `pyjwt[crypto]`). Checks sig, exp, nbf, aud, iss; `alg` is never taken from the token (blocks `alg=none`). |
| RBAC | `viewer` read; `reviewer` + review/notifications read; `admin` + labels, audit, requeue, live stream. Edit `rbac.py`. |
| Camera scope | Optional `cameras` claim limits a user to listed cameras. Out-of-scope incidents return **404** (no existence leak) for read, evidence and review. |
| Identity of reviewer | Taken from the token; the request body has no `actor` field. |
| Data exposure | Raw storage URIs and internal event payloads are stripped; evidence is served via `/api/incidents/{id}/evidence/{n}` after scope check, and each view is audited. |
| Audit | Hash-chained SQLite log of reviews, evidence views, exports, denials. `GET /api/audit` reports `chain_valid`. |
| Other | Per-user rate limit, no wildcard CORS, `nosniff`/`no-store` headers, API docs disabled, upstream errors masked (502/503). |

## Endpoints
`GET /health` · `/api/me` · `/api/cameras` · `/api/cameras/{id}/latest` · `/api/incidents` (filters: status, camera_id, zone, needs_review, limit, offset) · `/api/incidents/{id}` (includes AI analysis; degrades to `{"unavailable": true}`) · `/api/incidents/{id}/evidence/{n}` · `POST /api/incidents/{id}/review {action, comment}` · `/api/labels` (admin) · `/api/notifications` · `POST /api/notifications/requeue-dead` (admin) · `/api/audit` (admin)

## Run
```bash
pip install -r requirements.txt && pytest -q
export VG_JWT_SECRET=$(python3 -c "import secrets;print(secrets.token_urlsafe(48))")   # ≥32 chars; app refuses to start without it
uvicorn backend_api.app:create_app --factory --port 8000
```

| Env var | Default | Purpose |
|---|---|---|
| `VG_JWT_SECRET` | – (required) | HS256 signing secret for dev tokens |
| `VG_VISION_URL` / `VG_INCIDENT_URL` / `VG_AGENT_URL` / `VG_NOTIFY_URL` | `http://localhost:8001` / `8003` / `8004` / `8005` | Internal M1 / M3 / M4 / M5 services |
| `VG_AUDIT_DB` | `data/audit.db` | Hash-chained audit log |
| `VG_CORS_ORIGINS` | `http://localhost:3000` | Comma-separated; `*` is rejected |
| `VG_AUDIENCE` / `VG_ISSUER` | `visionguard-api` / `visionguard` | Expected `aud` / `iss` claims |

## Dev tokens
A token only works on a gateway started with the **same `VG_JWT_SECRET`** it was signed with. Generate tokens in the
terminal that started the gateway, or `source` the same env file first:
```bash
python -m backend_api token --sub alice --role reviewer                 # 8 h by default (--hours N)
python -m backend_api token --sub bob --role viewer --cameras CAM_001   # limited to listed cameras
python -m backend_api token --sub root --role admin
```
`--role` must be `viewer`, `reviewer` or `admin` (typos such as `reviewer.` are rejected).

Example: the local test stack (gateway on port 18000) keeps its settings in an env file, so load it in the same command
(that file lives in a temporary scratch folder; if you start your own stack, point this at your own env file):
```bash
source /private/tmp/claude-501/-Users-raj--claude-spares-spare-lvU4KC/7665eb7b-4895-483f-94f0-301aac640fe7/scratchpad/m6/env.sh && python -m backend_api token --sub alice --role admin
```
Check a token before pasting it into the dashboard (prints your user and role, or the reason it was rejected):
```bash
curl -s -H "Authorization: Bearer <token>" http://localhost:8000/api/me
```

### Troubleshooting
| Response | Cause / fix |
|---|---|
| `bad signature` | Token signed with a different `VG_JWT_SECRET` than the running gateway. Regenerate it in the gateway's terminal / env. |
| `invalid choice: 'reviewer.'` | Wrong role name; use `viewer`, `reviewer` or `admin`. |
| `token expired` | Default lifetime is 8 h; generate a new one (or pass `--hours`). |
| `KeyError: 'VG_JWT_SECRET'` | The token command needs the secret too; export it or `source` the env file. |
| `upstream unavailable` (503) | An internal service (M1/M3/M4/M5) is not running at its `VG_*_URL`. |

The dashboard (M7, `npm run dev`) proxies `/api` to `http://localhost:8000`; set `VG_API` to point it elsewhere.

## Limits
- Terminate TLS in front (reverse proxy / ingress). Without HTTPS, bearer tokens are exposed.
- Scoped users: incident list scans up to 10 pages and `total` is `null`; fine for MVP, needs a camera-list filter in M3 at scale.
- Rate limiter and token verification caches are in-process (use Redis/proxy limits when running replicas). No token revocation list; use short expiry.
- Live video stream proxy is not implemented (admin-only placeholder in RBAC). Login/IdP setup is out of scope: you issue tokens via your IdP.
- Camera response shape from M1 `/cameras` is handled leniently (list of ids or dicts with `camera_id`); verify against M1.
- Tested: `pytest -q` → 14 passed on macOS / Python 3.12 (2026-11-08): 13 gateway/auth/audit tests (RBAC, scoping, JWT attacks, tamper detection, rate limit) + the FastAPI flow. Also run end to end against live M3 and M4 over HTTP (`HttpUpstream`): list, detail, scoped 404, review with identity from the token, 403s, 503 when M5 is down, audit chain valid.
- `app.py` must **not** use `from __future__ import annotations`: route signatures use types imported inside `create_app()`, which FastAPI cannot resolve as strings, so every authenticated request failed.
