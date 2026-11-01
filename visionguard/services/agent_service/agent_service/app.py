"""uvicorn agent_service.app:create_app --factory --port 8004   (env: KB_DIR, RISK_CONFIG, LLM_KIND, AGENT_DB). Fronted by M6."""
import os
from typing import Optional


def create_app(service=None):
    from fastapi import FastAPI, HTTPException
    from .factory import build
    service = service or build(os.environ.get("KB_DIR", "knowledge_base"), os.environ.get("RISK_CONFIG"),
                               os.environ.get("LLM_KIND", "template"), os.environ.get("AGENT_DB", ":memory:"))
    app = FastAPI(title="VisionGuard Agent Service")
    try:    # M9: /metrics + request ids + JSON access logs. Optional so the service still runs without the ops package.
        from vg_obs import instrument
        _reg = instrument(app, "agent")
        from vg_obs.collectors import register_agent
        register_agent(service, _reg)
    except ImportError:
        pass

    @app.get("/health")
    def health():
        return {"status": "ok", **service.counters}

    @app.post("/analyze")
    def analyze(inc: dict, force: bool = False):
        try:
            return service.analyze(inc, force)
        except (ValueError, KeyError) as e:
            raise HTTPException(422, f"invalid incident: {e}")

    @app.get("/analyses/{iid}")
    def get(iid: str):
        a = service.store.get(iid)
        if not a:
            raise HTTPException(404, "no analysis")
        return a

    @app.get("/search")
    def search(q: str, k: int = 4):
        return [{"id": c.id, "source": c.source, "heading": c.heading, "score": s, "text": c.text[:400]}
                for c, s in service.retriever.search(q, min(k, 10))]
    return app
