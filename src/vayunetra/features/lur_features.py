"""Land Use Regression feature builder.

Produces two related tables:

  - `build_training_rows(city, pollutant, since, until)` → one row per
    (station_id, ts_hour) for training. Target = observed station value.

  - `build_inference_rows(city, ts)` → one row per (city, cell_id) for a single
    issue timestamp `ts`. Used by predictor.predict_grid().

Both share the same feature vector layout: nearest satellite column values,
hourly meteorology (interpolated from the nearest station for inference rows),
static grid_cell features (road density, industry count, etc.), and fire counts
in 50/100 km bands.

Phase 3 limits the LUR target to surface PM2.5 by default; pollutant is
parameterized to make extension to PM10/NO2 cheap.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import text

from vayunetra.common.logging import get_logger
from vayunetra.ingestion.utils import load_city_config
from vayunetra.storage.db import get_engine

log = get_logger(__name__)

STATIC_COLS = (
    "road_density",
    "industry_count",
    "hospital_count",
    "school_count",
    "pop_total",
    "pop_elderly",
    "pop_children",
    "elevation_m",
    "lulc_class",
)

SAT_PRODUCTS = ("s5p_no2", "s5p_so2", "s5p_co", "modis_aod")

METEO_COLS = ("temp_c", "wind_u", "wind_v", "pbl_m", "rh_pct", "precip_mm")


def _load_satellite_daily_per_cell(city: str, since: datetime, until: datetime) -> pd.DataFrame:
    """Returns ts (day floor), cell_centroid_lon/lat, and one column per product.

    Cells in `satellite_column` are 0.05° tiles, not the LUR 1 km grid. We carry
    their centroids so callers can match each LUR target to the nearest tile.
    """
    sql = text(
        """
        SELECT date_trunc('day', ts) AS day,
               product,
               ST_X(ST_Centroid(cell)) AS lon,
               ST_Y(ST_Centroid(cell)) AS lat,
               value
        FROM satellite_column
        WHERE city_id = :city AND ts BETWEEN :since AND :until
        """
    )
    with get_engine().begin() as conn:
        df = pd.read_sql(sql, conn, params={"city": city, "since": since, "until": until})
    if df.empty:
        return df
    df["day"] = pd.to_datetime(df["day"], utc=True)
    return df


def _nearest_sat_per_point(points: pd.DataFrame, sat: pd.DataFrame) -> pd.DataFrame:
    """For each (point_lon, point_lat, day) row, attach the value of each
    satellite product from the nearest tile centroid on that day. Brute-force
    is fine — tile counts are O(few hundred) and point counts O(thousands).
    """
    if sat.empty:
        out = points.copy()
        for p in SAT_PRODUCTS:
            out[p] = np.nan
        return out

    out_chunks = []
    sat_by_day = {d: g for d, g in sat.groupby("day")}
    for day, group in points.groupby("day"):
        s = sat_by_day.get(day)
        if s is None or s.empty:
            extra = pd.DataFrame({p: np.nan for p in SAT_PRODUCTS}, index=group.index)
            out_chunks.append(pd.concat([group, extra], axis=1))
            continue
        # For each product, build a (lat, lon) array and find nearest
        result = group.copy()
        for prod in SAT_PRODUCTS:
            sp = s[s["product"] == prod]
            if sp.empty:
                result[prod] = np.nan
                continue
            lats = sp["lat"].to_numpy()
            lons = sp["lon"].to_numpy()
            vals = sp["value"].to_numpy()
            # Euclidean in degrees is fine for nearest within a city bbox.
            gp_lat = result["lat"].to_numpy()[:, None]
            gp_lon = result["lon"].to_numpy()[:, None]
            d2 = (lats - gp_lat) ** 2 + (lons - gp_lon) ** 2
            nearest = np.argmin(d2, axis=1)
            result[prod] = vals[nearest]
        out_chunks.append(result)
    return pd.concat(out_chunks, ignore_index=False)


def _load_weather_hourly(city: str, since: datetime, until: datetime) -> pd.DataFrame:
    sql = text(
        """
        SELECT w.station_id, w.ts, ST_X(s.geom) AS lon, ST_Y(s.geom) AS lat,
               w.temp_c, w.wind_u, w.wind_v, w.pbl_m, w.rh_pct, w.precip_mm
        FROM weather w JOIN station s ON s.id = w.station_id
        WHERE s.city_id = :city AND w.ts BETWEEN :since AND :until
        """
    )
    with get_engine().begin() as conn:
        df = pd.read_sql(sql, conn, params={"city": city, "since": since, "until": until})
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.floor("h")
    return df.drop_duplicates(["station_id", "ts"])


def _load_static_grid(city: str) -> pd.DataFrame:
    sql = text(
        """
        SELECT cell_id, ST_X(centroid) AS lon, ST_Y(centroid) AS lat,
               road_density, industry_count, hospital_count, school_count,
               pop_total, pop_elderly, pop_children, elevation_m, lulc_class
        FROM grid_cell WHERE city_id = :city
        """
    )
    with get_engine().begin() as conn:
        df = pd.read_sql(sql, conn, params={"city": city})
    return df


def _load_stations(city: str) -> pd.DataFrame:
    sql = text(
        """
        SELECT id AS station_id, ST_X(geom) AS lon, ST_Y(geom) AS lat
        FROM station WHERE city_id = :city
        """
    )
    with get_engine().begin() as conn:
        df = pd.read_sql(sql, conn, params={"city": city})
    return df


def _load_observations_hourly(
    city: str, pollutant: str, since: datetime, until: datetime
) -> pd.DataFrame:
    sql = text(
        """
        SELECT o.station_id, date_trunc('hour', o.ts) AS ts, AVG(o.value) AS y
        FROM observation o JOIN station s ON s.id = o.station_id
        WHERE s.city_id = :city
          AND o.pollutant = :pollutant
          AND o.ts BETWEEN :since AND :until
        GROUP BY 1, 2
        """
    )
    with get_engine().begin() as conn:
        df = pd.read_sql(
            sql,
            conn,
            params={"city": city, "pollutant": pollutant, "since": since, "until": until},
        )
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df


def _fire_counts_per_point(
    points: pd.DataFrame, since: datetime, until: datetime
) -> pd.DataFrame:
    """One row per (point_lon, point_lat), aggregated over the whole window.
    Cheap approximation: use a single per-day per-city count instead of
    per-point ST_DWithin queries (which would be O(N×fires))."""
    sql = text(
        """
        SELECT ST_X(geom) AS lon, ST_Y(geom) AS lat, frp
        FROM fire_event
        WHERE ts BETWEEN :since AND :until
        """
    )
    with get_engine().begin() as conn:
        fires = pd.read_sql(sql, conn, params={"since": since, "until": until})

    if fires.empty:
        out = points[["lon", "lat"]].drop_duplicates().copy()
        out["fire_count_50km"] = 0
        out["fire_count_100km"] = 0
        out["frp_sum_100km"] = 0.0
        return out

    # Vectorised great-circle approximation.
    R = 6371.0
    rad = np.pi / 180
    plat = points["lat"].to_numpy()[:, None] * rad
    plon = points["lon"].to_numpy()[:, None] * rad
    flat = fires["lat"].to_numpy()[None, :] * rad
    flon = fires["lon"].to_numpy()[None, :] * rad
    dphi = flat - plat
    dlam = flon - plon
    a = np.sin(dphi / 2) ** 2 + np.cos(plat) * np.cos(flat) * np.sin(dlam / 2) ** 2
    dist_km = 2 * R * np.arcsin(np.sqrt(a))

    within_50 = (dist_km <= 50).astype(int)
    within_100 = (dist_km <= 100).astype(int)
    frp = np.nan_to_num(fires["frp"].to_numpy(), nan=0.0)[None, :]

    out = points[["lon", "lat"]].copy()
    out["fire_count_50km"] = within_50.sum(axis=1)
    out["fire_count_100km"] = within_100.sum(axis=1)
    out["frp_sum_100km"] = (within_100 * frp).sum(axis=1)
    return out


def _attach_static_grid(points: pd.DataFrame, grid: pd.DataFrame) -> pd.DataFrame:
    """Attach the nearest grid_cell's static features to each point."""
    if grid.empty:
        out = points.copy()
        for c in STATIC_COLS:
            out[c] = np.nan
        out["cell_id"] = None
        return out
    glat = grid["lat"].to_numpy()
    glon = grid["lon"].to_numpy()
    plat = points["lat"].to_numpy()[:, None]
    plon = points["lon"].to_numpy()[:, None]
    d2 = (glat - plat) ** 2 + (glon - plon) ** 2
    nearest = np.argmin(d2, axis=1)
    out = points.copy()
    out["cell_id"] = grid["cell_id"].to_numpy()[nearest]
    for c in STATIC_COLS:
        out[c] = grid[c].to_numpy()[nearest]
    return out


