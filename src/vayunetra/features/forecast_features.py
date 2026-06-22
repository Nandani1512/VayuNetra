"""Feature engineering for the per-station temporal forecast.

Pulls observation + weather + nearest satellite column for each (station, hour),
builds lags, rolling stats, calendar features, and fire counts in upwind
distance bands. Output: a pandas DataFrame ready for LightGBM.

Materialized view `mv_forecast_features` is a faster path for serving; the
Python builder here is the authoritative reference and is used by the trainer
and the walk-forward evaluator.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

import numpy as np
import pandas as pd
from sqlalchemy import text

from vayunetra.common.logging import get_logger
from vayunetra.ingestion.utils import load_city_config
from vayunetra.storage.db import get_engine

log = get_logger(__name__)

LAGS_H = (1, 3, 6, 12, 24, 48, 72)
ROLLING_H = (6, 24)


def _parse_md(s: str) -> tuple[int, int]:
    m, d = s.split("-")
    return int(m), int(d)


def _calendar_flags(city: str, ts: pd.Series) -> pd.DataFrame:
    cfg = load_city_config(city)
    out = pd.DataFrame(index=ts.index)
    hour = ts.dt.hour
    dow = ts.dt.dayofweek
    out["sin_hour"] = np.sin(2 * np.pi * hour / 24.0)
    out["cos_hour"] = np.cos(2 * np.pi * hour / 24.0)
    out["sin_dow"] = np.sin(2 * np.pi * dow / 7.0)
    out["cos_dow"] = np.cos(2 * np.pi * dow / 7.0)
    out["is_weekend"] = (dow >= 5).astype(int)

    diwali_set = {pd.Timestamp(d).date() for d in cfg.get("diwali_dates", [])}
    out["is_diwali"] = ts.dt.date.apply(
        lambda d: any(abs((d - dd).days) <= 3 for dd in diwali_set)
    ).astype(int)

    burn = cfg.get("crop_burn_season") or {}
    if burn and burn.get("start_month_day") and burn.get("end_month_day"):
        sm, sd = _parse_md(burn["start_month_day"])
        em, ed = _parse_md(burn["end_month_day"])

        def _in_burn(d):
            md = (d.month, d.day)
            return (sm, sd) <= md <= (em, ed)

        out["is_crop_burn_season"] = ts.dt.date.apply(_in_burn).astype(int)
    else:
        out["is_crop_burn_season"] = 0
    return out


def _load_observations(city: str, pollutant: str, since: datetime, until: datetime) -> pd.DataFrame:
    sql = text(
        """
        SELECT o.station_id, o.ts, o.value AS y
        FROM observation o
        JOIN station s ON s.id = o.station_id
        WHERE s.city_id = :city
          AND o.pollutant = :pollutant
          AND o.ts BETWEEN :since AND :until
        ORDER BY o.station_id, o.ts
        """
    )
    with get_engine().begin() as conn:
        df = pd.read_sql(sql, conn, params={"city": city, "pollutant": pollutant, "since": since, "until": until})
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.floor("h")
    # Hourly mean per station (collapses sub-hourly samples)
    df = df.groupby(["station_id", "ts"], as_index=False)["y"].mean()
    return df


def _load_weather(city: str, since: datetime, until: datetime) -> pd.DataFrame:
    sql = text(
        """
        SELECT w.station_id, w.ts, w.temp_c, w.wind_u, w.wind_v,
               w.pbl_m, w.rh_pct, w.precip_mm
        FROM weather w
        JOIN station s ON s.id = w.station_id
        WHERE s.city_id = :city
          AND w.ts BETWEEN :since AND :until
        """
    )
    with get_engine().begin() as conn:
        df = pd.read_sql(sql, conn, params={"city": city, "since": since, "until": until})
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.floor("h")
    return df.drop_duplicates(["station_id", "ts"])


def _load_satellite_daily(city: str, since: datetime, until: datetime) -> pd.DataFrame:
    """City-wide daily mean per product. Joined with a 1-day lag (column is
    daily, station forecast is hourly)."""
    sql = text(
        """
        SELECT date_trunc('day', ts) AS day, product, AVG(value) AS value
        FROM satellite_column
        WHERE city_id = :city AND ts BETWEEN :since AND :until
        GROUP BY 1, 2
        """
    )
    with get_engine().begin() as conn:
        df = pd.read_sql(sql, conn, params={"city": city, "since": since, "until": until})
    if df.empty:
        return df
    df = df.pivot(index="day", columns="product", values="value").reset_index()
    df["day"] = pd.to_datetime(df["day"], utc=True)
    return df


def _load_fire_counts(city: str, since: datetime, until: datetime) -> pd.DataFrame:
    """Hourly upwind-agnostic fire counts within 50km and 100km of city centroid.
    Phase 4 attributes per-cell fire impact; here we keep one feature per hour.
    """
    cfg = load_city_config(city)
    w, s, e, n = cfg["bbox"]
    centre_lon = (w + e) / 2
    centre_lat = (s + n) / 2
    sql = text(
        """
        SELECT date_trunc('hour', ts) AS hour,
               SUM(CASE WHEN ST_DWithin(geom::geography,
                                        ST_SetSRID(ST_MakePoint(:lon,:lat),4326)::geography,
                                        50000) THEN 1 ELSE 0 END) AS fire_count_50km,
               SUM(CASE WHEN ST_DWithin(geom::geography,
                                        ST_SetSRID(ST_MakePoint(:lon,:lat),4326)::geography,
                                        100000) THEN 1 ELSE 0 END) AS fire_count_100km,
               COALESCE(SUM(frp), 0) AS frp_sum_100km
        FROM fire_event
        WHERE ts BETWEEN :since AND :until
          AND ST_DWithin(geom::geography,
                         ST_SetSRID(ST_MakePoint(:lon,:lat),4326)::geography,
                         100000)
        GROUP BY 1
        """
    )
    with get_engine().begin() as conn:
        df = pd.read_sql(
            sql,
            conn,
            params={"lon": centre_lon, "lat": centre_lat, "since": since, "until": until},
        )
    if df.empty:
        return pd.DataFrame(columns=["hour", "fire_count_50km", "fire_count_100km", "frp_sum_100km"])
    df["hour"] = pd.to_datetime(df["hour"], utc=True)
    return df


def _add_lags_and_rolling(df: pd.DataFrame, group_col: str = "station_id") -> pd.DataFrame:
    df = df.sort_values([group_col, "ts"]).copy()
    g = df.groupby(group_col)["y"]
    for h in LAGS_H:
        df[f"y_lag_{h}h"] = g.shift(h)
    for w in ROLLING_H:
        df[f"y_roll_mean_{w}h"] = g.shift(1).rolling(w, min_periods=1).mean().reset_index(0, drop=True)
        df[f"y_roll_std_{w}h"] = g.shift(1).rolling(w, min_periods=1).std().reset_index(0, drop=True)
    return df


def build_features(
    city: str,
    pollutant: str,
    since: datetime,
    until: datetime,
    horizon_h: int,
) -> pd.DataFrame:
    """Returns one row per (station_id, ts_target). `y` is the target at ts+h."""
    obs = _load_observations(city, pollutant, since, until)
    if obs.empty:
        log.warning("no_observations", city=city, pollutant=pollutant)
        return obs

    obs = _add_lags_and_rolling(obs)
    # Shift target forward: predict y(t+h) from features at t.
    obs["target"] = obs.groupby("station_id")["y"].shift(-horizon_h)
    obs["ts_target"] = obs["ts"] + pd.Timedelta(hours=horizon_h)

    weather = _load_weather(city, since, until)
    if not weather.empty:
        obs = obs.merge(weather, on=["station_id", "ts"], how="left")

    sat = _load_satellite_daily(city, since - timedelta(days=2), until)
    if not sat.empty:
        # Use yesterday's satellite column for today's features (1-day lag).
        sat["day"] = sat["day"] + pd.Timedelta(days=1)
        obs["day"] = obs["ts"].dt.floor("d")
        obs = obs.merge(sat, on="day", how="left")
        obs = obs.drop(columns=["day"])

    fires = _load_fire_counts(city, since, until)
    if not fires.empty:
        obs = obs.merge(fires.rename(columns={"hour": "ts"}), on="ts", how="left")
        for col in ("fire_count_50km", "fire_count_100km", "frp_sum_100km"):
            if col in obs.columns:
                obs[col] = obs[col].fillna(0)

    cal = _calendar_flags(city, obs["ts"])
    obs = pd.concat([obs.reset_index(drop=True), cal.reset_index(drop=True)], axis=1)

    # Station id retained as categorical (LightGBM handles natively).
    obs["station_id"] = obs["station_id"].astype("category")

    # Drop rows with no target (the tail) and no lagged features (the head).
    obs = obs.dropna(subset=["target", "y_lag_1h"])
    return obs


def feature_columns(df: pd.DataFrame) -> list[str]:
    """Columns the model trains on. `y` (the value at issuance time) is kept —
    at prediction time we know the current observation, so persistence-equivalent
    behaviour is a floor, not a ceiling."""
    drop = {"ts", "ts_target", "target"}
    return [c for c in df.columns if c not in drop]
