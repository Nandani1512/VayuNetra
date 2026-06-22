"""CLI entry to trigger ingestion flows ad-hoc.

Usage:
  python -m vayunetra.ingestion --all-cities
  python -m vayunetra.ingestion --city delhi --source openaq
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from vayunetra.common.logging import configure_logging, get_logger
from vayunetra.ingestion.firms import ingest_firms
from vayunetra.ingestion.gee import ingest_satellite_for_city
from vayunetra.ingestion.open_meteo import ingest_open_meteo
from vayunetra.ingestion.openaq import ingest_openaq

log = get_logger(__name__)
CITIES = ["delhi", "bengaluru"]


async def run(city: str, sources: list[str], since: datetime, until: datetime) -> None:
    log.info("ingest_start", city=city, sources=sources, since=since.isoformat())
    if "openaq" in sources:
        await ingest_openaq(city=city, since=since, until=until)
    if "open_meteo" in sources:
        await ingest_open_meteo(city=city)
    if "firms" in sources:
        await ingest_firms(city=city)
    if "gee" in sources:
        ingest_satellite_for_city(city=city, date=until.date())
    log.info("ingest_done", city=city)


def main() -> None:
    configure_logging()
    p = argparse.ArgumentParser()
    p.add_argument("--city", choices=CITIES)
    p.add_argument("--all-cities", action="store_true")
    p.add_argument(
        "--source",
        choices=["openaq", "open_meteo", "firms", "gee"],
        action="append",
        default=None,
    )
    p.add_argument("--hours-back", type=int, default=24)
    args = p.parse_args()

    cities = CITIES if args.all_cities else [args.city or "delhi"]
    sources = args.source or ["openaq", "open_meteo", "firms"]
    until = datetime.now(timezone.utc)
    since = until - timedelta(hours=args.hours_back)

    for c in cities:
        asyncio.run(run(c, sources, since, until))


if __name__ == "__main__":
    main()
