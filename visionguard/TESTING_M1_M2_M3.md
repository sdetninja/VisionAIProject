# Testing Module 1 + Module 2 + Module 3 (+ Module 4) together

Steps 0-6 test M1-M3; step 7 adds M4 (AI risk assessment).

```
M1 vision (service, port 8001) ──results──► M2 rules ──events──► M3 ingester ──► incidents DB + evidence JPEGs
        ▲                                                            │                    ▲
        └────────── GET /cameras/CAM_001/frames/{id} ◄───────────────┘          M3 API (port 8003): list, detail,
                    (M3 fetches the evidence frames from M1's 30 s buffer)       evidence, review
```
- **M1 must run as a service** (uvicorn, port 8001), not the `--show` command: M3 downloads the evidence frames from
  M1's API. With `--show` there is no API and incidents get `evidence_status: missing`.
- **M3 runs as two processes** sharing one database: the **ingester** (`--follow`, turns M2 events into incidents and
  saves evidence) and the **API** (port 8003, to look at and review incidents).
- Verified 2026-11-14 with the demo clip: 12 M2 events → **8 incidents**, 36 evidence images, review flow working.

## 0. Every terminal: set the paths
```bash
export VG=~/Desktop/VisionAIProject/visionguard PY=~/Desktop/VisionAIProject/visionguard/services/vision_pipeline/.venv/bin/python
```

## 1. Fresh start (once, before each test run)
First press Ctrl+C in every terminal from a previous manual run: `reset` stops only what `dev_stack.sh start`
launched, and an old M1 left running keeps port 8001 busy. Then move old data to a backup (M1 frame ids restart at 1 on
every run, so old and new events would collide):
```bash
$VG/scripts/dev_stack.sh reset
```

