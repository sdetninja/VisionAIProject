# Testing Module 1 + Module 2 + Module 3 (+ the assessment service) together

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
- **Bus output for the assessment service**: `wc -l $VG/services/incident_service/data/bus/events.jsonl` - one message per incident
  created or updated (12 for the clip, before reviews).
- **Evidence needs M1**: stop M1 before M3 has processed an event and that incident gets `evidence_status: missing`
  (frames live only 30 s in M1's memory).

## 7. Add Module 4: the assessment service (judge + risk + explanation)
```
M3 ingester ──incident.created/updated──► data/bus/events.jsonl ──► assessment service (one process, port 8004)
                                                                      1. fetch the incident + unannotated frames from M3 (8003)
                                                                      2. judge: PPE → 3 vision transformers; fire/smoke → + 2 Claude models
                                                                      3. risk.py (rules decide severity)  4. SOP search + template text
                                                                      5. POST /incidents/{id}/assessment ──► M3 incidents.db
```
It reads the bus M3 writes and needs the M3 API (terminal 1) for the images and to store its result. The three
judges come from Milestone 0 (`milestone0/runs/judge/`, about 280 MB). Start it **before M1** (terminal 7); started
later it reads the bus from the beginning and catches up.

**Terminal 7 - assessment service** (worker + API on 8004; the models load in about 3 s):
```bash
cd $VG/services/agent_service && $PY -m agent_service --bus ../incident_service/data/bus/events.jsonl --m3-url http://127.0.0.1:8003 --kb knowledge_base --risk-config configs/risk.example.yaml --judge $VG/../milestone0/runs/judge/thresholds.yaml --port 8004 --follow
```
It keeps no data of its own: everything is in M3, so step 1 clears it too.

**What to check**
- Terminal 7 prints one line per incident version: verdict, severity, and why, e.g.
  `INC-000003: judge CONFIRMED (vit) | HIGH (71) grounded=True | helmet: P(missing) 0.94 -> CONFIRMED`.
- One incident with its assessment (judge scores per model, risk factors, summary, actions, SOP citations):
  ```bash
  curl -s http://127.0.0.1:8003/incidents/INC-000003 | $PY -c "import json,sys;print(json.dumps(json.load(sys.stdin)['assessment'],indent=2))"
  ```
- The review queue in priority order (risk first; PPE the judge calls a likely false alarm last; fire never moved down):
  ```bash
  curl -s "http://127.0.0.1:8003/incidents?sort=priority" | $PY -c "import json,sys;[print(i['incident_id'],i['severity'],i['risk_score'],i['judge_verdict']) for i in json.load(sys.stdin)['items']]"
  ```
- Re-judge one incident now: `curl -s -X POST "http://127.0.0.1:8004/assess/INC-000005?force=true"`
- Search the knowledge base the way the service does: `curl -s "http://127.0.0.1:8004/search?q=helmet"`

**Expected with the clip** (live run checked 2026-11-16; scores can move a few points with the order of events):

| Incident | Judge | Severity (score) |
|---|---|---|
| INC-000001 fire | UNSURE (ViT 0.99; no LLM panel without an API key) | CRITICAL (85) |
| INC-000002 smoke | UNSURE (ViT 0.95; same) | HIGH (79) |
| INC-000003 helmet | CONFIRMED (0.94) | HIGH (71) |
| INC-000004..7 helmet + vest | CONFIRMED (helmet 0.66-0.98; vest 0.63-0.97) | HIGH (69-76) |
| INC-000008 vest | UNSURE (0.50: the vest check is the weakest) | MEDIUM (54) |

**Points to make**
- **Two opinions, people decide.** The rule engine makes a claim; the judge (three different vision transformers,
  averaged) checks it on a clean crop. Nothing is closed or hidden: "likely false" only moves a PPE incident down.
- **Fire/smoke gets an LLM panel**: Claude Opus 5.5 on the crop and Claude Sonnet 5.5 on the full frame, both must agree.
  PPE images never leave the machine.
- **Severity is never decided by the AI.** `configs/risk.example.yaml` decides it (see `risk_factors`).
- **Grounded in standards**: actions cite `knowledge_base/*.md` (OSHA, ANSI/ISEA 107, a site template).
- **Idempotent**: restart terminal 7; it re-reads the bus and judges nothing again (M3 already has each version).

**Optional: switch on the LLM panel and Claude-written text.** Install the SDK once (`$PY -m pip install "anthropic>=2.6"`),
`export ANTHROPIC_API_KEY=...` in terminal 7, and add `--panel anthropic` (fire/smoke judges) and/or `--llm anthropic`
(explanations). Each fire/smoke incident then costs about $0.06 and sends its frames to the Anthropic API.

## 8. Stop
Ctrl+C in terminals 1-4 (and 5-7). Next run: step 1 (`dev_stack.sh reset`) again.

## Troubleshooting
| Symptom | Fix |
|---|---|
| Incidents have no images (`missing`) | M1 was started with `--show` (no API) or stopped too early; use the uvicorn command in terminal 4. |
| No incidents but M2 printed events | Terminal 2 not running, or started in another folder (it reads `../rule_engine/out/events.jsonl`). |
| `show_incidents.py`: M3 API not reachable | Terminal 1 not running (port 8003). |
| `address already in use` | Something else holds 8001/8003: `$VG/scripts/dev_stack.sh stop`. |
| Old incidents mixed in | Run step 1 before every test. |
| `cannot open 0` (webcam) | Camera permission for the terminal app (System Settings → Privacy & Security → Camera), restart it; close Zoom/Teams. |
