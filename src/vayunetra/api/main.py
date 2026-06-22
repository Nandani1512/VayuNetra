"""FastAPI app — VayuNetra serving layer.

Routers:
  /healthz, /forecast, /forecast/cell, /attribution, /enforce, /advisory.
Also serves the static frontend at / for an all-in-one demo.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from vayunetra.api.routers import advisory, attribution, enforce, forecast, health
from vayunetra.common.config import get_settings
from vayunetra.common.logging import configure_logging

configure_logging()
settings = get_settings()

app = FastAPI(
    title="VayuNetra API",
    version="0.1.0",
    description="Geospatial air-quality intelligence platform",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(forecast.router)
app.include_router(attribution.router)
app.include_router(enforce.router)
app.include_router(advisory.router)


_FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"

if _FRONTEND_DIR.exists():
    # Static assets — JS/CSS — at /static, index.html at /.
    app.mount("/static", StaticFiles(directory=str(_FRONTEND_DIR)), name="static")

    @app.get("/")
    def root() -> FileResponse:
        return FileResponse(str(_FRONTEND_DIR / "index.html"))
