"""Tile cache for forecast grids.

For each (city, pollutant, horizon, ts_issued):
  - Build a GeoJSON FeatureCollection from the `forecast` and `grid_cell` tables.
  - Upload it to MinIO under
      forecast/{city}/{pollutant}/{horizon}/{ts_issued_iso}.geojson
  - Store the object key in Redis at the same path with a TTL.

API handlers read the Redis key first; on miss, they rebuild and re-cache. The
cold path is bounded by the SQL that joins forecast → grid_cell (≤ a few k rows
per city per horizon).

Note: we serve GeoJSON now (deck.gl ingests it directly). Phase 7 may swap to
geobuf/MVT if payload size becomes a constraint; the cache key scheme stays.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from minio import Minio
from sqlalchemy import text

from vayunetra.common.cache import cache_get_json, cache_set_json
from vayunetra.common.config import get_settings
from vayunetra.common.logging import get_logger
from vayunetra.storage.db import get_engine

log = get_logger(__name__)


@dataclass
class TileKey:
    city: str
    pollutant: str
    horizon_h: int
    ts_issued: datetime

    def object_path(self) -> str:
        iso = self.ts_issued.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")
        return f"forecast/{self.city}/{self.pollutant}/{self.horizon_h}/{iso}.geojson"

    def redis_key(self) -> str:
        return f"tile:{self.object_path()}"


def _minio_client() -> Minio:
    s = get_settings()
    endpoint = s.minio_endpoint
    secure = endpoint.startswith("https://")
    host = endpoint.replace("http://", "").replace("https://", "")
    return Minio(
        host,
        access_key=s.aws_access_key_id,
        secret_key=s.aws_secret_access_key,
        secure=secure,
    )


def _build_geojson(key: TileKey) -> dict[str, Any]:
    sql = text(
        """
        WITH latest AS (
          SELECT cell_id, MAX(ts_issued) AS ts_issued
          FROM forecast
          WHERE city_id = :city
            AND pollutant = :pollutant
            AND ts_target = :ts_target
          GROUP BY cell_id
        )
        SELECT f.cell_id, f.p10, f.p50, f.p90, f.model_version,
               ST_AsGeoJSON(g.geom) AS geom_json
        FROM forecast f
        JOIN latest l ON f.cell_id = l.cell_id AND f.ts_issued = l.ts_issued
        JOIN grid_cell g ON g.city_id = :city AND g.cell_id = f.cell_id
        WHERE f.city_id = :city
          AND f.pollutant = :pollutant
          AND f.ts_target = :ts_target
        """
    )
    ts_target = key.ts_issued.astimezone(timezone.utc).replace(
        minute=0, second=0, microsecond=0
    )
    # ts_target = ts_issued + horizon
    from datetime import timedelta

    ts_target = ts_target + timedelta(hours=key.horizon_h)

    features: list[dict[str, Any]] = []
    with get_engine().begin() as conn:
        for r in conn.execute(
            sql,
            {
                "city": key.city,
                "pollutant": key.pollutant,
                "ts_target": ts_target,
            },
        ):
            features.append(
                {
                    "type": "Feature",
                    "geometry": json.loads(r.geom_json),
                    "properties": {
                        "cell_id": r.cell_id,
                        "p10": float(r.p10) if r.p10 is not None else None,
                        "p50": float(r.p50) if r.p50 is not None else None,
                        "p90": float(r.p90) if r.p90 is not None else None,
                        "model_version": r.model_version,
                    },
                }
            )

    return {
        "type": "FeatureCollection",
        "metadata": {
            "city": key.city,
            "pollutant": key.pollutant,
            "horizon_h": key.horizon_h,
            "ts_issued": key.ts_issued.astimezone(timezone.utc).isoformat(),
            "ts_target": ts_target.isoformat(),
            "count": len(features),
        },
        "features": features,
    }


async def get_or_build_tile(key: TileKey, ttl_s: int = 3600) -> dict[str, Any]:
    """Returns the GeoJSON for this tile, building & caching on miss."""
    cached = await cache_get_json(key.redis_key())
    if cached is not None:
        return cached

    geojson = _build_geojson(key)

    # Upload to MinIO (best-effort; fall back to Redis-only cache on failure).
    try:
        client = _minio_client()
        bucket = get_settings().minio_bucket
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
        body = json.dumps(geojson).encode("utf-8")
        client.put_object(
            bucket,
            key.object_path(),
            io.BytesIO(body),
            length=len(body),
            content_type="application/geo+json",
        )
    except Exception as e:
        log.warning("minio_upload_failed", error=str(e), key=key.object_path())

    await cache_set_json(key.redis_key(), geojson, ttl_s)
    return geojson


def invalidate(city: str, pollutant: str, horizon_h: int, ts_issued: datetime) -> None:
    """Best-effort cache invalidation for a single tile."""
    import asyncio

    from vayunetra.common.cache import get_redis

    key = TileKey(city, pollutant, horizon_h, ts_issued)

    async def _del():
        await get_redis().delete(key.redis_key())

    asyncio.get_event_loop().run_until_complete(_del())
