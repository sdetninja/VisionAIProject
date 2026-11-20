# M4 – Assessment service (judge + risk + explanation; advisory only)

Architecture v3 (`../../ARCHITECTURE_V3_ASSESSMENT.md`). Package name is still `agent_service`. For every
`incident.created/updated.v1` on M3's bus it fetches the incident from M3, judges it, scores it, explains it, and
writes **one assessment per incident version into M3** (`POST /incidents/{id}/assessment` → `assessments` table).
No database of its own; M5 (notifications) is removed.

```
M3 bus ─► fetch incident + unannotated frames from M3 (port 8003)
        ─► judge/      PPE (helmet, vest):  ViT ensemble (ViT-S, DINOv2-S, Swin-T), local
                       fire / smoke:        ViT ensemble + LLM panel (Claude Opus 5.5 on the crop, Claude Sonnet 5.5 on the frame)
                       gloves/goggles/mask: NO_JUDGE      restricted area: NOT_APPLICABLE
        ─► risk.py     deterministic severity + score (unchanged)
        ─► kb.py + llm.py + guardrails.py   SOP search + template (or Claude) explanation (unchanged)
        ─► POST /incidents/{id}/assessment to M3
```

## Judge
| File | What |
|---|---|
| `judge/crops.py` | crops from the event box: head square `min(0.6 w, 0.3 h)` at the top of the person box +15 %; person box +5 % (vest); fire/smoke box +15 % |
| `judge/vit.py` | loads the three checkpoints from `milestone0/runs/judge/thresholds.yaml`; claim probability renormalised over the check's classes; ensemble = mean |
| `judge/panel.py` | the two Claude judges, in parallel, structured outputs `{verdict, confidence, lookalike, reason}`, low effort, `fallbacks: "default"`; any failure = that judge `UNSURE` |
| `judge/__init__.py` | verdict rules (below); a broken judge gives `NO_JUDGE`, never blocks the incident |

| Incident | Verdict |
|---|---|
| PPE item | ensemble mean over the frames: ≥ `confirm_above` → CONFIRMED, < `reject_below` → LIKELY_FALSE, else UNSURE (thresholds from `thresholds.yaml`) |
| grouped PPE (helmet + vest) | CONFIRMED if any item is; LIKELY_FALSE only if every item is; an unjudged item (gloves) keeps it UNSURE |
| fire / smoke | both LLMs CONFIRMED → CONFIRMED; ViT < `reject_below` **and** both LLMs REJECTED → LIKELY_FALSE (flagged, never down-ranked); else UNSURE. No API key → UNSURE |

## Safety design
- **Advisory only.** The judge never closes or hides an incident and never lowers severity; M3's `priority` sort moves only
  PPE `LIKELY_FALSE` to the bottom, never fire/smoke.
- **Severity is never model-decided.** `risk.py` computes it; the LLM only phrases an explanation and is rejected if it contradicts it.
- Any LLM or judge failure → template / `UNSURE` / `NO_JUDGE`; the incident is always assessed.
- No matching SOP passage → `grounded: false` (no made-up policy).
- Idempotent per `(incident_id, updated_at)`: a version M3 already has an assessment for is not judged again (`force` re-judges).
- Privacy: PPE images never leave the machine; fire/smoke frames go to the Anthropic API only with `--panel anthropic`.

## Run
```bash
pip install -r requirements.txt && pytest -q        # 31 tests, stub models (no torch needed for the tests)
python -m agent_service --bus ../incident_service/data/bus/events.jsonl --m3-url http://127.0.0.1:8003 \
    --kb knowledge_base --risk-config configs/risk.example.yaml \
    --judge ../../../milestone0/runs/judge/thresholds.yaml --port 8004 --follow
# fire/smoke LLM panel:  pip install "anthropic>=2.6"; export ANTHROPIC_API_KEY=...; add --panel anthropic
# Claude-written explanations: --llm anthropic
curl -s localhost:8004/health                          # counters, verdict counts, loaded judge versions
curl -s -X POST "localhost:8004/assess/INC-000001?force=true"   # re-judge now (e.g. after retraining the judges)
curl -s "localhost:8004/search?q=missing+helmet"       # what the explanation step retrieves
```
The result is read from M3: `curl -s localhost:8003/incidents/INC-000001` → `assessment`, and `judge_verdict`,
`severity`, `risk_score` on the incident. Without `--judge` (or if the file is missing) PPE incidents get `NO_JUDGE`.

