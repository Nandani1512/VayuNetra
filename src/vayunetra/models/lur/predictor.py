"""Grid predictor for the LUR model + residual IDW fusion with the per-station
temporal forecast.

Pipeline:
  1. Load latest MLflow LUR model for (city, pollutant).
  2. Build inference rows for the 1 km grid at ts.
  3. Predict per-cell mean p50_lur.
  4. Pull station forecasts for the same horizon (forecast_features + Phase 2
     trainer). Compute station residuals = forecast_p50_station - p50_lur_at_station.
  5. Interpolate residuals over the grid via inverse-distance weighting (IDW).
  6. Final p50 = p50_lur + residual_grid. Approximate p10/p90 as p50 ± k·sigma
     where sigma is derived from the residual spread (cheap proxy until
     Phase 2 quantiles are joined at the grid level).
  7. Persist results into the `forecast` table.

CLI:
  python -m vayunetra.models.lur.predictor --city delhi --pollutant pm25 --horizon 24
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import lightgbm as lgb
import mlflow
import numpy as np
import pandas as pd
from sqlalchemy import text

from vayunetra.common.config import get_settings
from vayunetra.common.logging import configure_logging, get_logger
from vayunetra.features.lur_features import build_inference_rows
from vayunetra.models.forecast.lightgbm_trainer import predict as forecast_predict
from vayunetra.storage.db import session_scope

log = get_logger(__name__)


def _load_lur_booster(city: str, pollutant: str) -> tuple[lgb.Booster, dict]:
    settings = get_settings()
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    exp = mlflow.get_experiment_by_name(f"lur_{city}_{pollutant}")
    if exp is None:
        raise RuntimeError(f"LUR experiment lur_{city}_{pollutant} not found")
    runs = mlflow.search_runs(
        [exp.experiment_id],
        order_by=["start_time DESC"],
        max_results=1,
    )
    if runs.empty:
        raise RuntimeError(f"no LUR runs for {city}/{pollutant}")
    run_id = runs.iloc[0]["run_id"]
    local = mlflow.artifacts.download_artifacts(run_id=run_id, artifact_path="model")
    meta = json.loads((Path(local) / "meta.json").read_text())
    booster = lgb.Booster(model_file=str(Path(local) / "booster.txt"))
    return booster, meta


def _idw_interpolate(
    src_lon: np.ndarray,
    src_lat: np.ndarray,
    src_val: np.ndarray,
    dst_lon: np.ndarray,
    dst_lat: np.ndarray,
    power: float = 2.0,
    eps: float = 1e-6,
) -> np.ndarray:
    """Inverse-distance weighting (degree-Euclidean is fine within a city)."""
    if len(src_lon) == 0:
        return np.zeros_like(dst_lon, dtype=float)
    dlon = dst_lon[:, None] - src_lon[None, :]
    dlat = dst_lat[:, None] - src_lat[None, :]
    dist = np.sqrt(dlon * dlon + dlat * dlat) + eps
    w = 1.0 / dist**power
    return (w * src_val[None, :]).sum(axis=1) / w.sum(axis=1)


def _station_locations(city: str) -> pd.DataFrame:
    sql = text(
        """
        SELECT id AS station_id, ST_X(geom) AS lon, ST_Y(geom) AS lat
        FROM station WHERE city_id = :city
        """
    )
    from vayunetra.storage.db import get_engine

    with get_engine().begin() as conn:
        return pd.read_sql(sql, conn, params={"city": city})


def _persist_forecast(rows: list[dict]) -> int:
    if not rows:
        return 0
    stmt = text(
        """
        INSERT INTO forecast
          (city_id, cell_id, ts_issued, ts_target, pollutant, p10, p50, p90, model_version)
        VALUES
          (:city_id, :cell_id, :ts_issued, :ts_target, :pollutant, :p10, :p50, :p90, :model_version)
        ON CONFLICT (city_id, cell_id, ts_issued, ts_target, pollutant) DO UPDATE
        SET p10 = EXCLUDED.p10,
            p50 = EXCLUDED.p50,
            p90 = EXCLUDED.p90,
            model_version = EXCLUDED.model_version
        """
    )
    with session_scope() as s:
        for r in rows:
            s.execute(stmt, r)
    return len(rows)


def predict_grid(
    city: str,
    pollutant: str = "pm25",
    horizon_h: int = 24,
    ts_issued: datetime | None = None,
    persist: bool = True,
) -> pd.DataFrame:
    """Returns one row per grid cell with p10/p50/p90 for ts_issued + horizon_h.

    If `persist=True`, also writes into the `forecast` table.
    """
    ts_issued = ts_issued or datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    ts_target = ts_issued + timedelta(hours=horizon_h)

    # Step 1-3: LUR mean field.
    booster, meta = _load_lur_booster(city, pollutant)
    grid_df = build_inference_rows(city, ts_issued)
    if grid_df.empty:
        raise RuntimeError("empty grid — run build_grid first")

    feat_cols = [c for c in meta["feature_columns"] if c in grid_df.columns]
    p50_lur = booster.predict(grid_df[feat_cols])

    # Step 4-5: station forecast residuals → IDW.
    residual_grid = np.zeros_like(p50_lur)
    try:
        station_fc = forecast_predict(city, pollutant, horizon_h, ts_issued)
        if not station_fc.empty:
            stations = _station_locations(city)
            joined = station_fc.merge(stations, on="station_id", how="left").dropna(subset=["lon", "lat"])
            if not joined.empty:
                # LUR prediction at the station's nearest grid cell:
                glat = grid_df["lat"].to_numpy()
                glon = grid_df["lon"].to_numpy()
                slat = joined["lat"].to_numpy()[:, None]
                slon = joined["lon"].to_numpy()[:, None]
                d2 = (glat - slat) ** 2 + (glon - slon) ** 2
                nn = np.argmin(d2, axis=1)
                p50_lur_at_station = p50_lur[nn]
                resid = joined["p50"].to_numpy() - p50_lur_at_station
                residual_grid = _idw_interpolate(
                    joined["lon"].to_numpy(),
                    joined["lat"].to_numpy(),
                    resid,
                    grid_df["lon"].to_numpy(),
                    grid_df["lat"].to_numpy(),
                )
    except Exception as e:
        log.warning("station_fusion_skipped", error=str(e))

    p50 = np.maximum(p50_lur + residual_grid, 0.0)

    # Step 6: simple uncertainty band from residual spread.
    sigma = float(np.std(residual_grid)) if residual_grid.size else 0.0
    sigma = max(sigma, 0.10 * float(np.mean(p50_lur)) if p50_lur.size else 1.0)
    p10 = np.maximum(p50 - 1.28 * sigma, 0.0)
    p90 = p50 + 1.28 * sigma

    out = pd.DataFrame(
        {
            "city_id": city,
            "cell_id": grid_df["cell_id"].values,
            "ts_issued": ts_issued,
            "ts_target": ts_target,
            "pollutant": pollutant,
            "p10": p10,
            "p50": p50,
            "p90": p90,
            "model_version": meta.get("trained_at", "unknown"),
        }
    )

    if persist:
        n = _persist_forecast(out.to_dict(orient="records"))
        log.info("forecast_persisted", rows=n, city=city, horizon_h=horizon_h)

    return out


def main() -> None:
    configure_logging()
    p = argparse.ArgumentParser()
    p.add_argument("--city", required=True)
    p.add_argument("--pollutant", default="pm25")
    p.add_argument("--horizon", type=int, default=24)
    p.add_argument("--no-persist", action="store_true")
    args = p.parse_args()
    df = predict_grid(args.city, args.pollutant, args.horizon, persist=not args.no_persist)
    print(json.dumps({
        "cells": int(len(df)),
        "p50_min": float(df["p50"].min()),
        "p50_mean": float(df["p50"].mean()),
        "p50_max": float(df["p50"].max()),
    }, indent=2))


if __name__ == "__main__":
    main()
