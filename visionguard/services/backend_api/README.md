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
export VG_JWT_SECRET=$(python3 -c "import secrets;print(secrets.token_urlsafe(48))")
uvicorn backend_api.app:create_app --factory --port 8000
python -m backend_api token --sub alice --role reviewer            # dev token
python -m backend_api token --sub bob --role viewer --cameras CAM_001
```
Other env: `VG_VISION_URL`, `VG_INCIDENT_URL`, `VG_AGENT_URL`, `VG_NOTIFY_URL`, `VG_AUDIT_DB`, `VG_CORS_ORIGINS`, `VG_AUDIENCE`, `VG_ISSUER`.

## Limits
- Terminate TLS in front (reverse proxy / ingress). Without HTTPS, bearer tokens are exposed.
- Scoped users: incident list scans up to 10 pages and `total` is `null`; fine for MVP, needs a camera-list filter in M3 at scale.
- Rate limiter and token verification caches are in-process (use Redis/proxy limits when running replicas). No token revocation list; use short expiry.
- Live video stream proxy is not implemented (admin-only placeholder in RBAC). Login/IdP setup is out of scope: you issue tokens via your IdP.
- Camera response shape from M1 `/cameras` is handled leniently (list of ids or dicts with `camera_id`); verify against M1.
- Tested: 13 gateway/auth/audit tests pass here with a minimal runner (RBAC, scoping, JWT attacks, tamper detection, rate limit). `tests/test_api.py` (FastAPI wiring) and HTTP upstream client are **unrun**; run `pytest -q` on your Mac.
