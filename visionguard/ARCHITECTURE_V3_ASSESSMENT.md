# VisionGuard architecture v3: assessment service with a hybrid judge

Status: **built 2026-11-16.** ViT judges trained and evaluated (Milestone 0, scripts 10-13; section 5); M3, the assessment
service, M6, M7 and ops changed and M5 removed (section 8). The fire/smoke LLM panel is built but has not made a live call yet. Builds on `../VisionGuard_AI_Architecture_v2.md`; where they differ, this document wins.

## 1. Decisions
| # | Decision | Why |
|---|---|---|
| 1 | **Remove M5 (notification service).** The dashboard is where incidents are seen. | Fewer moving parts: 2 processes, 1 port, 1 database and 1 bus consumer less. Push alerts can come back later as a small webhook call from M3. |
| 2 | **Merge M4 and the new judge into one assessment service.** | One consumer of new incidents; one place that decides "is it real, how serious, what to do". |
| 3 | **Store every assessment in M3's incident database** (new `assessments` table), not in `agent.db` / `analyses.jsonl`. | One source of truth: the incident and its assessment are read together; M6 reads M3 only. |
| 4 | **Hybrid judge.** PPE incidents: **ViT ensemble only** (local). Fire/smoke incidents: **ViT ensemble + a panel of two LLMs** (Claude Opus 5.5 and Claude Sonnet 5.5). | PPE is frequent: a local check costs nothing, takes ~0.1 s and keeps worker images on site. Fire/smoke is rare and costly to get wrong: lookalikes (a phone screen, steam, sunset) need the context an LLM brings. |
| 5 | **ViT ensemble = three different transformer families** (ViT-S/16 AugReg, ViT-S/14 DINOv2, Swin-T), probabilities averaged. | Different pretraining and attention design make different mistakes, so the average is steadier than any one model. |
| 6 | **The judge is advisory.** It never closes, deletes or hides an incident, and never lowers a fire/smoke incident. | Same safety stance as v2: people decide. A "likely false" verdict only moves an incident down the review queue. |

## 2. Data flow
```
M1 vision ─► M2 rules ─► M3 incident service ─────────────────────────────► incidents.db
                          │ incident created/updated (outbox → bus)            ▲  incidents
                          ▼                                                    │  assessments  (new)
                 Assessment service (was M4)                                   │  history, labels
                 1. crops     raw frames from M3 → person / head / hazard crops│
                 2. judge     PPE:        ViT ensemble                         │
                              fire/smoke: ViT ensemble + Claude Opus 5.5       │
                                                      + Claude Sonnet 5.5      │
                 3. risk      rules (risk.yaml) → severity + score             │
                 4. explain   SOP search + template (or Claude) → actions      │
                 5. POST /incidents/{id}/assessment ─────────────────────────────┘
M6 gateway ─► M3 only ─► M7 dashboard (incident + assessment + review)
```
M5 and its two processes, `agent.db` and `analyses.jsonl` are gone. The bus stays (M3 → assessment service). The
assessment service is one process (`python -m agent_service ... --port 8004`): the bus worker plus a small API
(health, metrics, SOP search, `POST /assess/{id}?force=true`), so the ViT models are loaded once.

## 3. The judge
### 3.1 What it checks
The rule engine makes a **claim**; the judge returns the probability that the claim is true.

| Incident | Claim | Crop | Judge |
|---|---|---|---|
| missing helmet | "this person has no helmet" | head square at the top of the person box, side `min(0.6 × width, 0.3 × height)`, +15 % margin (section 5) | ViT ensemble: P(no_helmet) over {helmet, no_helmet} |
| missing vest | "this person has no vest" | the person box (only when the whole person is in frame, as in M2) | ViT ensemble: P(no_vest) over {vest, no_vest} |
| fire / smoke | "there is fire/smoke here" | the fire/smoke box + 15 % margin, plus the full frame for the LLMs | ViT ensemble: P(fire)+P(smoke) over {fire, smoke, none} **and** the LLM panel |
| missing gloves, goggles, mask | - | - | **no judge yet** (verdict `NO_JUDGE`): no trained check; stays with human review |
| restricted area | - | - | `NOT_APPLICABLE`: geometry, not appearance |

Scores are averaged over the incident's evidence frames (3 per event). A grouped incident (helmet + vest) is judged per
item: `CONFIRMED` if any item is confirmed, `LIKELY_FALSE` only if every item is rejected, otherwise `UNSURE`.

### 3.2 Verdicts
PPE (ViT only), with the thresholds from `milestone0/runs/judge/thresholds.yaml` (chosen on val, section 5):

| Ensemble score | Verdict | Effect in the dashboard |
|---|---|---|
| ≥ `confirm_above` | `CONFIRMED` | "judge: confirmed"; normal priority |
| < `reject_below` | `LIKELY_FALSE` | "judge: likely false alarm"; bottom of the review queue (still visible, still reviewable) |
| in between | `UNSURE` | normal priority, a person decides |

