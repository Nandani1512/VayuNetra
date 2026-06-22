from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from vayunetra.api.schemas import HealthResponse
from vayunetra.storage.db import get_engine

router = APIRouter()


@router.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    services: dict[str, str] = {}
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        services["postgis"] = "ok"
    except Exception as e:
        services["postgis"] = f"fail: {e}"
    return HealthResponse(status="ok", services=services)
