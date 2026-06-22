"""Sentinel-5P and MODIS AOD ingestion via Google Earth Engine.

For each product, reduces the day's collection over the city bbox, masks by QA,
resamples to a 0.05° grid, and ingests cell-level values into satellite_column.
If >gap_threshold_pct of cells are masked, returns gap=True so the caller can
trigger CAMS backfill.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any

from prefect import flow, get_run_logger, task
from sqlalchemy import text

from vayunetra.common.config import get_settings
from vayunetra.ingestion.utils import city_bbox, load_city_config

PRODUCTS: dict[str, dict[str, Any]] = {
    "s5p_no2": {
        "collection": "COPERNICUS/S5P/OFFL/L3_NO2",
        "band": "tropospheric_NO2_column_number_density",
        "qa": "qa_value",
        "qa_threshold": 0.75,
    },
    "s5p_so2": {
        "collection": "COPERNICUS/S5P/OFFL/L3_SO2",
        "band": "SO2_column_number_density",
        "qa": "qa_value",
        "qa_threshold": 0.5,
    },
    "s5p_co": {
        "collection": "COPERNICUS/S5P/OFFL/L3_CO",
        "band": "CO_column_number_density",
        "qa": "qa_value",
        "qa_threshold": 0.5,
    },
    "modis_aod": {
        "collection": "MODIS/061/MCD19A2_GRANULES",
        "band": "Optical_Depth_055",
        "qa": None,
        "qa_threshold": None,
    },
}

GRID_RES_DEG = 0.05


def _init_gee() -> None:
    import ee  # type: ignore

    s = get_settings()
    if not s.gee_key_file:
        raise RuntimeError("GEE_KEY_FILE not configured")
    credentials = ee.ServiceAccountCredentials(s.gee_service_account, s.gee_key_file)
    ee.Initialize(credentials, opt_url="https://earthengine-highvolume.googleapis.com")


@task
def fetch_product_grid(city: str, product: str, day: date) -> tuple[list[dict], float]:
    """Returns (rows, masked_pct). Each row: ts, product, cell_key, lon0/lat0/lon1/lat1, value, qa."""
    import ee  # type: ignore

    _init_gee()
    cfg = PRODUCTS[product]
    bbox = city_bbox(city)
    region = ee.Geometry.Rectangle(list(bbox))

    start = datetime.combine(day, time.min).replace(tzinfo=timezone.utc)
    end = datetime.combine(day, time.max).replace(tzinfo=timezone.utc)

    col = ee.ImageCollection(cfg["collection"]).filterDate(start.isoformat(), end.isoformat()).filterBounds(region)
    img = col.mean() if cfg["qa"] is None else col.map(
        lambda im: im.updateMask(im.select(cfg["qa"]).gte(cfg["qa_threshold"]))
    ).mean()

    band = img.select(cfg["band"])

    # Build a 0.05° grid of polygons covering the bbox.
    min_lon, min_lat, max_lon, max_lat = bbox
    cells = []
    lat = min_lat
    while lat < max_lat:
        lon = min_lon
        while lon < max_lon:
            cells.append((lon, lat, min(lon + GRID_RES_DEG, max_lon), min(lat + GRID_RES_DEG, max_lat)))
            lon += GRID_RES_DEG
        lat += GRID_RES_DEG

    feats = []
    for i, (lo0, la0, lo1, la1) in enumerate(cells):
        rect = ee.Geometry.Rectangle([lo0, la0, lo1, la1])
        feats.append(ee.Feature(rect, {"cell_key": f"{product}:{i}", "lo0": lo0, "la0": la0, "lo1": lo1, "la1": la1}))
    fc = ee.FeatureCollection(feats)

    reduced = band.reduceRegions(collection=fc, reducer=ee.Reducer.mean(), scale=5500)
    results = reduced.getInfo().get("features", [])

    ts = datetime.combine(day, time.min).replace(tzinfo=timezone.utc).isoformat()
    rows = []
    masked = 0
    for f in results:
        props = f.get("properties", {})
        val = props.get("mean")
        if val is None:
            masked += 1
            continue
        rows.append(
            {
                "ts": ts,
                "product": product,
                "cell_key": props["cell_key"],
                "lo0": props["lo0"],
                "la0": props["la0"],
                "lo1": props["lo1"],
                "la1": props["la1"],
                "value": float(val),
                "qa": None,
                "city_id": city,
            }
        )
    total = len(results) or 1
    return rows, masked / total * 100.0


@task
def upsert_satellite(rows: list[dict]) -> int:
    if not rows:
        return 0
    from vayunetra.storage.db import session_scope

    stmt = text(
        """
        INSERT INTO satellite_column (ts, product, cell_key, cell, city_id, value, qa)
        VALUES (:ts, :product, :cell_key,
                ST_MakeEnvelope(:lo0,:la0,:lo1,:la1,4326)::geometry(Polygon,4326),
                :city_id, :value, :qa)
        ON CONFLICT (ts, product, cell_key) DO UPDATE
        SET value = EXCLUDED.value,
            qa = EXCLUDED.qa,
            cell = EXCLUDED.cell
        """
    )
    with session_scope() as s:
        for r in rows:
            s.execute(stmt, r)
    return len(rows)


@flow(name="ingest-gee")
def ingest_satellite_for_city(city: str, date: date | None = None) -> dict:
    log = get_run_logger()
    day = date or datetime.now(timezone.utc).date()
    out = {}
    gaps: list[str] = []
    for product in PRODUCTS:
        rows, masked_pct = fetch_product_grid(city, product, day)
        n = upsert_satellite(rows)
        out[product] = {"rows": n, "masked_pct": masked_pct}
        log.info("%s rows=%d masked=%.1f%%", product, n, masked_pct)
        if masked_pct > 40:
            gaps.append(product)
    out["gaps"] = gaps
    return out
