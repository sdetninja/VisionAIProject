"""Small HTTP API of the assessment service, served by the worker process (`python -m agent_service --port 8004`).
Internal only: M6 reads assessments from M3, not from here."""


def create_app(service):
    from fastapi import FastAPI, HTTPException
    from .m3client import M3Error
    app = FastAPI(title="VisionGuard Assessment Service")
    try:    # M9: /metrics + request ids + JSON access logs. Optional so the service still runs without the ops package.
        from vg_obs import instrument
        _reg = instrument(app, "assessment")
        from vg_obs.collectors import register_assessment
        register_assessment(service, _reg)
    except ImportError:
        pass

    @app.get("/health")
    def health():
        return {"status": "ok", **service.counters, "verdicts": service.verdicts, "judge": service.judge.versions()}

    @app.post("/assess/{iid}")
    def assess(iid: str, force: bool = False):
        """Judge one incident now (e.g. after training new judges: force=true re-judges the current version)."""
        try:
            return service.assess(iid, force)
        except M3Error as e:
            raise HTTPException(e.status if e.status in (404, 503) else 502, str(e))

    @app.get("/search")
    def search(q: str, k: int = 4):
        return [{"id": c.id, "source": c.source, "heading": c.heading, "score": s, "text": c.text[:400]}
                for c, s in service.retriever.search(q, min(k, 10))]
    return app
