# Manual testing: Module 1 + Module 2, then the full stack in the UI

> **Architecture v3 (built 2026-11-16):** M5 is removed; M4 is the assessment service with a judge, and its results are
> stored in M3. See `ARCHITECTURE_V3_ASSESSMENT.md`.

Model: the combined PPE + fire/smoke YOLO11n from Milestone 0 (`runs/detect/runs/combined-yolo-v2/weights/best.pt`).
All Python services use one virtualenv that already has every dependency. Paste this line into **every new terminal**:

```bash
export VG=~/Desktop/VisionAIProject/visionguard PY=~/Desktop/VisionAIProject/visionguard/services/vision_pipeline/.venv/bin/python
```

| Piece | What it does | Port |
|---|---|---|
| M1 vision_pipeline | camera/clip -> YOLO -> tracks; writes `out/results/CAM_001.jsonl`, serves evidence frames | 8001 |
| M2 rule_engine | M1 results -> confirmed violations/hazards (`out/events.jsonl`) | - |
| M3 incident_service | events -> incidents + evidence images; publishes to its bus | 8003 |
| M4 assessment service (`agent_service`) | incident -> judge verdict (3 vision transformers; + 2 Claude models for fire/smoke) + risk score + explanation, stored in M3 | 8004 |
| M6 backend_api | the only API the UI talks to (login token, roles, audit) | 8000 |
| M7 dashboard | the UI | 3000 |

The demo clip `services/vision_pipeline/samples/combined_demo.mp4` (36 s, held-out test images):

| Time | Scene | Expected |
|---|---|---|
| 0-9 s | fire | fire alarm (HAZARD_DETECTED, fire) |
| 9-18 s | smoke only (tiny distant plumes) | often missed, see "Known model limits" |
| 18-24 s | workers without helmets | helmet violations (explicit `no_helmet`) |
| 21-24 s | full-body workers without vests | vest violations |
| 24-30 s | workers with helmet + vest | no events |
| 30-36 s | no fire or smoke | ideally nothing; a storm cloud may score as smoke |

---

## A. Module 1 + Module 2 only (no UI)
For a presentation (theory, script, readable event viewer, short demo cooldowns) use `DEMO_M1_M2_WEBCAM.md`.
To add Module 3 (incidents, evidence images, review) and the assessment service (judge, risk) without the UI: `TESTING_M1_M2_M3.md`.

