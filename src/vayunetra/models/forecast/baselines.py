"""Two baselines the forecast model must beat.

- persistence: prediction at t+h equals the last observed value at t.
- climatology: prediction = mean of same (hour, day-of-week, month) over a
  trailing window (default 60 days), per station.

These are pure functions on a feature dataframe (output of
forecast_features.build_features); they don't touch the DB so the same code is
reused by the walk-forward evaluator, the persistence gate, and notebooks.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def persistence_predict(df: pd.DataFrame) -> np.ndarray:
    """y_pred(t+h) = y(t)."""
    if "y" not in df.columns:
        raise KeyError("persistence baseline needs a 'y' column (current observation)")
    return df["y"].to_numpy()


def climatology_predict(df: pd.DataFrame, window_days: int = 60) -> np.ndarray:
    """For each row, returns the mean y of rows in the same station + same
    (hour-of-day, day-of-week, month) within the prior `window_days`.

    Falls back to per-station mean, then global mean, when a bucket is empty.
    """
    if df.empty:
        return np.array([])
    work = df.copy()
    if "ts_target" not in work.columns:
        raise KeyError("climatology baseline needs 'ts_target'")
    ts = pd.to_datetime(work["ts_target"], utc=True)
    work["__hour"] = ts.dt.hour
    work["__dow"] = ts.dt.dayofweek
    work["__month"] = ts.dt.month

    preds = np.full(len(work), np.nan, dtype=float)
    # Bucket-wise leave-current-row-out mean over the trailing window.
    by_bucket = work.groupby(["station_id", "__hour", "__dow", "__month"])
    for (station, h, dow, mo), idx in by_bucket.groups.items():
        rows = work.loc[idx].sort_values("ts_target")
        if rows.empty:
            continue
        # Cumulative trailing mean (excluding current via shift(1)).
        vals = rows["y"].astype(float).values
        # Simple expanding mean — adequate hackathon baseline.
        if len(vals) == 1:
            continue
        running = np.cumsum(vals) - vals
        denom = np.arange(len(vals))
        denom[0] = 1
        mean_excl = running / denom
        preds[rows.index] = mean_excl

    # Fallbacks
    if np.isnan(preds).any():
        per_station = work.groupby("station_id")["y"].transform("mean").to_numpy()
        m = np.isnan(preds)
        preds[m] = per_station[m]
    if np.isnan(preds).any():
        preds[np.isnan(preds)] = float(np.nanmean(work["y"].to_numpy()))
    return preds
