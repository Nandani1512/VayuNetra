from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    services: dict[str, str]


class ForecastCellResponse(BaseModel):
    city: str
    cell_id: str
    pollutant: str
    horizon_h: int
    ts_target: datetime
    p10: float | None
    p50: float | None
    p90: float | None
    history: list[dict[str, Any]] = Field(default_factory=list)


class AttributionResponse(BaseModel):
    city: str
    cell_id: str
    ts: str
    pollutant: str
    blended_sources: dict[str, float]
    shap_sources: dict[str, float]
    overlay_sources: dict[str, float]
    overlay_evidence: dict[str, Any]
    trajectory_geojson: dict[str, Any]
    wind_speed_ms: float | None
    wind_bearing_from_deg: float | None
    confidence: float


class EnforceItem(BaseModel):
    cluster: dict[str, Any]
    attribution: dict[str, Any] | None = None
    brief: str | None = None
    llm: str | None = None


class EnforceResponse(BaseModel):
    city: str
    pollutant: str
    horizon_h: int
    total_cells: int
    hot_cells: int
    n_clusters: int
    items: list[EnforceItem]


class AdvisoryResponse(BaseModel):
    city: str
    lang: str
    severity: str
    headline: str
    advice: str
    aqi_p50: float
    pollutant: str
    issued_at: datetime
    vuln_tier: str = "general"
    citation_source: str | None = None
    citation_text: str | None = None
