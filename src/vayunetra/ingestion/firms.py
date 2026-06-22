"""NASA FIRMS active-fire ingestion.

CSV API: https://firms.modaps.eosdis.nasa.gov/api/area/csv/<KEY>/<SENSOR>/<bbox>/<day_range>
bbox = west,south,east,north
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

import httpx
from prefect import flow, get_run_logger, task
from sqlalchemy import text
from tenacity import retry, stop_after_attempt, wait_exponential

from vayunetra.common.config import get_settings
from vayunetra.ingestion.utils import city_bbox
from vayunetra.storage.db import session_scope


@retry(stop=stop_after_attempt(5), wait=wait_exponential(min=1, max=20))
async def _get(url: str) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.text


def _parse_ts(date_str: str, time_str: str) -> datetime | None:
    try:
        t = time_str.zfill(4)
        return datetime.strptime(f"{date_str} {t}", "%Y-%m-%d %H%M").replace(tzinfo=timezone.utc)
    except Exception:
        return None


@task
async def fetch_firms(city: str, sensor: str = "VIIRS_SNPP_NRT", day_range: int = 1) -> list[dict]:
    key = get_settings().firms_api_key
    if not key:
        raise RuntimeError("FIRMS_API_KEY not set")
    w, s, e, n = city_bbox(city)
    # Bbox extension: widen by 1° to catch upwind fires that affect this city.
    bbox = f"{w-1},{s-1},{e+1},{n+1}"
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{sensor}/{bbox}/{day_range}"
    text_data = await _get(url)
    reader = csv.DictReader(io.StringIO(text_data))
    rows = []
    for r in reader:
        lat = float(r.get("latitude", 0) or 0)
        lon = float(r.get("longitude", 0) or 0)
        ts = _parse_ts(r.get("acq_date", ""), r.get("acq_time", ""))
        if not ts:
            continue
        rows.append(
            {
                "ts": ts.isoformat(),
                "lat": lat,
                "lon": lon,
                "brightness": float(r.get("bright_ti4", 0) or r.get("brightness", 0) or 0) or None,
                "frp": float(r.get("frp", 0) or 0) or None,
                "confidence": r.get("confidence"),
                "sensor": sensor,
            }
        )
    return rows


@task
def insert_fires(rows: list[dict]) -> int:
    if not rows:
        return 0
    stmt = text(
        """
        INSERT INTO fire_event (ts, geom, brightness, frp, confidence, sensor)
        VALUES (:ts, ST_SetSRID(ST_MakePoint(:lon,:lat),4326), :brightness, :frp, :confidence, :sensor)
        """
    )
    with session_scope() as s:
        for r in rows:
            s.execute(stmt, r)
    return len(rows)


@flow(name="ingest-firms")
async def ingest_firms(city: str, day_range: int = 1) -> dict:
    log = get_run_logger()
    rows = await fetch_firms(city, day_range=day_range)
    n = insert_fires(rows)
    log.info("firms inserted: %d", n)
    return {"inserted": n}
