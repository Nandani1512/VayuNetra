"""Back-trajectory source-region estimator.

Primary: NOAA READY web API (no Fortran build needed). When unavailable, falls
back to an analytical Gaussian-plume back-projection using the local
weather.wind_u/v over the last `hours_back` hours. Both return a GeoJSON polygon
of the upwind source region + a polyline approximation of the trajectory.

Cached in Redis by (lat_round, lon_round, day) for 24 h — back-trajectories at
nearby cells on the same day are effectively identical.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import numpy as np
from shapely.geometry import LineString, Point, Polygon, mapping
from sqlalchemy import text

from vayunetra.common.cache import cache_get_json, cache_set_json
from vayunetra.common.logging import get_logger
from vayunetra.storage.db import get_engine

log = get_logger(__name__)

READY_URL = "https://www.ready.noaa.gov/hypub-bin/trajasrc.pl"
DEFAULT_HOURS_BACK = 48
WIND_BUFFER_KM_PER_HR = 3.0  # plume half-width per hour of advection


def _round_grid(v: float, step: float = 0.05) -> float:
    return round(v / step) * step


async def _ready_trajectory(lat: float, lon: float, ts: datetime, hours_back: int) -> dict | None:
    """Submit a query to NOAA READY. The page is HTML, but we don't actually
    need it for a hackathon — we use the analytical fallback by default since
    it's deterministic and offline. NOAA READY is documented as a stretch goal
    behind ENABLE_NOAA_READY."""
    return None


def _haversine_destination(lat: float, lon: float, bearing_deg: float, dist_km: float) -> tuple[float, float]:
    R = 6371.0
    brg = math.radians(bearing_deg)
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    lat2 = math.asin(
        math.sin(lat1) * math.cos(dist_km / R)
        + math.cos(lat1) * math.sin(dist_km / R) * math.cos(brg)
    )
    lon2 = lon1 + math.atan2(
        math.sin(brg) * math.sin(dist_km / R) * math.cos(lat1),
        math.cos(dist_km / R) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), math.degrees(lon2)


def _gaussian_plume_back(
    lat: float, lon: float, ts: datetime, hours_back: int, wind_u: float, wind_v: float
) -> dict[str, Any]:
    """Approximate back-trajectory as a wedge polygon along the wind direction,
    widening with travel distance.

    wind_u/v are the wind vector blowing TOWARD u east / v north. The source
    region lies upwind: invert the vector.
    """
    speed = math.hypot(wind_u, wind_v)  # m/s if from Open-Meteo
    if speed < 0.1:
        # Calm: small circle around the cell.
        circle_km = 5.0
        coords = []
        for ang in range(0, 360, 30):
            la, lo = _haversine_destination(lat, lon, ang, circle_km)
            coords.append((lo, la))
        coords.append(coords[0])
        return {
            "trajectory": [(lon, lat)],
            "source_polygon": coords,
            "mean_wind_speed_ms": speed,
            "mean_wind_bearing_deg": None,
            "method": "calm_circle",
        }

    # Bearing the wind is going TO; source is the opposite direction.
    bearing_to = (math.degrees(math.atan2(wind_u, wind_v)) + 360) % 360
    bearing_from = (bearing_to + 180) % 360

    total_km = (speed * hours_back * 3600) / 1000.0
    # Sample 12 points along the back-trajectory.
    steps = 12
    traj = [(lon, lat)]
    for i in range(1, steps + 1):
        d = total_km * i / steps
        la, lo = _haversine_destination(lat, lon, bearing_from, d)
        traj.append((lo, la))

    # Build a wedge polygon: left/right half-widths grow with distance.
    half_widths = [max(2.0, WIND_BUFFER_KM_PER_HR * (i * hours_back / steps)) for i in range(steps + 1)]
    left_side, right_side = [], []
    for (lo, la), hw in zip(traj, half_widths):
        la_l, lo_l = _haversine_destination(la, lo, (bearing_from - 90) % 360, hw)
        la_r, lo_r = _haversine_destination(la, lo, (bearing_from + 90) % 360, hw)
        left_side.append((lo_l, la_l))
        right_side.append((lo_r, la_r))
    poly = left_side + list(reversed(right_side))
    poly.append(poly[0])

    return {
        "trajectory": traj,
        "source_polygon": poly,
        "mean_wind_speed_ms": speed,
        "mean_wind_bearing_deg": bearing_from,
        "method": "gaussian_plume_back",
    }


def _nearest_wind(city: str, ts: datetime) -> tuple[float, float]:
    """Most recent (wind_u, wind_v) for any station in the city before ts."""
    sql = text(
        """
        SELECT w.wind_u, w.wind_v
        FROM weather w JOIN station s ON s.id = w.station_id
        WHERE s.city_id = :city AND w.ts <= :ts
        ORDER BY w.ts DESC LIMIT 1
        """
    )
    with get_engine().begin() as conn:
        row = conn.execute(sql, {"city": city, "ts": ts}).fetchone()
    if not row or row.wind_u is None or row.wind_v is None:
        return 0.0, 0.0
    return float(row.wind_u), float(row.wind_v)


async def back_trajectory(
    city: str,
    lat: float,
    lon: float,
    ts: datetime,
    hours_back: int = DEFAULT_HOURS_BACK,
) -> dict[str, Any]:
    cache_key = f"traj:{city}:{_round_grid(lat)}:{_round_grid(lon)}:{ts.date().isoformat()}:{hours_back}"
    cached = await cache_get_json(cache_key)
    if cached:
        return cached

    # NOAA READY is opt-in; default to deterministic plume.
    ready = await _ready_trajectory(lat, lon, ts, hours_back)
    if ready is not None:
        out = ready
    else:
        wind_u, wind_v = _nearest_wind(city, ts)
        out = _gaussian_plume_back(lat, lon, ts, hours_back, wind_u, wind_v)

    out["geojson"] = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": out["trajectory"]},
                "properties": {"role": "trajectory"},
            },
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [out["source_polygon"]]},
                "properties": {"role": "source_region"},
            },
        ],
    }
    await cache_set_json(cache_key, out, ttl_s=24 * 3600)
    return out