## Knowledge base (`knowledge_base/`)
Plain-language summaries of the standards behind each detection, one `#` heading per searchable section (kept under
700 characters, the passage size the text writer sees). Each file has a `source:` ID (what citations show), the
official URL, and a note that it is a summary, not the official text. Sources checked 2026-11-15.

| Detection | File | `source:` | Based on |
|---|---|---|---|
| any missing PPE | `ppe_general_osha_1910_132.md` | OSHA-1910.132 | hazard assessment, provision at no cost, training/retraining |
| missing helmet | `head_protection_helmet_osha_1910_135.md` | OSHA-1910.135 | hard hat rule; ANSI/ISEA Z89.1 types and classes; inspection |
| missing gloves | `hand_protection_gloves_osha_1910_138.md` | OSHA-1910.138 | hand protection; selection; ANSI/ISEA 105 cut levels |
| missing goggles | `eye_face_protection_goggles_osha_1910_133.md` | OSHA-1910.133 | eye/face protection; welding shades; ANSI/ISEA Z87.1 |
| missing vest | `high_visibility_vest_ansi_isea_107.md` | ANSI-ISEA-107 | via the 1910.132(d) hazard assessment and 1926.201; ANSI/ISEA 107-2020 types/classes |
| mask | `respiratory_protection_mask_osha_1910_134.md` | OSHA-1910.134 | respirator program, fit testing |
| fire | `fire_detected_emergency_osha_1910_38_165.md` | OSHA-1910.38 | emergency action plan, alarms (1910.165), evacuation |
| smoke | `smoke_detected_response.md` | OSHA-1910.38-SMOKE | verify and report; smouldering after hot work; incipient-stage extinguisher use |
| fire prevention | `fire_prevention_extinguishers_osha_1910_39_157.md` | OSHA-1910.39 | fire prevention plan; extinguisher distances, inspection, training |
| hot work | `hot_work_welding_fire_osha_1910_252.md` | OSHA-1910.252 | permits, 35 ft rule, 30 min fire watch |
| what to do on site | `site_ppe_violation_response.md` | SITE-PPE-RESPONSE | **template** site procedure: stop and correct, repeats, records |
| restricted area | `sop_restricted.md` | SOP-ZONE-02 | sample site procedure (no standard added yet) |

- The ANSI/ISEA and NFPA standards are copyrighted: the files give only facts (editions, types, classes), not their text.
- Check that the right sections come back for an incident type: `curl -s "localhost:8004/search?q=missing+helmet"`.
- Adding a file: `source:` header, short `#` sections, and use the words the query uses (`missing helmet`,
  `fire`, `smoke`, ...): retrieval is keyword based. Rule-id letters such as the `R` in `R-VEST` are ignored.

## You must do
- **Adapt `site_ppe_violation_response.md` and `sop_restricted.md` to the site and have the safety officer approve
  them**, and add the site's own emergency action plan / fire prevention plan sections. The OSHA files are US federal
  rules; for another country, replace them with the local regulations (for example UK HSE, EU directives).
- Tune `configs/risk.example.yaml` weights/thresholds with your safety officer.

## Limits
- The ViTs reach AUC 0.999 (helmet) on labelled head boxes but 0.987 on head crops cut from person boxes, as at run
  time (`milestone0/scripts/13_check_runtime_head_crops.py`): there, CONFIRMED precision is ~95 % rather than 98 %.
  The vest check is the weakest (AUC 0.982). Re-measure on reviewed incidents (M3 `labels`).
- The judge sees the event's box from the confirming frame on all three evidence frames (the event carries one box).
- The LLM panel was checked against stubbed SDK responses only; it has not made a live API call yet.
- Retrieval is lexical BM25; `AnthropicLLM` (explanations) defaults to `claude-sonnet-5-5` at `effort: low`.
- Tested 2026-11-16: 31 tests (judge rules, crops, panel parsing, fake-M3 end to end, idempotency, failures) and a
  live clip run: 8 incidents, all assessed and stored in M3; PPE judged by the real ViT ensemble (~75 ms per incident on
  the M4 GPU, 3 s model load), fire/smoke UNSURE without an API key.
