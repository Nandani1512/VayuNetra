"""Open-Meteo weather ingestion. No API key needed.

For every station in the city, fetches hourly weather from the forecast API
covering [now - past_days, now + forecast_days]. Wind is decomposed into u/v
(meteorological convention: u = eastward, v = northward).
"""

from __future__ import annotations

import math
from typing import Any

import httpx
from prefect import flow, get_run_logger, task
from sqlalchemy import text
from tenacity import retry, stop_after_attempt, wait_exponential

from vayunetra.storage.db import session_scope

BASE_URL = "https://api.open-meteo.com/v1/forecast"

HOURLY_VARS = [
    "temperature_2m",
    "relativehumidity_2m",
    "windspeed_10m",
    "winddirection_10m",
    "boundary_layer_height",
    "precipitation",
]


def _wind_to_uv(speed: float | None, direction_deg: float | None) -> tuple[float | None, float | None]:
    if speed is None or direction_deg is None:
        return None, None
    rad = math.radians(direction_deg)
    # meteorological direction is the direction wind is coming *from*;
    # convert to vector (going to).
    u = -speed * math.sin(rad)
    v = -speed * math.cos(rad)
    return u, v


@task
def list_stations(city: str) -> list[dict]:
    sql = text(
        """
        SELECT id, ST_X(geom) AS lon, ST_Y(geom) AS lat
        FROM station
        WHERE city_id = :city
        """
    )
    with session_scope() as s:
        return [dict(r._mapping) for r in s.execute(sql, {"city": city}).all()]


@retry(stop=stop_after_attempt(5), wait=wait_exponential(min=1, max=20))
async def _get(client: httpx.AsyncClient, params: dict[str, Any]) -> dict:
    r = await client.get(BASE_URL, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


@task
async def fetch_for_station(station: dict, past_days: int, forecast_days: int) -> list[dict]:
    params = {
        "latitude": station["lat"],
        "longitude": station["lon"],
        "hourly": ",".join(HOURLY_VARS),
        "past_days": past_days,
        "forecast_days": forecast_days,
        "timezone": "UTC",
    }
    async with httpx.AsyncClient() as client:
        data = await _get(client, params)
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    out = []
    for i, t in enumerate(times):
        speed = (hourly.get("windspeed_10m") or [None])[i] if i < len(hourly.get("windspeed_10m", [])) else None
        direc = (hourly.get("winddirection_10m") or [None])[i] if i < len(hourly.get("winddirection_10m", [])) else None
        u, v = _wind_to_uv(speed, direc)
        out.append(
            {
                "station_id": station["id"],
                "ts": t + "+00:00",
                "temp_c": _safe(hourly.get("temperature_2m"), i),
                "wind_u": u,
                "wind_v": v,
                "pbl_m": _safe(hourly.get("boundary_layer_height"), i),
                "rh_pct": _safe(hourly.get("relativehumidity_2m"), i),
                "precip_mm": _safe(hourly.get("precipitation"), i),
            }
        )
    return out


def _safe(arr, i):
    try:
        return arr[i]
    except (TypeError, IndexError):
        return None


@task
def upsert_weather(rows: list[dict]) -> int:
    if not rows:
        return 0
    stmt = text(
        """
        INSERT INTO weather (station_id, ts, temp_c, wind_u, wind_v, pbl_m, rh_pct, precip_mm)
        VALUES (:station_id, :ts, :temp_c, :wind_u, :wind_v, :pbl_m, :rh_pct, :precip_mm)
        ON CONFLICT (station_id, ts) DO UPDATE
        SET temp_c    = EXCLUDED.temp_c,
            wind_u    = EXCLUDED.wind_u,
            wind_v    = EXCLUDED.wind_v,
            pbl_m     = EXCLUDED.pbl_m,
            rh_pct    = EXCLUDED.rh_pct,
            precip_mm = EXCLUDED.precip_mm
        """
    )
    with session_scope() as s:
        for r in rows:
            s.execute(stmt, r)
    return len(rows)


@flow(name="ingest-open-meteo")
async def ingest_open_meteo(city: str, past_days: int = 2, forecast_days: int = 3) -> dict:
    log = get_run_logger()
    stations = list_stations(city)
    if not stations:
        log.warning("no stations for city=%s; run OpenAQ first", city)
        return {"rows": 0}
    total = 0
    for st in stations:
        rows = await fetch_for_station(st, past_days, forecast_days)
        total += upsert_weather(rows)
    log.info("open_meteo rows upserted: %d", total)
    return {"rows": total}