Fire/smoke (ViT + both LLMs). Each LLM answers `CONFIRMED`, `REJECTED` or `UNSURE`:

| ViT ensemble | Opus 5.5 | Sonnet 5.5 | Verdict |
|---|---|---|---|
| any | CONFIRMED | CONFIRMED | `CONFIRMED` |
| < `reject_below` | REJECTED | REJECTED | `LIKELY_FALSE` (flagged only: **never down-ranked**, a person must look) |
| anything else (disagreement, UNSURE, an LLM error or timeout) | | | `UNSURE` |

An LLM failure (no network, refusal, invalid JSON) never blocks the incident: that judge counts as `UNSURE`.

### 3.3 The LLM panel (fire/smoke only)
- **Models:** Claude Opus 5.5 (`claude-opus-5-5`) sees the hazard crop at full resolution; Claude Sonnet 5.5
  (`claude-sonnet-5-5`) sees the full frame. Different model, different view: their mistakes overlap less.
- **Input:** the claim ("fire detected in zone X"), the image(s), and the instruction to judge only what is visible.
  Output by structured outputs: `{verdict: CONFIRMED|REJECTED|UNSURE, confidence: 0-1, lookalike: str|null, reason: str}`.
- **Settings:** low effort (a visual check, not a long reasoning task), server-side refusal fallback enabled
  (`fallbacks: "default"`), timeout ~20 s; both called in parallel.
- **Cost:** about $0.04 (Opus) + $0.02 (Sonnet) per hazard incident, estimated for one crop or frame and a short answer. Hazards are rare, so this is cents per
  day per camera; PPE incidents cost nothing.
- **Privacy:** hazard frames are sent to the Anthropic API. PPE images never leave the site. Put this in the DPIA.

### 3.4 The ViT ensemble (all incidents with a judge)
- Three models, one 7-class head each (`helmet, no_helmet, vest, no_vest, fire, smoke, none`), 224 px crops, running
  locally (Apple GPU / CUDA / CPU). Only the classes of the check are compared at run time.
- Trained on crops cut from the Milestone 0 combined dataset (section 5). Files: `milestone0/runs/judge/<arch>/best.pt`
  (git-ignored; register them in M8 like the YOLO weights).

## 4. Storage (M3 `incidents.db`)
New table, one row per incident version (re-judged when M3 updates the incident, e.g. a vest event joins a helmet incident):
```sql
CREATE TABLE assessments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  incident_id TEXT NOT NULL, incident_version TEXT NOT NULL,          -- incident updated_at
  created_at TEXT NOT NULL,
  verdict TEXT NOT NULL,          -- CONFIRMED | LIKELY_FALSE | UNSURE | NO_JUDGE | NOT_APPLICABLE
  judge_mode TEXT NOT NULL,       -- vit | vit+llm | none
  judge_reason TEXT,              -- one line per item, e.g. "helmet: P(missing) 0.94 -> CONFIRMED"
  vit TEXT,                       -- JSON: per check, per model and ensemble scores, thresholds used
  llm TEXT,                       -- JSON: per model verdict, confidence, lookalike, reason, tokens, cost, latency
  severity TEXT, risk_score INTEGER, risk_factors TEXT,              -- from the risk rules (unchanged)
  summary TEXT, recommended_actions TEXT, citations TEXT, grounded INTEGER, explanation_mode TEXT,
  model_versions TEXT,            -- JSON: ViT checkpoints, LLM model ids, knowledge-base version
  UNIQUE(incident_id, incident_version));
```
The latest verdict and severity are also copied to `incidents` (`judge_verdict`, `severity`, `risk_score`,
`assessed_version`; an older version never overwrites a newer one, and recording an assessment does not change
`updated_at`) so the list can filter (`judge_verdict=`) and sort on them: `sort=priority` orders by risk, with PPE
`LIKELY_FALSE` last and fire/smoke never moved down. Unannotated evidence frames are stored next to the reviewer copies
(`raw_evidence`: uri, event_id, frame_id; `keep_raw_evidence: true`); M6 never exposes them. Older databases are
migrated on start (`ALTER TABLE ADD COLUMN`). Written through `POST /incidents/{id}/assessment` (M3 stays the only writer).
Reviewer decisions (`labels`) next to judge verdicts give the judge's real precision over time, and become training data.

## 5. ViT training (Milestone 0)
```bash
cd milestone0
python scripts/10_build_judge_crops.py      # crops -> data/processed/judge_crops/{train,val,test}/<class>/
python scripts/11_train_vit_judges.py       # 3 models -> runs/judge/<arch>/best.pt
python scripts/12_eval_vit_judges.py        # -> reports/vit_judges_report.md, runs/judge/thresholds.yaml
python scripts/13_check_runtime_head_crops.py   # head crops cut from person boxes, as the service does
```
Results and thresholds: `milestone0/reports/vit_judges_report.md` (summary in the playbook, section 6c).

Results (2026-11-16, test crops, ensemble of the three; per-model numbers in the report):

