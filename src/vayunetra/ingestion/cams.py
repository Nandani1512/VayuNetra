"""CAMS (Copernicus Atmosphere) backfill via the ADS CDS API.

Triggered only when GEE's S5P pull reports >gap_threshold_pct masking. Pulls
global forecast for the target day, extracts the city bbox, writes per-cell
columns into satellite_column with product='cams_<var>'.

Requires `cdsapi` and a ~/.cdsapirc or CAMS_ADS_KEY env. We keep this thin —
full CAMS support is post-hackathon work.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from prefect import flow, get_run_logger, task

from vayunetra.common.config import get_settings
from vayunetra.ingestion.utils import city_bbox


@task
def download_cams(day: date, city: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"cams_{city}_{day.isoformat()}.grib"
    if target.exists():
        return target
    try:
        import cdsapi  # type: ignore
    except ImportError as e:
        raise RuntimeError("cdsapi not installed; add it to optional deps") from e

    s = get_settings()
    client = cdsapi.Client(url=s.cams_ads_url, key=s.cams_ads_key) if s.cams_ads_key else cdsapi.Client()
    w, sth, e, nth = city_bbox(city)
    client.retrieve(
        "cams-global-atmospheric-composition-forecasts",
        {
            "variable": [
                "nitrogen_dioxide",
                "sulphur_dioxide",
                "particulate_matter_2.5um",
                "particulate_matter_10um",
                "carbon_monoxide",
                "ozone",
            ],
            "date": day.isoformat(),
            "type": "forecast",
            "time": "00:00",
            "leadtime_hour": ["0", "12", "24", "36", "48", "60", "72"],
            "area": [nth, w, sth, e],  # N, W, S, E
            "format": "grib",
        },
        str(target),
    )
    return target


@flow(name="ingest-cams")
def ingest_cams(city: str, day: date) -> dict:
    """Stub: downloads CAMS for the day. Cell-level decode into satellite_column
    is implemented when Phase 1 demands it; for now we keep the file in MinIO
    for downstream consumers."""
    log = get_run_logger()
    out_dir = Path("data/cache/cams")
    f = download_cams(day, city, out_dir)
    log.info("cams cached at %s", f)
    return {"file": str(f)}
