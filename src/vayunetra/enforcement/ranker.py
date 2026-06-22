"""Rank hotspots by severity × exposed population × emitter density.

Cross-references each cluster centroid with registered emitters within 500 m
via OSM tags already loaded into grid_cell (industry/construction/road density).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import text

from vayunetra.storage.db import get_engine


def _emitters_near(city: str, lon: float, lat: float, radius_m: float = 500.0) -> dict[str, int]:
    sql = text(
        """
        SELECT COALESCE(SUM(industry_count), 0) AS industry,
               COALESCE(SUM(school_count), 0) AS schools,
               COALESCE(SUM(hospital_count), 0) AS hospitals,
               AVG(road_density) AS road_density
        FROM grid_cell
        WHERE city_id = :city
          AND ST_DWithin(centroid::geography,
                         ST_SetSRID(ST_MakePoint(:lon,:lat),4326)::geography,
                         :r)
        """
    )
    with get_engine().begin() as conn:
        row = conn.execute(sql, {"city": city, "lon": lon, "lat": lat, "r": radius_m}).fetchone()
    return {
        "industry": int(row.industry or 0),
        "schools": int(row.schools or 0),
        "hospitals": int(row.hospitals or 0),
        "road_density": float(row.road_density or 0),
    }


def rank_clusters(city: str, clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = []
    for c in clusters:
        emitters = _emitters_near(city, c["centroid_lon"], c["centroid_lat"])
        severity = c["mean_p50"] * math.log1p(c["pop_exposed"]) * (1 + 0.1 * emitters["industry"] + 0.05 * emitters["road_density"])
        ranked.append({**c, "emitters": emitters, "severity_score": float(severity)})
    ranked.sort(key=lambda c: c["severity_score"], reverse=True)
    for i, c in enumerate(ranked):
        c["rank"] = i + 1
    return ranked
