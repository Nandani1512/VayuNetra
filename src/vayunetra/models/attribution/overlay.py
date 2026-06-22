"""Overlay the back-trajectory source polygon with OSM industry, FIRMS fire
events, and OSM road density to refine SHAP category proportions.

Pipeline:
  1. Build the back-trajectory source polygon (trajectory.back_trajectory).
  2. Count fires inside it within the lookback window → biomass_burning weight.
  3. Count industry POIs/polygons → industrial weight.
  4. Sample road_density inside grid cells touched → vehicular weight.
  5. Blend with SHAP source mix (50/50). Renormalize.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import asyncio
import json
import numpy as np
from shapely.geometry import Polygon, Point, shape
from sqlalchemy import text

from vayunetra.common.logging import get_logger
from vayunetra.models.attribution.shap_explainer import explain_cell
from vayunetra.models.attribution.trajectory import back_trajectory
from vayunetra.storage.db import get_engine

log = get_logger(__name__)

CATEGORIES = ("vehicular", "industrial", "biomass_burning", "construction_dust", "secondary", "dust_mixed")


def _polygon_wkt(coords: list[tuple[float, float]]) -> str:
    pts = ", ".join(f"{lon} {lat}" for lon, lat in coords)
    return f"SRID=4326;POLYGON(({pts}))"


def _fire_count_inside(coords: list[tuple[float, float]], since: datetime, until: datetime) -> tuple[int, float]:
    sql = text(
        """
        SELECT count(*) AS n, COALESCE(SUM(frp), 0) AS frp_sum
        FROM fire_event
        WHERE ts BETWEEN :since AND :until
          AND ST_Within(geom, ST_GeomFromEWKT(:wkt))
        """
    )
    with get_engine().begin() as conn:
        row = conn.execute(sql, {"since": since, "until": until, "wkt": _polygon_wkt(coords)}).fetchone()
    return (int(row.n or 0), float(row.frp_sum or 0))


def _city_metrics_inside(city: str, coords: list[tuple[float, float]]) -> dict[str, float]:
    """road density mean, industry/hospital/school sums for the grid cells whose
    centroid falls inside the source polygon."""
    sql = text(
        """
        SELECT AVG(road_density) AS road_density_mean,
               SUM(industry_count) AS industry_total,
               SUM(hospital_count) AS hospital_total,
               SUM(school_count)   AS school_total,
               COUNT(*) AS cells
        FROM grid_cell
        WHERE city_id = :city
          AND ST_Within(centroid, ST_GeomFromEWKT(:wkt))
        """
    )
    with get_engine().begin() as conn:
        row = conn.execute(sql, {"city": city, "wkt": _polygon_wkt(coords)}).fetchone()
    return {
        "road_density_mean": float(row.road_density_mean or 0),
        "industry_total": int(row.industry_total or 0),
        "hospital_total": int(row.hospital_total or 0),
        "school_total": int(row.school_total or 0),
        "cells": int(row.cells or 0),
    }


def _overlay_weights(metrics: dict[str, float], fires: int, frp: float) -> dict[str, float]:
    """Heuristic mapping from raw counts to per-category weights in [0,1]."""
    w = {
        "vehicular": min(metrics["road_density_mean"] / 5.0, 1.0),
        "industrial": min(metrics["industry_total"] / 50.0, 1.0),
        "biomass_burning": min((fires + frp / 100.0) / 30.0, 1.0),
        "construction_dust": 0.05,  # constant prior — refine when OSM construction is enriched
        "secondary": 0.10,
        "dust_mixed": 0.05,
    }
    total = sum(w.values()) or 1.0
    return {k: v / total for k, v in w.items()}


def _blend(shap_mix: dict[str, float], overlay_mix: dict[str, float], alpha: float = 0.5) -> dict[str, float]:
    cats = set(shap_mix) | set(overlay_mix) | set(CATEGORIES)
    out = {}
    for c in cats:
        out[c] = alpha * shap_mix.get(c, 0.0) + (1 - alpha) * overlay_mix.get(c, 0.0)
    total = sum(out.values()) or 1.0
    return {k: v / total for k, v in out.items() if v > 0}


async def attribute_cell(
    city: str,
    cell_id: str,
    ts: datetime,
    pollutant: str = "pm25",
    hours_back: int = 48,
) -> dict[str, Any]:
    # 1. SHAP on the LUR model.
    shap_out = explain_cell(city, cell_id, ts, pollutant=pollutant)

    # 2. Locate cell + run back-trajectory.
    sql = text("SELECT ST_X(centroid) AS lon, ST_Y(centroid) AS lat FROM grid_cell WHERE city_id=:city AND cell_id=:cid")
    with get_engine().begin() as conn:
        row = conn.execute(sql, {"city": city, "cid": cell_id}).fetchone()
    if not row:
        raise ValueError(f"cell_id {cell_id} not found")
    traj = await back_trajectory(city, float(row.lat), float(row.lon), ts, hours_back=hours_back)
    poly = traj["source_polygon"]

    # 3. Overlay: fires, industry, roads inside the source polygon.
    since = ts - timedelta(hours=hours_back)
    fires, frp_sum = _fire_count_inside(poly, since, ts)
    overlay_metrics = _city_metrics_inside(city, poly)
    overlay_mix = _overlay_weights(overlay_metrics, fires, frp_sum)

    # 4. Blend SHAP + overlay.
    blended = _blend(shap_out["sources"], overlay_mix, alpha=0.5)

    return {
        "city": city,
        "cell_id": cell_id,
        "ts": ts.isoformat(),
        "pollutant": pollutant,
        "shap_sources": shap_out["sources"],
        "overlay_sources": overlay_mix,
        "blended_sources": dict(sorted(blended.items(), key=lambda kv: -kv[1])),
        "overlay_evidence": {
            "fires_in_source_region": fires,
            "frp_sum": frp_sum,
            "industry_in_source_region": overlay_metrics["industry_total"],
            "road_density_mean": overlay_metrics["road_density_mean"],
            "cells_touched": overlay_metrics["cells"],
        },
        "trajectory_geojson": traj["geojson"],
        "wind_speed_ms": traj["mean_wind_speed_ms"],
        "wind_bearing_from_deg": traj["mean_wind_bearing_deg"],
        "confidence": shap_out["confidence"],
    }
