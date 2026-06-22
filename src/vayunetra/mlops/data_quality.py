"""Lightweight data-quality (expectations) checks (Phase 10).

The plan calls for Great Expectations suites on ``observation``, ``weather`` and
``grid_cell``. GE pulls a very large dependency tree for what we need here, so
we ship a tiny, dependency-free expectations engine with the same *shape*
(declarative expectations → a pass/fail validation report). Swapping this for
GE later is a drop-in at the suite-definition layer.

Each expectation returns an :class:`ExpectationResult`. A :class:`Suite` bundles
expectations for one table; :func:`validate` runs a suite against a DataFrame.

The ingestion flows call :func:`run_table_check` at the end of each run; a
failing *critical* expectation should page on-call (Slack webhook, wired in the
flow, not here).

CLI:
  python -m vayunetra.mlops.data_quality --table observation
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from vayunetra.common.logging import configure_logging, get_logger

log = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass
class ExpectationResult:
    name: str
    column: str | None
    success: bool
    critical: bool
    observed: Any
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "expectation": self.name,
            "column": self.column,
            "success": self.success,
            "critical": self.critical,
            "observed": self.observed,
            "detail": self.detail,
        }


# An expectation is a callable: DataFrame -> ExpectationResult.
Expectation = Callable[[pd.DataFrame], ExpectationResult]


def expect_row_count_min(min_rows: int, *, critical: bool = True) -> Expectation:
    def _check(df: pd.DataFrame) -> ExpectationResult:
        n = len(df)
        return ExpectationResult(
            name=f"row_count >= {min_rows}",
            column=None,
            success=n >= min_rows,
            critical=critical,
            observed=n,
        )

    return _check


def expect_columns_exist(columns: list[str], *, critical: bool = True) -> Expectation:
    def _check(df: pd.DataFrame) -> ExpectationResult:
        missing = [c for c in columns if c not in df.columns]
        return ExpectationResult(
            name=f"columns_exist {columns}",
            column=None,
            success=not missing,
            critical=critical,
            observed={"missing": missing},
        )

    return _check


def expect_null_fraction_below(
    column: str, max_fraction: float, *, critical: bool = False
) -> Expectation:
    def _check(df: pd.DataFrame) -> ExpectationResult:
        if column not in df.columns:
            return ExpectationResult(
                f"null_fraction({column}) < {max_fraction}", column, False, critical,
                observed=None, detail="column absent",
            )
        frac = float(df[column].isna().mean()) if len(df) else 1.0
        return ExpectationResult(
            f"null_fraction({column}) < {max_fraction}", column, frac <= max_fraction,
            critical, observed=round(frac, 4),
        )

    return _check


def expect_values_between(
    column: str, low: float, high: float, *, critical: bool = False, allow_null: bool = True
) -> Expectation:
    def _check(df: pd.DataFrame) -> ExpectationResult:
        if column not in df.columns:
            return ExpectationResult(
                f"{low} <= {column} <= {high}", column, False, critical,
                observed=None, detail="column absent",
            )
        s = pd.to_numeric(df[column], errors="coerce")
        valid = s.dropna() if allow_null else s
        if valid.empty:
            return ExpectationResult(
                f"{low} <= {column} <= {high}", column, True, critical,
                observed=None, detail="no non-null values to check",
            )
        oob = int(((valid < low) | (valid > high)).sum())
        return ExpectationResult(
            f"{low} <= {column} <= {high}", column, oob == 0, critical,
            observed={"out_of_bounds": oob, "min": float(valid.min()), "max": float(valid.max())},
        )

    return _check


def expect_unique(columns: list[str], *, critical: bool = True) -> Expectation:
    def _check(df: pd.DataFrame) -> ExpectationResult:
        present = [c for c in columns if c in df.columns]
        if not present:
            return ExpectationResult(
                f"unique {columns}", None, False, critical, observed=None, detail="columns absent"
            )
        dupes = int(df.duplicated(subset=present).sum())
        return ExpectationResult(
            f"unique {columns}", None, dupes == 0, critical, observed={"duplicate_rows": dupes}
        )

    return _check


@dataclass
class Suite:
    table: str
    expectations: list[Expectation] = field(default_factory=list)


@dataclass
class ValidationResult:
    table: str
    ts: str
    success: bool
    n_critical_failures: int
    results: list[ExpectationResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "ts": self.ts,
            "success": self.success,
            "n_critical_failures": self.n_critical_failures,
            "results": [r.to_dict() for r in self.results],
        }


def validate(df: pd.DataFrame, suite: Suite) -> ValidationResult:
    results = [exp(df) for exp in suite.expectations]
    critical_failures = sum(1 for r in results if r.critical and not r.success)
    return ValidationResult(
        table=suite.table,
        ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        success=critical_failures == 0,
        n_critical_failures=critical_failures,
        results=results,
    )


# --------------------------------------------------------------------------- #
# Suites (mirror the PostGIS schema in storage/migrations/0001_init.sql).
# --------------------------------------------------------------------------- #
def observation_suite() -> Suite:
    return Suite(
        "observation",
        [
            expect_columns_exist(["station_id", "ts", "pollutant", "value", "unit"]),
            expect_row_count_min(1),
            expect_null_fraction_below("value", 0.05, critical=True),
            expect_null_fraction_below("ts", 0.0, critical=True),
            # PM/gas concentrations: physically plausible upper bound (µg/m³).
            expect_values_between("value", 0.0, 2000.0, critical=False),
            expect_unique(["station_id", "ts", "pollutant"]),
        ],
    )


def weather_suite() -> Suite:
    return Suite(
        "weather",
        [
            expect_columns_exist(["station_id", "ts", "temp_c", "rh_pct"]),
            expect_values_between("temp_c", -50.0, 60.0),
            expect_values_between("rh_pct", 0.0, 100.0),
            expect_values_between("pbl_m", 0.0, 6000.0),
            expect_null_fraction_below("temp_c", 0.2),
        ],
    )


def grid_cell_suite() -> Suite:
    return Suite(
        "grid_cell",
        [
            expect_columns_exist(["city_id", "cell_id", "pop_total", "road_density"]),
            expect_row_count_min(1),
            expect_unique(["city_id", "cell_id"]),
            expect_values_between("pop_total", 0.0, 1e7),
            expect_values_between("road_density", 0.0, 100.0),
        ],
    )


SUITES: dict[str, Callable[[], Suite]] = {
    "observation": observation_suite,
    "weather": weather_suite,
    "grid_cell": grid_cell_suite,
}


def _load_table(table: str, limit: int = 50000) -> pd.DataFrame | None:
    """Best-effort load of a table from the live DB; None if unreachable."""
    try:
        from sqlalchemy import text

        from vayunetra.storage.db import get_engine

        with get_engine().begin() as conn:
            return pd.read_sql(text(f"SELECT * FROM {table} LIMIT :n"), conn, params={"n": limit})
    except Exception as e:
        log.warning("dq_table_unavailable", table=table, error=str(e)[:100])
        return None


def run_table_check(
    table: str, df: pd.DataFrame | None = None, out_dir: Path | None = None
) -> ValidationResult:
    """Validate one table; persist a JSON report under ``reports/data_quality/``."""
    if table not in SUITES:
        raise ValueError(f"unknown table '{table}'; known: {list(SUITES)}")
    suite = SUITES[table]()
    if df is None:
        df = _load_table(table)
    if df is None:
        # No live data — emit a 'skipped' style result (success=False, no crash).
        res = ValidationResult(
            table=table,
            ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            success=False,
            n_critical_failures=0,
            results=[
                ExpectationResult(
                    "data_available", None, False, critical=False, observed=None,
                    detail="live table unreachable; check skipped",
                )
            ],
        )
    else:
        res = validate(df, suite)

    out_dir = out_dir or (_REPO_ROOT / "reports" / "data_quality")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (out_dir / f"{table}_{stamp}.json").write_text(
        json.dumps(res.to_dict(), indent=2, default=str), encoding="utf-8"
    )
    log.info(
        "dq_check_done", table=table, success=res.success,
        critical_failures=res.n_critical_failures,
    )
    return res


def main() -> None:
    configure_logging()
    p = argparse.ArgumentParser(description="VayuNetra data-quality checks")
    p.add_argument("--table", choices=list(SUITES) + ["all"], default="all")
    args = p.parse_args()

    tables = list(SUITES) if args.table == "all" else [args.table]
    any_fail = False
    for t in tables:
        res = run_table_check(t)
        status = "PASS" if res.success else "FAIL/SKIP"
        print(f"[{status}] {t}: {res.n_critical_failures} critical failure(s)")
        for r in res.results:
            mark = "✓" if r.success else ("✗" if r.critical else "·")
            print(f"   {mark} {r.name} (observed={r.observed})")
        any_fail = any_fail or not res.success
    raise SystemExit(1 if any_fail else 0)


if __name__ == "__main__":
    main()