def _attach_nearest_weather(
    points: pd.DataFrame, weather: pd.DataFrame, ts_col: str = "ts"
) -> pd.DataFrame:
    """For each (point, ts) row, attach the nearest weather station's reading
    at the same hour."""
    if weather.empty:
        out = points.copy()
        for c in METEO_COLS:
            out[c] = np.nan
        return out

    out_chunks = []
    weather_by_ts = {t: g for t, g in weather.groupby(ts_col)}
    for ts, group in points.groupby(ts_col):
        w = weather_by_ts.get(ts)
        if w is None or w.empty:
            extra = pd.DataFrame({c: np.nan for c in METEO_COLS}, index=group.index)
            out_chunks.append(pd.concat([group, extra], axis=1))
            continue
        wlat = w["lat"].to_numpy()
        wlon = w["lon"].to_numpy()
        plat = group["lat"].to_numpy()[:, None]
        plon = group["lon"].to_numpy()[:, None]
        d2 = (wlat - plat) ** 2 + (wlon - plon) ** 2
        nearest = np.argmin(d2, axis=1)
        res = group.copy()
        for c in METEO_COLS:
            res[c] = w[c].to_numpy()[nearest]
        out_chunks.append(res)
    return pd.concat(out_chunks, ignore_index=False)


def build_training_rows(
    city: str, pollutant: str, since: datetime, until: datetime
) -> pd.DataFrame:
    """One row per (station, hour) with the LUR feature vector + target."""
    obs = _load_observations_hourly(city, pollutant, since, until)
    if obs.empty:
        return obs

    stations = _load_stations(city)
    obs = obs.merge(stations, on="station_id", how="left")

    # day floor for satellite join
    obs["day"] = obs["ts"].dt.floor("d")
    sat = _load_satellite_daily_per_cell(city, since - timedelta(days=2), until)
    if not sat.empty:
        # 1-day lag: yesterday's column → today's features.
        sat = sat.copy()
        sat["day"] = sat["day"] + pd.Timedelta(days=1)
    obs = _nearest_sat_per_point(obs, sat)

    weather = _load_weather_hourly(city, since, until)
    obs = _attach_nearest_weather(obs, weather)

    grid = _load_static_grid(city)
    obs = _attach_static_grid(obs, grid)

    fire = _fire_counts_per_point(obs[["lon", "lat"]].drop_duplicates(), since, until)
    obs = obs.merge(fire, on=["lon", "lat"], how="left")
    for c in ("fire_count_50km", "fire_count_100km", "frp_sum_100km"):
        if c in obs.columns:
            obs[c] = obs[c].fillna(0)

    obs["lulc_class"] = obs["lulc_class"].astype("category")
    # Coerce numeric columns that may come back as object when entirely NULL in DB.
    for col in ("pop_total", "pop_elderly", "pop_children", "elevation_m",
                "hospital_count", "school_count", "industry_count",
                "road_density", "fire_count_50km", "fire_count_100km", "frp_sum_100km"):
        if col in obs.columns:
            obs[col] = pd.to_numeric(obs[col], errors="coerce").astype(float)
    obs = obs.rename(columns={"y": "target"})
    obs = obs.dropna(subset=["target"])
    return obs.reset_index(drop=True)


