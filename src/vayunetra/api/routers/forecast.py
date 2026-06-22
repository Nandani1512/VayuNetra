from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import text

from vayunetra.api.schemas import ForecastCellResponse
from vayunetra.serving.tile_cache import TileKey, get_or_build_tile
from vayunetra.storage.db import get_engine

router = APIRouter(prefix="/forecast", tags=["forecast"])


@router.get("")
async def forecast_geojson(
    city: str = Query(...),
    pollutant: str = Query("pm25"),
    horizon: int = Query(24, ge=1, le=72),
):
    """Returns the latest forecast as a GeoJSON FeatureCollection for the given
    (city, pollutant, horizon). Cached in Redis for ~1h."""
    ts_issued = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    key = TileKey(city=city, pollutant=pollutant, horizon_h=horizon, ts_issued=ts_issued)
    try:
        gj = await get_or_build_tile(key, ttl_s=3600)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"tile_build_failed: {e}") from e
    if not gj.get("features"):
        # Try the most recent existing ts_target in DB so a stale prediction
        # still renders something instead of an empty map on the demo.
        sql = text(
            """
            SELECT MAX(ts_target) AS ts_max FROM forecast
            WHERE city_id=:city AND pollutant=:pollutant
            """
        )
        with get_engine().begin() as conn:
            row = conn.execute(sql, {"city": city, "pollutant": pollutant}).fetchone()
        if row and row.ts_max:
            ts_issued = row.ts_max - pd.Timedelta(hours=horizon)
            key = TileKey(city=city, pollutant=pollutant, horizon_h=horizon, ts_issued=ts_issued)
            gj = await get_or_build_tile(key, ttl_s=3600)
    return JSONResponse(gj)


@router.get("/cell", response_model=ForecastCellResponse)
def forecast_cell(
    city: str,
    cell_id: str,
    pollutant: str = "pm25",
    horizon: int = 24,
):
    sql = text(
        """
        SELECT cell_id, ts_target, p10, p50, p90, model_version
        FROM forecast
        WHERE city_id = :city AND cell_id = :cell_id AND pollutant = :pollutant
        ORDER BY ts_issued DESC, ts_target DESC LIMIT 1
        """
    )
    with get_engine().begin() as conn:
        row = conn.execute(sql, {"city": city, "cell_id": cell_id, "pollutant": pollutant}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="forecast not found")

    # 24h of past observations from nearest station, for the chart.
    hist_sql = text(
        """
        SELECT o.ts, o.value
        FROM observation o JOIN station s ON s.id = o.station_id
        WHERE s.city_id = :city
          AND o.pollutant = :pollutant
          AND o.ts > now() - interval '24 hours'
        ORDER BY o.ts ASC
        LIMIT 200
        """
    )
    with get_engine().begin() as conn:
        history = [
            {"ts": r.ts.isoformat(), "value": float(r.value)}
            for r in conn.execute(hist_sql, {"city": city, "pollutant": pollutant})
        ]

    return ForecastCellResponse(
        city=city,
        cell_id=cell_id,
        pollutant=pollutant,
        horizon_h=horizon,
        ts_target=row.ts_target,
        p10=float(row.p10) if row.p10 is not None else None,
        p50=float(row.p50) if row.p50 is not None else None,
        p90=float(row.p90) if row.p90 is not None else None,
        history=history,
    )