### A1. With the clip
Terminal 1 - **M2 first** (it waits for M1's output file to appear):
```bash
cd $VG/services/rule_engine && rm -f out/events.jsonl ../vision_pipeline/out/results/CAM_001.jsonl
$PY -m rule_engine --config configs/rules.combined.yaml --input ../vision_pipeline/out/results/CAM_001.jsonl --output out/events.jsonl --follow
```
Terminal 2 - **M1** (opens a window with the boxes; the clip plays in real time, 36 s):
```bash
cd $VG/services/vision_pipeline && $PY -m vision_pipeline --config configs/pipeline.combined.clip.yaml --show --print
```
What to check:
- M1 window: red/green boxes with class + confidence; terminal 2 prints one line per frame.
- Terminal 1 prints one JSON line per **confirmed** event: `"event_type": "HAZARD_DETECTED", "hazard_class": "fire"` or
  `"PPE_VIOLATION", "missing_ppe": ["helmet"]`. `evidence_basis` says if it was `explicit` (a `no_helmet` box) or
  `inferred` (no helmet seen on a clearly visible person). Events are also in `rule_engine/out/events.jsonl`.
- Stop M2 with Ctrl+C when the clip is done. Readable summary of the events:
```bash
cd $VG/services/rule_engine && $PY -c "import json;[print(e['rule_id'],e['event_type'],e.get('hazard_class') or e['missing_ppe'],e['confidence'],e['evidence_basis']) for e in map(json.loads,open('out/events.jsonl'))]"
```
To test your own video: copy it into `services/vision_pipeline/samples/` and change `source:` in
`configs/pipeline.combined.clip.yaml`.

### A2. With the webcam
Same as A1, but terminal 2 runs the webcam config (press `q` in the window to stop):
```bash
cd $VG/services/vision_pipeline && $PY -m vision_pipeline --config configs/pipeline.combined.yaml --show --print
```
- First run: macOS asks your terminal app for **camera access**. Allow it, then restart the terminal and run again.
- Try: stand in view without a helmet (helmet event after about 2 s: 8 of the last 10 frames at 5 fps); hold up a phone
  playing a fire or smoke video (hazard event after 3 of 5 frames). A real hard hat should clear the helmet episode.
- Repeat alarms are held back by cooldowns (fire/smoke 5 min, PPE 1 min): restart M2 to test again.

---

## B. Full stack, viewed in the dashboard

### B1. One command (recommended)
```bash
$VG/scripts/dev_stack.sh start clip       # or: start webcam
$VG/scripts/dev_stack.sh token            # copy the printed token
```
Open **http://localhost:3000**, paste the token, Sign in. Other commands:
```bash
$VG/scripts/dev_stack.sh status           # which services are up
$VG/scripts/dev_stack.sh logs m2          # follow one log: m1 m2 m3 m3api m4 m6 m7
$VG/scripts/dev_stack.sh stop
$VG/scripts/dev_stack.sh reset            # stop + move all runtime data to visionguard/.dev/backup-<time>
```
Run `reset` before each new clip test: M1 frame ids restart at 1 on every run, so old and new events would collide.

### B2. Manually, one terminal per service
Do this to see each service's own output. Start them in this order (every `--follow` reader waits for its input file,
so downstream services can start first; M1 goes last because the clip starts playing as soon as M1 is up).
Create the login secret once (tokens only work with the secret the gateway was started with):
```bash
mkdir -p $VG/.dev && [ -f $VG/.dev/env.sh ] || $PY -c "import secrets;print('export VG_JWT_SECRET='+secrets.token_urlsafe(48))" > $VG/.dev/env.sh
```

| # | Terminal | Command (after the `export VG=... PY=...` line) |
|---|---|---|
| 1 | M2 rules | `cd $VG/services/rule_engine && $PY -m rule_engine --config configs/rules.combined.yaml --input ../vision_pipeline/out/results/CAM_001.jsonl --output out/events.jsonl --follow` |
| 2 | M3 ingester | `cd $VG/services/incident_service && $PY -m incident_service --config configs/incident.dev.yaml --input ../rule_engine/out/events.jsonl --follow` |
| 3 | M3 API | `cd $VG/services/incident_service && INCIDENT_CONFIG=configs/incident.dev.yaml INCIDENT_OUTBOX=0 $PY -m uvicorn incident_service.app:create_app --factory --port 8003` |
| 4 | M4 assessment | `cd $VG/services/agent_service && $PY -m agent_service --bus ../incident_service/data/bus/events.jsonl --m3-url http://127.0.0.1:8003 --kb knowledge_base --risk-config configs/risk.example.yaml --judge $VG/../milestone0/runs/judge/thresholds.yaml --port 8004 --follow` |
| 5 | M6 gateway | `cd $VG/services/backend_api && mkdir -p data && source $VG/.dev/env.sh && $PY -m uvicorn backend_api.app:create_app --factory --port 8000` |
| 6 | M7 dashboard | `cd $VG/dashboard && npm run dev` |
| 7 | M1 vision | `cd $VG/services/vision_pipeline && CONFIG_PATH=configs/pipeline.combined.clip.yaml $PY -m uvicorn vision_pipeline.app:app --port 8001` (webcam: `configs/pipeline.combined.yaml`) |
| - | token | `cd $VG/services/backend_api && source $VG/.dev/env.sh && $PY -m backend_api token --sub alice --role admin` |

Why two processes for M3: the worker (`--follow`) processes the stream, the API (uvicorn) answers the gateway and the
assessment service; they share the same SQLite database. The assessment service is one process (worker + API) so its
three models load once. `INCIDENT_OUTBOX=0` keeps the M3 API from publishing the same outbox
rows as the ingester. Stop everything with Ctrl+C in each terminal.

