"""Applies SQL migrations in src/vayunetra/storage/migrations/ in lexical order.

Lightweight replacement for Alembic so the migration story works on day one;
swap to Alembic when versioned migrations become necessary.

Run via: `python -m vayunetra.storage.bootstrap` or `make migrate`.
"""

from __future__ import annotations

import sys
from importlib.resources import files
from pathlib import Path

from sqlalchemy import text

from vayunetra.common.logging import configure_logging, get_logger
from vayunetra.storage.db import get_engine

log = get_logger(__name__)


def _migrations_dir() -> Path:
    return Path(str(files("vayunetra.storage").joinpath("migrations")))


def apply_migrations() -> int:
    engine = get_engine()
    migrations_dir = _migrations_dir()
    sql_files = sorted(migrations_dir.glob("*.sql"))
    if not sql_files:
        log.warning("no_migrations_found", dir=str(migrations_dir))
        return 0

    applied = 0
    with engine.begin() as conn:
        for f in sql_files:
            log.info("applying_migration", file=f.name)
            sql = f.read_text()
            # Run as one block; psycopg supports multi-statement strings.
            conn.execute(text(sql))
            applied += 1
    log.info("migrations_done", count=applied)
    return applied


if __name__ == "__main__":
    configure_logging()
    try:
        apply_migrations()
    except Exception as exc:
        log.error("migration_failed", error=str(exc))
        sys.exit(1)
