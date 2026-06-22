"""Traffic/mobility ingestion — dynamic congestion data.

Fetches from TomTom Traffic Flow API (free tier, requires TOMTOM_API_KEY env)
or falls back to synthetic stub based on static road_density from grid_cell.

-- Migration SQL:
-- CREATE TABLE traffic_density (
--     id BIGSERIAL PRIMARY KEY,
--     city_id TEXT NOT NULL,
--     cell_id TEXT NOT NULL,
--     ts TIMESTAMPTZ NOT NULL,
--     congestion_level REAL NOT NULL,
--     speed_ratio REAL NOT NULL,
--     road_type TEXT,
--     UNIQUE (city_id, cell_id, ts)
-- );
-- CREATE INDEX ix_traffic_density_city_ts ON traffic_density (city_id, ts);
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from prefect import flow, get_run_logger, task
from sqlalchemy import text

from vayunetra.storage.db import get_engine

_CONF = Path(__file__).resolve().parents[3] / "conf" / "ingest" / "traffic.yaml"


def _load_config() -> dict:
    return yaml.safe_load(_CONF.read_text())


@task
def fetch_tomtom(city: str, cells: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Fetch congestion from TomTom Flow Segment for each cell centroid."""
    import httpx

    api_key = os.environ.get("TOMTOM_API_KEY", "")
    base = cfg.get("tomtom_base_url", "https://api.tomtom.com/traffic/services/4/flowSegmentData/relative0/10/json")
    rows = []
    now = datetime.now(timezone.utc)
    for _, cell in cells.iterrows():
        url = f"{base}?point={cell['lat']},{cell['lon']}&key={api_key}"
        resp = httpx.get(url, timeout=10)
        if resp.status_code != 200:
            continue
        data = resp.json().get("flowSegmentData", {})
        speed = data.get("currentSpeed", 0)
        free = data.get("freeFlowSpeed", 1)
        ratio = speed / free if free else 1.0
        congestion = max(0.0, 1.0 - ratio)
        rows.append({
            "city_id": city,
            "cell_id": cell["cell_id"],
            "ts": now,
            "congestion_level": round(congestion, 3),
            "speed_ratio": round(ratio, 3),
            "road_type": data.get("frc", "unknown"),
        })
    return pd.DataFrame(rows)


@task
def generate_stub(city: str, cells: pd.DataFrame) -> pd.DataFrame:
    """Synthetic fallback: derive congestion from static road_density + noise."""
    now = datetime.now(timezone.utc)
    rng = np.random.default_rng(int(now.timestamp()) % 2**31)
    rd = cells["road_density"].fillna(0).to_numpy()
    congestion = np.clip(rd / rd.max() * 0.6 + rng.normal(0, 0.1, len(rd)), 0, 1) if rd.max() > 0 else rng.uniform(0, 0.3, len(rd))
    speed_ratio = np.clip(1.0 - congestion, 0.1, 1.0)
    return pd.DataFrame({
        "city_id": city,
        "cell_id": cells["cell_id"],
        "ts": now,
        "congestion_level": np.round(congestion, 3),
        "speed_ratio": np.round(speed_ratio, 3),
        "road_type": "stub",
    })


@task
def store_traffic(df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS traffic_density (
                id BIGSERIAL PRIMARY KEY,
                city_id TEXT NOT NULL,
                cell_id TEXT NOT NULL,
                ts TIMESTAMPTZ NOT NULL,
                congestion_level REAL NOT NULL,
                speed_ratio REAL NOT NULL,
                road_type TEXT,
                UNIQUE (city_id, cell_id, ts)
            )
        """))
        for _, r in df.iterrows():
            conn.execute(text("""
                INSERT INTO traffic_density (city_id, cell_id, ts, congestion_level, speed_ratio, road_type)
                VALUES (:city_id, :cell_id, :ts, :congestion_level, :speed_ratio, :road_type)
                ON CONFLICT (city_id, cell_id, ts) DO UPDATE
                SET congestion_level = EXCLUDED.congestion_level,
                    speed_ratio = EXCLUDED.speed_ratio,
                    road_type = EXCLUDED.road_type
            """), dict(r))
    return len(df)


@flow(name="ingest-traffic")
def ingest_traffic(city: str = "delhi") -> dict:
    """Main flow: fetch dynamic traffic density for all grid cells."""
    log = get_run_logger()
    cfg = _load_config()

    # Load grid cells
    engine = get_engine()
    with engine.begin() as conn:
        cells = pd.read_sql(
            text("SELECT cell_id, ST_X(centroid) AS lon, ST_Y(centroid) AS lat, road_density FROM grid_cell WHERE city_id = :city"),
            conn, params={"city": city},
        )

    if cells.empty:
        log.warning("No grid cells for %s", city)
        return {"rows": 0}

    api_key = os.environ.get("TOMTOM_API_KEY", "")
    if api_key:
        log.info("Using TomTom API for %s (%d cells)", city, len(cells))
        df = fetch_tomtom(city, cells, cfg)
    else:
        log.info("No TOMTOM_API_KEY; using stub for %s", city)
        df = generate_stub(city, cells)

    n = store_traffic(df)
    log.info("Stored %d traffic rows for %s", n, city)
    return {"rows": n}
