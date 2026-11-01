"""FastAPI service exposing health, status, results, evidence frames, MJPEG preview and metrics.

Run:  CONFIG_PATH=configs/pipeline.webcam.yaml uvicorn vision_pipeline.app:app --port 8001
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .config import load_config
from .pipeline import VisionPipeline


def create_app(pipeline: VisionPipeline | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        p = pipeline or VisionPipeline(load_config(os.environ.get("CONFIG_PATH", "configs/pipeline.webcam.yaml")))
        app.state.pipeline = p
        p.start()
        yield
        p.stop()

    app = FastAPI(title="VisionGuard Vision Pipeline", version="0.1.0", lifespan=lifespan)

    def pl() -> VisionPipeline:
        return app.state.pipeline

    def worker(cid: str):
        w = pl().workers.get(cid)
        if w is None:
            raise HTTPException(404, f"unknown camera {cid}")
        return w

    @app.get("/health")
    def health():
        st = pl().status()
        ok = bool(st) and any(s["state"] == "running" for s in st)
        return {"status": "ok" if ok else "degraded", "cameras": st}

    @app.get("/cameras")
    def cameras():
        return pl().status()

    @app.get("/cameras/{camera_id}/latest")
    def latest(camera_id: str):
        snap = worker(camera_id).snapshot()
        if snap is None:
            raise HTTPException(404, "no results yet")
        return snap[1].model_dump(by_alias=True)

    @app.get("/cameras/{camera_id}/results")
    def results(camera_id: str, since_frame_id: int = Query(0, ge=0), limit: int = Query(100, le=1000)):
        rows = [r for r in list(worker(camera_id).history) if r.frame_id > since_frame_id][:limit]
        return [r.model_dump(by_alias=True) for r in rows]

    @app.get("/cameras/{camera_id}/frames/{frame_id}")
    def frame(camera_id: str, frame_id: int):
        jpg = pl().buffer.get(camera_id, frame_id)
        if jpg is None:
            raise HTTPException(404, "frame not in buffer (expired or unknown)")
        return Response(jpg, media_type="image/jpeg")

    @app.get("/cameras/{camera_id}/stream")
    def stream(camera_id: str, fps: float = 10.0, draw: bool = True):
        worker(camera_id)
        return StreamingResponse(pl().mjpeg_frames(camera_id, fps, draw),
                                 media_type="multipart/x-mixed-replace; boundary=frame")

    @app.get("/metrics")
    def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app() if os.environ.get("CONFIG_PATH") else None