| Check | AUC | reject_below | confirm_above | Real claims kept | Real claims confirmed | False claims rejected | False claims confirmed | Unsure |
|---|---|---|---|---|---|---|---|---|
| helmet | 0.9992 | 0.34 | 0.58 | 99.2 % | 98.5 % | 98.0 % | 0.8 % | 1.0 % |
| vest | 0.982 | 0.22 | 0.75 | 96.7 % | 91.0 % | 89.5 % | 2.2 % | 6.7 % |
| fire/smoke | 0.9998 | 0.78 | 0.78 | 98.9 % | 98.9 % | 99.6 % | 0.4 % | 0 % |

Threshold policy: `reject_below` keeps >= 98 % of real claims on val (fire/smoke: 99.5 %, since a missed fire costs far
more); `confirm_above` is the lowest score with val precision >= 98 %. Each model alone: 93.7-94.3 % 7-class test
accuracy; the ensemble 94.7 %, and the best or equal-best AUC on every check. Latency ~0.1 s per incident (3 crops,
three models, Apple M4 GPU).

**Run-time head crops (script 13).** The service has no head box, only the person box. On 1,141 test-split persons with
one labelled head, a head square of `min(width, 0.3 × height)` + 25 % (head and shoulders in waist-up framing) gave AUC
0.975; `min(0.6 × width, 0.3 × height)` + 15 % gives **0.987**, keeps 99.2 % of real no-helmet claims, rejects 87 % of
false ones and leaves 5 % unsure, with CONFIRMED precision ~95 % (98 % on head boxes). That rule is used. On the demo
clip it moved the five real no-helmet incidents from 0.53-0.97 to 0.66-0.98, all CONFIRMED.

## 6. What changed per module (done 2026-11-16)
| Module | Change |
|---|---|
| M3 incident service | `assessments` table + `judge_verdict`/`severity`/`risk_score` columns; `POST /incidents/{id}/assessment`; save an **unannotated** copy of each evidence frame and the boxes as JSON (the judge must not see the drawn rectangles); list filter/sort by verdict. |
| M4 → assessment service | Add crops, ViT ensemble, LLM panel, verdict logic; keep risk + SOP explanation; write to M3 instead of `agent.db` / `analyses.jsonl`. |
| M5 notification service | **Removed** (code, configs, docs). |
| M6 gateway | Drop `notify_url`, `/notifications`, `/notifications/requeue-dead` and the `notifications:*` permissions; read the assessment from M3 instead of M4. |
| M7 dashboard | Remove the notifications panel and retry button (Admin); show the judge verdict (badge + scores + LLM reasons) and sort the review queue by it. |
| M8 MLOps | Register the three ViT checkpoints and thresholds; retrain from `labels` once a few hundred reviewed incidents exist. |
| M9 ops / scripts | Remove M5 from docker-compose, Prometheus, Grafana, CI, tests and `dev_stack.sh`; add the assessment service's metrics (verdict counts, judge latency, LLM cost). |

## 7. Known limits
- The ViTs were tested on crops of dataset boxes, not on the detector's own mistakes; real false alarms can be harder.
  Re-measure on reviewed incidents (M3 `labels` next to `assessments`).
- Run-time head crops come from the person box: helmet AUC 0.987 vs 0.999 on labelled head boxes, CONFIRMED precision
  ~95 % rather than the 98 % the thresholds were chosen for. Choosing the helmet thresholds on run-time crops (or adding
  M1's helmet/no_helmet box to the event) would close the gap.
- One box per event (the confirming frame's) is used for all three evidence frames; fast movement shifts the crop.
- No judge for gloves, goggles and mask yet (too few or too small examples in Milestone 0).
- Vest negatives come from two sources only (construction, sh17); the vest check is the least certain (UNSURE on 2 of
  the 5 vest claims in the demo clip).
- The LLM panel has only been tested with stubbed SDK responses. Without `ANTHROPIC_API_KEY` and `--panel anthropic`,
  every fire/smoke verdict is UNSURE (by design: a person decides). In docker-compose it also needs an egress proxy.

## 8. Build plan
1. ~~M3: `assessments` table, columns, endpoint, unannotated evidence copy (+ tests).~~ Done: 7 new tests (21 total).
2. ~~Assessment service: crops + ViT ensemble + verdict logic for PPE (+ tests with stub models).~~ Done.
3. ~~LLM panel for fire/smoke (+ tests with stubbed SDK responses)~~ Done (31 tests in the service);
   **still open: one live check with an API key** (`export ANTHROPIC_API_KEY=...; JUDGE_PANEL=anthropic dev_stack.sh start clip`).
4. ~~M6 + M7: read the assessment from M3, show the verdict; remove notifications.~~ Done (gateway 14 tests, dashboard 11).
5. ~~Remove M5 everywhere; update `dev_stack.sh`, ops, CI and the testing guides; full clip run end to end.~~ Done:
   clip run 2026-11-16, 8 incidents, all assessed in M3 and shown in the dashboard (queue sorted by priority, judge
   scores on the incident page).
