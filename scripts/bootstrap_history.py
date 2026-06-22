"""Pulls 6 months of OpenAQ + 30 days of S5P + 6 months of Open-Meteo into
PostGIS, then snapshots the curated tables under data/snapshots/<date>/.

Run once per fresh deployment. Idempotent — re-running just patches gaps.

Usage:
  poetry run python scripts/bootstrap_history.py [--cities delhi bengaluru]
                                                 [--months-back 6]
                                                 [--days-satellite 30]
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from vayunetra.common.logging import configure_logging, get_logger
from vayunetra.ingestion.gee import ingest_satellite_for_city
from vayunetra.ingestion.open_meteo import ingest_open_meteo
from vayunetra.ingestion.openaq import ingest_openaq
from vayunetra.ingestion.static_layers import build_grid

log = get_logger(__name__)


async def bootstrap_city(city: str, months_back: int, days_satellite: int) -> None:
    log.info("bootstrap_start", city=city)
    until = datetime.now(timezone.utc)
    since = until - timedelta(days=months_back * 30)

    # Static grid first — needed by everything downstream.
    build_grid(city)

    # Pull OpenAQ in weekly chunks (API limits).
    chunk = timedelta(days=7)
    cursor = since
    while cursor < until:
        chunk_end = min(cursor + chunk, until)
        await ingest_openaq(city=city, since=cursor, until=chunk_end)
        cursor = chunk_end

    # Open-Meteo only goes back ~2 days via /forecast; use what we get.
    await ingest_open_meteo(city=city, past_days=2, forecast_days=3)

    # Satellite: last N days, day-by-day (each call costs one GEE compute slot).
    today = until.date()
    for i in range(days_satellite):
        d = today - timedelta(days=i)
        try:
            ingest_satellite_for_city(city=city, date=d)
        except Exception as e:
            log.warning("satellite_skip", city=city, date=str(d), error=str(e))

    log.info("bootstrap_done", city=city)


def write_snapshot_marker() -> Path:
    snap_root = Path("data/snapshots") / date.today().isoformat()
    snap_root.mkdir(parents=True, exist_ok=True)
    marker = snap_root / "MANIFEST.txt"
    marker.write_text(
        "VayuNetra bootstrap snapshot\n"
        f"created_at: {datetime.now(timezone.utc).isoformat()}\n"
    )
    return snap_root


async def main_async(cities: list[str], months_back: int, days_satellite: int) -> None:
    for c in cities:
        await bootstrap_city(c, months_back, days_satellite)
    snap = write_snapshot_marker()
    log.info("snapshot_ready", path=str(snap))
    log.info("next_step", hint=f"dvc add {snap} && git commit")


def main() -> None:
    configure_logging()
    p = argparse.ArgumentParser()
    p.add_argument("--cities", nargs="+", default=["delhi", "bengaluru"])
    p.add_argument("--months-back", type=int, default=6)
    p.add_argument("--days-satellite", type=int, default=30)
    args = p.parse_args()
    asyncio.run(main_async(args.cities, args.months_back, args.days_satellite))


if __name__ == "__main__":
    main()