**Before each new run, clear runtime data** (or use `dev_stack.sh reset`, which keeps a backup):
```bash
cd $VG/services && rm -rf incident_service/data rule_engine/out vision_pipeline/out
```

### B3. What to look at in the UI (http://localhost:3000)
- **Incidents** (list): with the clip, 8 incidents within about 40 s: possible fire, possible smoke, missing helmet,
  missing helmet + vest (several people), missing vest. `needs review` = low confidence or inferred evidence.
  Columns **Severity** and **Judge** (confirmed / unsure / likely false alarm / no judge). Order: **Priority** (risk
  first; PPE the judge calls a likely false alarm goes last; fire/smoke never moved down) or newest. Filters: status,
  judge verdict, camera, needs-review only.
- **Incident detail** (click an ID): evidence images (3 frames, boxes drawn by M3), the AI assessment: the judge verdict
  with each vision transformer's score and the thresholds (for fire/smoke also the two Claude judges' answers), risk
  score, severity, suggested actions and SOP references from `agent_service/knowledge_base`; history, and the
  **Review** form. Fire is CRITICAL (85/100) and the judge says UNSURE unless the LLM panel is on
  (`export ANTHROPIC_API_KEY=...` and `JUDGE_PANEL=anthropic $VG/scripts/dev_stack.sh start clip`). Without `ANTHROPIC_API_KEY` the text comes from a template (`explanation_mode: template`);
  start with `LLM_KIND=anthropic $VG/scripts/dev_stack.sh start clip` (key exported) to have Claude write it.
- **Review**: Confirm / Reject / Under review with a comment. The reviewer name comes from the token. Rejected/confirmed
  labels feed M8 (Admin -> labels).
- **Cameras**: CAM_001 state (`running`, then `finished` for the clip), frames captured/processed/dropped, fps.
  Live video is not proxied by M6 yet; for a debug view open **http://localhost:8001/cameras/CAM_001/stream** (M1
  directly, no login - never expose it).
- **Admin** (admin role): audit log (`chain_valid` should be true). Labels: `GET /api/labels`.
- Roles: `--role viewer` (read only), `reviewer` (+ review), `admin` (+ labels/audit). Try a viewer token to see 403s.

### B4. Full stack with the webcam
`$VG/scripts/dev_stack.sh reset && $VG/scripts/dev_stack.sh start webcam` (or row 7 with `configs/pipeline.combined.yaml`).
Watch yourself at http://localhost:8001/cameras/CAM_001/stream, do the A2 tests, and the incidents appear in the UI.
Run it from Terminal (or iTerm/VS Code): the camera permission belongs to the app that starts M1.

---

## C. Known model limits (combined-yolo-v2; details in `milestone0/reports/combined_model_report.md`)
- Smoke: tiny, distant plumes score 0.25-0.33 and are missed at the 0.35 threshold; storm clouds/fog can score ~0.35.
- Gloves and goggles: small objects, about half are missed. The gloves rule only fires on an explicit `no_gloves` (>= 0.50).
- Vest absence is only inferred when the whole person is in frame (not for head-and-shoulders views).
- Every hazard event is flagged `needs_review`: a person confirms before anyone acts on it.

## D. Troubleshooting
| Symptom | Fix |
|---|---|
| Dashboard: `bad signature` / cannot sign in | Token made with a different secret: generate it with `dev_stack.sh token` (or `source $VG/.dev/env.sh` first). |
| `upstream unavailable` in the UI | A service is down: `dev_stack.sh status`, then its log. |
| No incidents, but M2 printed events | M3 ingester not running (row 2) or it was started with a different `--input`. |
| Incidents without images | M1 must be running on 8001 when M3 ingests (frames are kept 30 s). |
| `cannot open 0` (webcam) | Allow camera access for your terminal app (System Settings -> Privacy & Security -> Camera), restart it. |
| `address already in use` | Something already listens on that port: `dev_stack.sh stop`, or `lsof -nP -iTCP:8000 -sTCP:LISTEN`. |
| Old incidents mixed with new ones | Run `dev_stack.sh reset` (or the `rm -rf` line in B2) before a new run. |
