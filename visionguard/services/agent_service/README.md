# M4 – Agent + RAG Service (advisory only)

Consumes M3 `incident.created/updated.v1`, produces `incident.analyzed.v1`:
severity + risk score + summary + recommended actions, **grounded in your own SOP documents**.

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

## You must do
- **Replace `knowledge_base/*.md` (SAMPLE content) with your real SOPs/policies.** Format: optional `source: ID` header, `#` headings = chunks. Citations are only as good as these documents.
- Tune `configs/risk.example.yaml` weights/thresholds with your safety officer.

## Limits
- Retrieval is lexical BM25 (no network/embeddings needed). `QdrantRetriever` is a placeholder for semantic search once the KB grows.
- Plain ordered pipeline rather than LangGraph (no branching needed yet). `AnthropicLLM` is written but untested (no network here); model name is a default you may change.
- Tested: 11 tests (retrieval, scoring, guardrails, fallbacks, caching, bus handling) passing here with a minimal runner; the FastAPI app and `pytest` itself were not run. Also ran M3-format JSONL → analysis end to end.
