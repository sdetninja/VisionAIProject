# M7 – Dashboard (React + Vite)

Review console for supervisors. Talks **only** to the M6 gateway (`/api/*`).

## Screens
- **Incidents** – filterable queue (status, camera, needs-review), auto-refresh every 5 s, paging.
- **Incident detail** – evidence gallery, AI assessment (clearly labelled *advisory only*, with policy citations and score breakdown), review actions with comment, history.
- **Cameras** – cameras visible to the signed-in user.
- **Notifications / Admin** – delivery queue (reviewer+), retry failed, tamper-evident audit log with chain status (admin).

## Run
```bash
npm install
npm test                     # 9 logic tests, no browser needed
npm run dev                  # http://localhost:3000, /api proxied to http://localhost:8000 (VG_API to override)
npm run build                # static files in dist/ (serve behind the same origin as /api)
```
Sign in by pasting a dev token: `python -m backend_api token --sub alice --role reviewer` (M6).

## Design decisions
- **Logic lives in plain `.js` modules** (`api.js`, `auth.js`, `format.js`, `polling.js`) so it is testable without a DOM; components are thin.
- **Server is the authority.** The UI hides buttons by role but the gateway enforces RBAC and camera scope. Role/claims decoded from the token are display-only.
- **Evidence images** need the auth header, so they are fetched as blobs and shown via object URLs (revoked on unmount). Only gateway paths matching `/api/incidents/{id}/evidence/{n}` are ever fetched.
- **No `dangerouslySetInnerHTML`**; all LLM/event text is rendered as text. Strict CSP in `index.html`.
- Token is kept in `sessionStorage` (cleared with the tab). That is readable by page scripts, hence the CSP; with an IdP, move to an in-memory token + refresh via the IdP's SDK.
- Polling pauses when the tab is hidden and backs off on errors; last good data stays on screen with an error banner.
- A false-positive verdict requires a comment (it becomes a training label in M8).

## Not done / limits
- Verified 2026-11-08 (Node 26, Vite 5.4): `npm test` 9/9, `npm run build` clean, and `npm run dev` driven in a browser against the live M1/M3/M4/M5/M6 stack: login, queue, incident detail with the Claude analysis, review (start → under review, false positive blocked without a comment), Cameras, Notifications, Admin audit log (`chain intact`), no console errors. Still no component tests.
- npm 11 warns that `esbuild`'s install script was not run; the build works without it (the platform binary ships as an optional dependency).
- No live video / bounding-box overlay yet (gateway has no stream proxy).
- No SSO login screen, no i18n, no accessibility audit, no component tests (add Vitest + Testing Library next).
- Queue sorts by newest; severity sorting needs M3 to store severity from M4.
- Production: serve `dist/` and the gateway on one origin behind HTTPS; keep the CSP `connect-src 'self'`.