## 2. Start the services (4 terminals, in this order)
**Terminal 1 - M3 API** (port 8003):
```bash
cd $VG/services/incident_service && INCIDENT_CONFIG=configs/incident.dev.yaml INCIDENT_OUTBOX=0 $PY -m uvicorn incident_service.app:create_app --factory --port 8003
```
**Terminal 2 - M3 ingester** (waits for M2's events file; fetches evidence from M1 on port 8001):
```bash
cd $VG/services/incident_service && $PY -m incident_service --config configs/incident.dev.yaml --input ../rule_engine/out/events.jsonl --follow
```
**Terminal 3 - M2 rule engine** (waits for M1's results file). Clip: `rules.combined.yaml`; webcam demo:
`rules.combined.demo.yaml` (short cooldowns so you can repeat events):
```bash
cd $VG/services/rule_engine && $PY -m rule_engine --config configs/rules.combined.yaml --input ../vision_pipeline/out/results/CAM_001.jsonl --output out/events.jsonl --follow
```
**Terminal 4 - M1 vision service** (start last: the clip begins playing at once):
```bash
cd $VG/services/vision_pipeline && CONFIG_PATH=configs/pipeline.combined.clip.yaml $PY -m uvicorn vision_pipeline.app:app --port 8001
```
For the **webcam**, use `CONFIG_PATH=configs/pipeline.combined.yaml` instead (macOS asks this terminal app for camera
access the first time; allow it and restart the terminal).

**Watch M1** in a browser (there is no window in service mode): **http://localhost:8001/cameras/CAM_001/stream**

## 3. Watch it happen (optional terminals 5 and 6)
```bash
$PY $VG/scripts/show_events.py              # M2: one line per confirmed event
$PY $VG/scripts/show_incidents.py --watch   # M3: incident list, refreshed every 2 s
```

## 4. Check each module
**M1 is running and processing**
```bash
curl -s http://127.0.0.1:8001/cameras
```
Expect `"state":"running"` (clip: `"finished"` after 36 s), `processed` rising, `dropped` near 0, `fps` about 5.
`/health` says `degraded` once the clip has finished: that is normal for a file source.

**M2 produced events** - terminal 3 prints one JSON line per event, or:
```bash
wc -l $VG/services/rule_engine/out/events.jsonl
```
Clip: **12 events** (fire, smoke, 4 missing helmet, 6 missing vest).

**M3 created incidents**
```bash
$PY $VG/scripts/show_incidents.py
```
Clip: **8 incidents**, all with `ok 3 img` or `ok 6 img` evidence:

| Incident | What | Why |
|---|---|---|
| INC-000001 | FIRE | hazard, needs review |
| INC-000002 | SMOKE | hazard, needs review |
| INC-000003 | missing helmet (T001) | explicit `no_helmet`, high confidence: no review needed |
| INC-000004..7 | missing helmet, vest | **grouping**: the helmet and vest events of the same person within 10 s merge into ONE incident (2 events, 6 images) |
| INC-000008 | missing vest | inferred, needs review |

**One incident in detail** (events, how it was decided, evidence file paths, history):
```bash
$PY $VG/scripts/show_incidents.py INC-000005
```
**Look at the evidence images** (the violating person boxed in red with the rule id):
```bash
open $VG/services/incident_service/data/evidence/CAM_001
```

## 5. Test the review workflow (M3 API)
Allowed: `start_review` (OPEN → UNDER_REVIEW), `confirm` (OPEN/UNDER_REVIEW → CONFIRMED), `reject`
(OPEN/UNDER_REVIEW → FALSE_POSITIVE), `resolve` (CONFIRMED → RESOLVED).
```bash
curl -s -X POST http://127.0.0.1:8003/incidents/INC-000003/review -H 'Content-Type: application/json' -d '{"action":"confirm","actor":"alice","comment":"No helmet, confirmed on the images"}'
curl -s -X POST http://127.0.0.1:8003/incidents/INC-000002/review -H 'Content-Type: application/json' -d '{"action":"reject","actor":"alice","comment":"Not smoke"}'
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8003/incidents/INC-000001/review -H 'Content-Type: application/json' -d '{"action":"resolve","actor":"alice"}'
```
Expect: INC-000003 `CONFIRMED`, INC-000002 `FALSE_POSITIVE`, and **409** for the last one (an OPEN incident cannot be
resolved before it is confirmed). Then:
```bash
$PY $VG/scripts/show_incidents.py INC-000003          # history: system -> OPEN, alice OPEN -> CONFIRMED
curl -s http://127.0.0.1:8003/labels                   # reviewer verdicts (true/false positive) for retraining (M8)
```
In the full system the reviewer comes from the login token (M6), not from the request body.

## 6. Other checks worth showing
- **Idempotency** (a re-delivered event never creates a second incident): stop terminal 2 (Ctrl+C) and start it
  again with the same command. It re-reads every event from the start, and the incident count stays at 8 (the M3 log
  summary shows them as duplicates).
- **Bus output for M4/M5**: `wc -l $VG/services/incident_service/data/bus/events.jsonl` - one message per incident
  created or updated (12 for the clip, before reviews).
- **Evidence needs M1**: stop M1 before M3 has processed an event and that incident gets `evidence_status: missing`
  (frames live only 30 s in M1's memory).

## 7. Add Module 4 (AI risk assessment)
```
M3 ingester ──incident.created/updated──► data/bus/events.jsonl ──► M4 worker ──► data/analyses.jsonl (for M5)
                                                                        │             + data/agent.db
                                                                        └── risk.py (rules decide severity)
                                                                            kb.py   (finds the matching SOP section)
                                                                            template or Claude (writes the text)
                                                                    M4 API (port 8004): read analyses, search the SOPs
```
M4 reads the bus that M3 writes, so it needs no change to the steps above. Start it **before M1** (terminals 7 and 8,
after the `export` line); if you start it later it still reads the bus from the beginning and catches up.

**Terminal 7 - M4 worker** (waits for M3's bus file):
```bash
cd $VG/services/agent_service && mkdir -p data && $PY -m agent_service --bus ../incident_service/data/bus/events.jsonl --kb knowledge_base --risk-config configs/risk.example.yaml --db data/agent.db --follow
```
**Terminal 8 - M4 API** (port 8004, same database):
```bash
cd $VG/services/agent_service && KB_DIR=knowledge_base RISK_CONFIG=configs/risk.example.yaml AGENT_DB=data/agent.db $PY -m uvicorn agent_service.app:create_app --factory --port 8004
```
`dev_stack.sh reset` also moves `agent_service/data` to the backup, so step 1 clears M4 too.

**What to check**
- Terminal 7 prints one line per incident version, e.g. `INC-000001: CRITICAL (85) grounded=True mode=template`.
  Clip: **12 lines for 8 incidents**. A grouped incident (helmet + vest) is analysed again when its second event
  arrives, and its score goes up.
- One analysis in full (severity, how the score was made, summary, actions, SOP citations):
  ```bash
  curl -s http://127.0.0.1:8004/analyses/INC-000001 | $PY -m json.tool
  ```
- All analyses, one line each (the latest per incident is the one that counts):
  ```bash
  $PY -c "import json;[print(p['incident_id'],p['severity'],p['risk_score'],p['explanation_mode'],'|',p['summary']) for p in (json.loads(l)['payload'] for l in open('$VG/services/agent_service/data/analyses.jsonl'))]"
  ```
- Search the knowledge base the way M4 does: `curl -s "http://127.0.0.1:8004/search?q=helmet"`
- An incident that does not exist returns **404**: `curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8004/analyses/INC-999999`

**Expected with the clip** (live M1-M4 run checked 2026-11-15; scores can move a few points with
the order of events, because of the repeat bonus):

| Incident | Severity (score) | Why |
|---|---|---|
| INC-000001 fire | CRITICAL (85) | base 70, fire +30, × 0.85 because it needs review |
| INC-000002 smoke | HIGH (79) | |
| INC-000003 helmet | HIGH (55) | explicit, no review: no 0.85 discount |
| INC-000004..7 helmet + vest | HIGH (63-71, then 76) | helmet +25, vest +10, **repeat bonus**: up to +24 for similar incidents on the same camera and zone in 24 h |
| INC-000008 vest | MEDIUM (54) | |

All are `grounded=True` (a matching SOP section was found) and `advisory_only: true`. With no SOP match the analysis
says `grounded: false` and asks for a human instead of inventing a policy.

**Points to make**
- **Severity is never decided by the AI.** `configs/risk.example.yaml` weights and thresholds decide it (see
  `risk_factors`); the text writer only explains it, and an answer that contradicts the severity is rejected.
- **Grounded in standards**: recommended actions cite `knowledge_base/*.md` sections: OSHA PPE and fire rules, the
  ANSI/ISEA 107 vest standard and a site response template (list in `services/agent_service/README.md`). With the clip,
  helmet incidents cite SITE-PPE-RESPONSE and OSHA-1910.135, vest incidents ANSI-ISEA-107, fire OSHA-1910.38, smoke
  OSHA-1910.38-SMOKE.
- **Idempotent**: restart terminal 7; it re-reads the bus and its summary says `'analyzed': 0, 'cached': 12`.
- `/health` on 8004 counts only the API process's own work, so it shows 0 while the worker does the analysing.

**Optional: let Claude write the text.** By default the text comes from a template (`mode=template`). For Claude,
install the SDK once (`$PY -m pip install "anthropic>=2.6"`, not installed yet), `export ANTHROPIC_API_KEY=...` in
terminals 7 and 8, and add `--llm anthropic` to terminal 7 and `LLM_KIND=anthropic` to terminal 8. `mode=llm` means
Claude wrote it; `template_fallback` means the call failed or a guardrail rejected the answer (reason in
`guardrail_problems`). The severity and score stay the same. Clear `agent_service/data` first, or the cached template
answers are reused.

## 8. Stop
Ctrl+C in terminals 1-4 (and 5-8). Next run: step 1 (`dev_stack.sh reset`) again.

## Troubleshooting
| Symptom | Fix |
|---|---|
| Incidents have no images (`missing`) | M1 was started with `--show` (no API) or stopped too early; use the uvicorn command in terminal 4. |
| No incidents but M2 printed events | Terminal 2 not running, or started in another folder (it reads `../rule_engine/out/events.jsonl`). |
| `show_incidents.py`: M3 API not reachable | Terminal 1 not running (port 8003). |
| `address already in use` | Something else holds 8001/8003: `$VG/scripts/dev_stack.sh stop`. |
| Old incidents mixed in | Run step 1 before every test. |
| `cannot open 0` (webcam) | Camera permission for the terminal app (System Settings → Privacy & Security → Camera), restart it; close Zoom/Teams. |
