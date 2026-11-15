# M4 – Agent + RAG Service (advisory only)

Consumes M3 `incident.created/updated.v1`, produces `incident.analyzed.v1`:
severity + risk score + summary + recommended actions, **grounded in safety standards and your site procedures** (see Knowledge base).

```
incident ─► risk.py (deterministic score/severity, audit trail)
         ─► kb.py   (BM25 over knowledge_base/*.md, chunked by heading)
         ─► llm.py  (writes summary/actions from facts + passages; Template fallback)
         ─► guardrails.py (valid JSON, citations ⊆ retrieved, no severity contradiction) ─► else template fallback
```

## Safety design
- **Severity is never LLM-decided.** `risk.py` computes it from rules/weights in config; the LLM only phrases an explanation and is rejected if it contradicts the severity.
- Output is always `advisory_only: true`; nothing is enforced automatically.
- Any LLM failure (API down, bad JSON, invented citation) → deterministic template; the incident is never blocked.
- No matching SOP passage → `grounded: false`, `needs_human_review: true` (no made-up policy).
- Idempotent per `(incident_id, incident.updated_at)`; repeat-pattern bonus counts similar incidents (same camera/zone/type) in the last 24h.

## Run
```bash
pip install -r requirements.txt && pytest -q
python -m agent_service --bus ../incident_service/data/bus/events.jsonl --kb knowledge_base --out data/analyses.jsonl [--follow]
KB_DIR=knowledge_base uvicorn agent_service.app:create_app --factory --port 8004   # POST /analyze, GET /analyses/{id}, GET /search?q=
# real LLM: pip install anthropic; export ANTHROPIC_API_KEY=...; --llm anthropic  (or LLM_KIND=anthropic)
```

### Sample: analyse M3 incidents with Claude
The bus file is written by M3, so run M3 first (from `incident_service/`):
```bash
python -m incident_service --config configs/incident.example.yaml --input ../rule_engine/out/events.jsonl
```
Then, from `agent_service/` with `ANTHROPIC_API_KEY` exported and `pip install "anthropic>=2.6"`:
```bash
python -m agent_service --bus ../incident_service/data/bus/events.jsonl --kb knowledge_base --risk-config configs/risk.example.yaml --llm anthropic
```
Results go to `data/analyses.jsonl` (one `incident.analyzed.v1` record per line; the analysis is under `payload`).
Check `explanation_mode`: `llm` = Claude wrote it, `template` = no API key found, `template_fallback` = the call failed or
a guardrail rejected the answer (reason in `guardrail_problems`). The console only prints severity per incident; read the
summary and actions from the file:
```bash
python -c "import json;[print(json.dumps(json.loads(l)['payload'],indent=2)) for l in open('data/analyses.jsonl')]"
```

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
- Retrieval is lexical BM25 (no network/embeddings needed). `QdrantRetriever` is a placeholder for semantic search once the KB grows.
- Plain ordered pipeline rather than LangGraph (no branching needed yet). `AnthropicLLM` defaults to `claude-sonnet-5-5` at `effort: low` with server-side refusal fallback (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`; needs `anthropic` ≥ 2.6). It was checked offline against stubbed SDK responses only and has not made a live API call yet; refusals, truncation or no text all fall back to the template.
- Tested: 12 tests with `pytest` (retrieval on the real knowledge base, scoring, guardrails, fallbacks, caching, bus handling). Also ran a live M1-M4 clip run and replayed its M3 bus against the current knowledge base: all 8 incidents grounded.
