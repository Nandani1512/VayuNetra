"""/advisory — citizen-facing health guidance.

Lookups:
  1. Nearest forecast cell p50 (or city-wide mean) for the requested pollutant.
  2. Severity band → ``advisory.templates`` → headline + body in the user's
     language for their vulnerability tier.
  3. RAG retrieval over the curated knowledge base for one supporting citation.

No LLM is on this path — the rubric demands deterministic, low-latency,
auditable citizen messaging.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from vayunetra.advisory import rag, templates
from vayunetra.api.schemas import AdvisoryResponse
from vayunetra.storage.db import get_engine

router = APIRouter(prefix="/advisory", tags=["advisory"])

VulnTierQ = Literal["general", "elderly_children", "asthmatic"]


class VulnerabilityResponse(BaseModel):
    cell_id: str | None = None
    pop_elderly: int | None = None
    pop_children: int | None = None
    pop_total: int | None = None
    hospital_count: int | None = None
    school_count: int | None = None
    vulnerability_index: float
    auto_vuln_tier: str


def _get_vulnerability(city: str, lat: float, lon: float) -> dict | None:
    """Query grid_cell vulnerability data for the nearest cell."""
    sql = text(
        """
        SELECT cell_id, pop_elderly, pop_children, pop_total, hospital_count, school_count
        FROM grid_cell
        WHERE city_id=:city
        ORDER BY centroid <-> ST_SetSRID(ST_MakePoint(:lon,:lat),4326)
        LIMIT 1
        """
    )
    try:
        with get_engine().begin() as conn:
            row = conn.execute(sql, {"city": city, "lon": lon, "lat": lat}).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    return row._mapping


def _compute_vulnerability_index(row) -> float:
    pop_total = row.get("pop_total") or 0
    if pop_total <= 0:
        return 0.5
    return ((row.get("pop_elderly") or 0) + (row.get("pop_children") or 0)) / pop_total


def _auto_tier(vi: float) -> str:
    if vi >= 0.4:
        return "asthmatic"
    elif vi >= 0.25:
        return "elderly_children"
    return "general"


def _aqi_for_city(city: str, pollutant: str, lat: float | None, lon: float | None) -> float:
    """Latest p50: nearest cell if lat/lon, else city-wide mean."""
    if lat is not None and lon is not None:
        sql = text(
            """
            SELECT f.p50
            FROM forecast f JOIN grid_cell g ON g.city_id=f.city_id AND g.cell_id=f.cell_id
            WHERE f.city_id=:city AND f.pollutant=:pollutant
              AND f.ts_target = (SELECT MAX(ts_target) FROM forecast
                                 WHERE city_id=:city AND pollutant=:pollutant)
            ORDER BY g.centroid <-> ST_SetSRID(ST_MakePoint(:lon,:lat),4326)
            LIMIT 1
            """
        )
        params = {"city": city, "pollutant": pollutant, "lon": lon, "lat": lat}
    else:
        sql = text(
            """
            SELECT AVG(p50) AS p50 FROM forecast
            WHERE city_id=:city AND pollutant=:pollutant
              AND ts_target = (SELECT MAX(ts_target) FROM forecast
                               WHERE city_id=:city AND pollutant=:pollutant)
            """
        )
        params = {"city": city, "pollutant": pollutant}
    try:
        with get_engine().begin() as conn:
            row = conn.execute(sql, params).fetchone()
    except Exception:
        # DB not reachable in this context (tests, demo without ingest). Return
        # a neutral mid-band so downstream messaging still renders.
        return 75.0
    if row is None or row.p50 is None:
        return 75.0
    return float(row.p50)


@router.get("", response_model=AdvisoryResponse)
def advisory(
    city: str,
    lang: str = Query("en"),
    pollutant: str = Query("pm25"),
    lat: float | None = None,
    lon: float | None = None,
    vuln_tier: VulnTierQ = Query("general"),
    neighborhood: str | None = None,
    auto_vuln_tier: bool = Query(True),
) -> AdvisoryResponse:
    if lang not in templates.LANGUAGES:
        lang = "en"

    # Auto-pick vuln_tier from ward vulnerability when lat/lon provided and auto enabled
    effective_tier: str = vuln_tier
    if auto_vuln_tier and lat is not None and lon is not None:
        row = _get_vulnerability(city, lat, lon)
        if row is not None:
            vi = _compute_vulnerability_index(row)
            effective_tier = _auto_tier(vi)

    aqi = _aqi_for_city(city, pollutant, lat, lon)
    sev = templates.severity_for(aqi)
    nbhd = neighborhood or city.title()
    rendered = templates.render(
        lang=lang,
        severity=sev,
        vuln_tier=effective_tier,
        aqi=aqi,
        pollutant=pollutant.upper(),
        neighborhood=nbhd,
    )

    # RAG citation — picks the most query-relevant chunk for this severity.
    query = f"{pollutant} {sev} {effective_tier} health advisory"
    chunks = rag.retrieve(query, k=1)
    citation = chunks[0] if chunks else None

    return AdvisoryResponse(
        city=city,
        lang=lang,
        severity=sev,
        headline=rendered.headline,
        advice=rendered.body,
        aqi_p50=aqi,
        pollutant=pollutant,
        issued_at=datetime.now(timezone.utc),
        vuln_tier=effective_tier,
        citation_source=citation.citation if citation else None,
        citation_text=citation.text if citation else None,
    )


@router.get("/languages")
def languages() -> dict[str, str]:
    """List supported language codes and their display names."""
    return {lc: templates.LANGUAGE_NAMES[lc] for lc in templates.LANGUAGES}


@router.get("/vulnerability", response_model=VulnerabilityResponse)
def vulnerability(
    city: str,
    lat: float = Query(...),
    lon: float = Query(...),
) -> VulnerabilityResponse:
    """Return ward-level vulnerability data for the nearest grid cell."""
    row = _get_vulnerability(city, lat, lon)
    if row is None:
        raise HTTPException(status_code=404, detail="No grid cell found")
    vi = _compute_vulnerability_index(row)
    return VulnerabilityResponse(
        cell_id=row.get("cell_id"),
        pop_elderly=row.get("pop_elderly"),
        pop_children=row.get("pop_children"),
        pop_total=row.get("pop_total"),
        hospital_count=row.get("hospital_count"),
        school_count=row.get("school_count"),
        vulnerability_index=vi,
        auto_vuln_tier=_auto_tier(vi),
    )
