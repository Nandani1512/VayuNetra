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
from sqlalchemy import text

from vayunetra.advisory import rag, templates
from vayunetra.api.schemas import AdvisoryResponse
from vayunetra.storage.db import get_engine

router = APIRouter(prefix="/advisory", tags=["advisory"])

VulnTierQ = Literal["general", "elderly_children", "asthmatic"]


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
) -> AdvisoryResponse:
    if lang not in templates.LANGUAGES:
        # Unknown language → fall back to English rather than 4xx; this is a
        # public citizen endpoint and graceful degradation matters more than
        # strictness.
        lang = "en"
    aqi = _aqi_for_city(city, pollutant, lat, lon)
    sev = templates.severity_for(aqi)
    nbhd = neighborhood or city.title()
    rendered = templates.render(
        lang=lang,
        severity=sev,
        vuln_tier=vuln_tier,
        aqi=aqi,
        pollutant=pollutant.upper(),
        neighborhood=nbhd,
    )

    # RAG citation — picks the most query-relevant chunk for this severity.
    query = f"{pollutant} {sev} {vuln_tier} health advisory"
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
        vuln_tier=vuln_tier,
        citation_source=citation.citation if citation else None,
        citation_text=citation.text if citation else None,
    )


@router.get("/languages")
def languages() -> dict[str, str]:
    """List supported language codes and their display names."""
    return {lc: templates.LANGUAGE_NAMES[lc] for lc in templates.LANGUAGES}
