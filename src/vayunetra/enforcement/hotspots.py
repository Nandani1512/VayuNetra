"""Hotspot detection on the live forecast grid.

Getis-Ord Gi* on (city, pollutant, horizon) cells → significant clusters.
DBSCAN groups contiguous hot cells. Returns a ranked list of polygons with
mean p50, exposed population, and member cell ids.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd
from libpysal.weights import KNN
from esda.getisord import G_Local
from sklearn.cluster import DBSCAN
from sqlalchemy import text

from vayunetra.common.logging import get_logger
from vayunetra.storage.db import get_engine

log = get_logger(__name__)


def _load_forecast_grid(city: str, pollutant: str, horizon_h: int) -> pd.DataFrame:
    sql = text(
        """
        WITH latest AS (
          SELECT cell_id, MAX(ts_issued) AS ts_issued
          FROM forecast
          WHERE city_id = :city AND pollutant = :pollutant
            AND ts_target = (SELECT MAX(ts_target) FROM forecast
                             WHERE city_id = :city AND pollutant = :pollutant)
          GROUP BY cell_id
        )
        SELECT f.cell_id, f.ts_target, f.p10, f.p50, f.p90,
               ST_X(g.centroid) AS lon, ST_Y(g.centroid) AS lat,
               COALESCE(g.pop_total, 0) AS pop_total,
               ST_AsText(g.geom) AS geom_wkt
        FROM forecast f
        JOIN latest l ON f.cell_id = l.cell_id AND f.ts_issued = l.ts_issued
        JOIN grid_cell g ON g.city_id = :city AND g.cell_id = f.cell_id
        WHERE f.city_id = :city AND f.pollutant = :pollutant
        """
    )
    with get_engine().begin() as conn:
        return pd.read_sql(sql, conn, params={"city": city, "pollutant": pollutant})


def detect_hotspots(
    city: str,
    pollutant: str = "pm25",
    horizon_h: int = 24,
    z_threshold: float = 1.65,
    eps_deg: float = 0.02,  # ~2 km
    min_samples: int = 3,
) -> dict[str, Any]:
    df = _load_forecast_grid(city, pollutant, horizon_h)
    if df.empty:
        return {"clusters": [], "total_cells": 0}

    # Gi* (queen-contiguity replaced with KNN-5 — faster and works without
    # explicit polygon adjacency on the 1km grid).
    coords = df[["lon", "lat"]].to_numpy()
    w = KNN.from_array(coords, k=min(8, len(df) - 1))
    g = G_Local(df["p50"].to_numpy(), w, transform="r", permutations=0)
    df = df.assign(gi_z=g.Zs)

    hot = df[df["gi_z"] > z_threshold].copy()
    if hot.empty:
        return {"clusters": [], "total_cells": int(len(df)), "hot_cells": 0}

    db = DBSCAN(eps=eps_deg, min_samples=min_samples).fit(hot[["lon", "lat"]])
    hot["cluster_id"] = db.labels_

    clusters = []
    for cid, group in hot.groupby("cluster_id"):
        if cid == -1:
            continue
        clusters.append(
            {
                "cluster_id": int(cid),
                "cells": group["cell_id"].tolist(),
                "n_cells": int(len(group)),
                "mean_p50": float(group["p50"].mean()),
                "max_p50": float(group["p50"].max()),
                "mean_gi_z": float(group["gi_z"].mean()),
                "pop_exposed": float(group["pop_total"].sum()),
                "centroid_lon": float(group["lon"].mean()),
                "centroid_lat": float(group["lat"].mean()),
            }
        )
    clusters.sort(key=lambda c: c["mean_p50"] * (c["pop_exposed"] + 1), reverse=True)
    return {
        "city": city,
        "pollutant": pollutant,
        "horizon_h": horizon_h,
        "total_cells": int(len(df)),
        "hot_cells": int(len(hot)),
        "clusters": clusters,
    }
