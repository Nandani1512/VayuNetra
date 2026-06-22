"""OpenAQ v3 ingestion.

Pages through /v3/locations within a city bbox, upserts stations, then pulls
/v3/measurements per (station, pollutant). Rate-limited to 60 req/min,
exponential backoff on 429/5xx, idempotent upserts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from aiolimiter import AsyncLimiter
from prefect import flow, get_run_logger, task
from sqlalchemy import text
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from vayunetra.common.config import get_settings
from vayunetra.common.errors import UpstreamRateLimitError
from vayunetra.ingestion.utils import city_bbox, parameter_id_for, pollutant_from_id

BASE_URL = "https://api.openaq.org/v3"
PAGE_LIMIT = 1000
RATE = AsyncLimiter(max_rate=60, time_period=60)  # 60 req/min

POLLUTANT_NAMES = ("pm25", "pm10", "no2", "so2", "o3", "co")


def _headers() -> dict[str, str]:
    key = get_settings().openaq_api_key
    if not key:
        raise RuntimeError("OPENAQ_API_KEY not set")
    return {"X-API-Key": key, "Accept": "application/json"}


@retry(
    stop=stop_after_attempt(6),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    retry=retry_if_exception_type((httpx.HTTPError, UpstreamRateLimitError)),
    reraise=True,
)
async def _get(client: httpx.AsyncClient, url: str, params: dict[str, Any]) -> dict:
    async with RATE:
        r = await client.get(url, params=params, headers=_headers(), timeout=20)
    if r.status_code == 429:
        raise UpstreamRateLimitError("openaq 429")
    r.raise_for_status()
    return r.json()


@task(retries=2)
async def fetch_locations(city: str) -> list[dict]:
    bbox = city_bbox(city)
    out: list[dict] = []
    page = 1
    async with httpx.AsyncClient() as client:
        while True:
            data = await _get(
                client,
                f"{BASE_URL}/locations",
                {
                    "bbox": ",".join(str(v) for v in bbox),
                    "limit": PAGE_LIMIT,
                    "page": page,
                },
            )
            results = data.get("results", [])
            if not results:
                break
            out.extend(results)
            if len(results) < PAGE_LIMIT:
                break
            page += 1
            if page > 50:
                break
    return out


@task
def upsert_stations(city: str, locations: list[dict]) -> int:
    from vayunetra.storage.db import session_scope

    if not locations:
        return 0
    rows = []
    for loc in locations:
        loc_id = loc.get("id")
        coords = loc.get("coordinates") or {}
        lat, lon = coords.get("latitude"), coords.get("longitude")
        if loc_id is None or lat is None or lon is None:
            continue
        rows.append(
            {
                "id": f"openaq:{loc_id}",
                "source": "openaq",
                "city_id": city,
                "name": loc.get("name") or "",
                "lon": lon,
                "lat": lat,
                "attrs": {
                    "country": (loc.get("country") or {}).get("code"),
                    "owner": (loc.get("owner") or {}).get("name"),
                    "sensors": [s.get("id") for s in (loc.get("sensors") or [])],
                },
            }
        )
    if not rows:
        return 0
    stmt = text(
        """
        INSERT INTO station (id, source, city_id, name, geom, attrs, updated_at)
        VALUES (:id, :source, :city_id, :name,
                ST_SetSRID(ST_MakePoint(:lon, :lat), 4326),
                CAST(:attrs AS jsonb), now())
        ON CONFLICT (id) DO UPDATE
        SET name = EXCLUDED.name,
            geom = EXCLUDED.geom,
            attrs = EXCLUDED.attrs,
            updated_at = now()
        """
    )
    import json

    with session_scope() as s:
        for r in rows:
            r["attrs"] = json.dumps(r["attrs"])
            s.execute(stmt, r)
    return len(rows)


@task(retries=2)
async def fetch_measurements(
    station_openaq_id: int, sensors: list[int], since: datetime, until: datetime
) -> list[dict]:
    """Pulls measurements for each sensor of a station within [since, until]."""
    rows: list[dict] = []
    async with httpx.AsyncClient() as client:
        for sensor_id in sensors:
            page = 1
            while True:
                data = await _get(
                    client,
                    f"{BASE_URL}/sensors/{sensor_id}/measurements",
                    {
                        "datetime_from": since.isoformat(),
                        "datetime_to": until.isoformat(),
                        "limit": PAGE_LIMIT,
                        "page": page,
                    },
                )
                results = data.get("results", [])
                if not results:
                    break
                rows.extend({"sensor_id": sensor_id, **r} for r in results)
                if len(results) < PAGE_LIMIT:
                    break
                page += 1
                if page > 50:
                    break
    return rows


@task
def upsert_observations(station_id: str, rows: list[dict]) -> int:
    from vayunetra.storage.db import session_scope

    if not rows:
        return 0
    cleaned = []
    for r in rows:
        param = (r.get("parameter") or {}).get("name") or pollutant_from_id(
            (r.get("parameter") or {}).get("id")
        )
        if param not in POLLUTANT_NAMES:
            continue
        period = r.get("period") or {}
        dt = (period.get("datetimeTo") or {}).get("utc") or r.get("datetime", {}).get("utc")
        val = r.get("value")
        if not dt or val is None:
            continue
        cleaned.append(
            {
                "station_id": station_id,
                "ts": dt,
                "pollutant": param,
                "value": float(val),
                "unit": (r.get("parameter") or {}).get("units") or "ug/m3",
                "qa": None,
                "source": "openaq",
            }
        )
    if not cleaned:
        return 0
    stmt = text(
        """
        INSERT INTO observation (station_id, ts, pollutant, value, unit, qa, source)
        VALUES (:station_id, :ts, :pollutant, :value, :unit, :qa, :source)
        ON CONFLICT (station_id, ts, pollutant) DO UPDATE
        SET value = EXCLUDED.value,
            unit = EXCLUDED.unit,
            qa = EXCLUDED.qa
        """
    )
    with session_scope() as s:
        for r in cleaned:
            s.execute(stmt, r)
    return len(cleaned)


@flow(name="ingest-openaq")
async def ingest_openaq(city: str, since: datetime, until: datetime) -> dict:
    log = get_run_logger()
    locs = await fetch_locations(city)
    n_stations = upsert_stations(city, locs)
    log.info("openaq stations upserted: %d", n_stations)

    total_obs = 0
    for loc in locs:
        sensors = [s.get("id") for s in (loc.get("sensors") or [])]
        sensors = [s for s in sensors if s is not None]
        if not sensors:
            continue
        rows = await fetch_measurements(loc["id"], sensors, since, until)
        total_obs += upsert_observations(f"openaq:{loc['id']}", rows)

    log.info("openaq observations upserted: %d", total_obs)
    return {"stations": n_stations, "observations": total_obs}