def build_inference_rows(city: str, ts: datetime) -> pd.DataFrame:
    """One row per (cell_id) with the LUR feature vector evaluated at `ts`."""
    grid = _load_static_grid(city)
    if grid.empty:
        return grid

    points = grid[["cell_id", "lon", "lat"] + list(STATIC_COLS)].copy()
    points["ts"] = pd.Timestamp(ts).floor("h").tz_convert("UTC") if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts, tz="UTC").floor("h")
    points["day"] = points["ts"].dt.floor("d")

    sat_since = points["day"].min() - pd.Timedelta(days=2)
    sat_until = points["day"].max() + pd.Timedelta(days=1)
    sat = _load_satellite_daily_per_cell(city, sat_since.to_pydatetime(), sat_until.to_pydatetime())
    if not sat.empty:
        sat = sat.copy()
        sat["day"] = sat["day"] + pd.Timedelta(days=1)
    points = _nearest_sat_per_point(points, sat)

    weather = _load_weather_hourly(city, points["ts"].min().to_pydatetime(), points["ts"].max().to_pydatetime())
    points = _attach_nearest_weather(points, weather)

    fire_window_start = (points["ts"].min() - pd.Timedelta(days=1)).to_pydatetime()
    fire_window_end = points["ts"].max().to_pydatetime()
    fire = _fire_counts_per_point(points[["lon", "lat"]].drop_duplicates(), fire_window_start, fire_window_end)
    points = points.merge(fire, on=["lon", "lat"], how="left")
    for c in ("fire_count_50km", "fire_count_100km", "frp_sum_100km"):
        points[c] = points[c].fillna(0)

    points["lulc_class"] = points["lulc_class"].astype("category")
    for col in ("pop_total", "pop_elderly", "pop_children", "elevation_m",
                "hospital_count", "school_count", "industry_count",
                "road_density", "fire_count_50km", "fire_count_100km", "frp_sum_100km"):
        if col in points.columns:
            points[col] = pd.to_numeric(points[col], errors="coerce").astype(float)
    return points.reset_index(drop=True)


def lur_feature_columns(df: pd.DataFrame) -> list[str]:
    drop = {"ts", "day", "station_id", "target", "cell_id"}
    return [c for c in df.columns if c not in drop]
