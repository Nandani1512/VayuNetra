"""Feature/data drift reporting with Evidently (Phase 10).

Compares a *reference* feature distribution (the training window) against a
*current* window (e.g. the last 7 days) and emits:

  - ``reports/drift/drift_<ts>.html`` — the full Evidently report, and
  - ``reports/drift/drift_<ts>.json`` — a compact machine-readable summary
    (drifted-column count + share + per-column p-values).

Graceful degradation: when the live feature view is unreachable, a seeded
synthetic reference/current pair is used so the report mechanism is always
demonstrable. The data source is recorded in the summary.

Built against Evidently 0.7.x (``from evidently import Report, Dataset,
DataDefinition``; ``from evidently.presets import DataDriftPreset``).

CLI:
  python -m vayunetra.mlops.drift --city delhi --pollutant pm25
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from vayunetra.common.logging import configure_logging, get_logger

log = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DRIFT_DIR = _REPO_ROOT / "reports" / "drift"

# Feature columns we monitor for drift (subset of the forecast feature view).
DEFAULT_FEATURES: tuple[str, ...] = (
    "value",
    "temp_c",
    "rh_pct",
    "wind_u",
    "wind_v",
    "pbl_m",
)


def _synthetic_pair(seed: int = 11) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Seeded reference/current frames with an injected shift in two columns."""
    rng = np.random.default_rng(seed)
    n = 800
    ref = pd.DataFrame(
        {
            "value": rng.normal(70, 15, n),
            "temp_c": rng.normal(30, 5, n),
            "rh_pct": rng.uniform(20, 90, n),
            "wind_u": rng.normal(0, 2, n),
            "wind_v": rng.normal(0, 2, n),
            "pbl_m": rng.normal(800, 200, n),
        }
    )
    cur = pd.DataFrame(
        {
            "value": rng.normal(95, 20, n),  # injected pollution shift
            "temp_c": rng.normal(31, 5, n),
            "rh_pct": rng.uniform(20, 90, n),
            "wind_u": rng.normal(0, 2, n),
            "wind_v": rng.normal(0, 2, n),
            "pbl_m": rng.normal(600, 180, n),  # injected PBL shift
        }
    )
    return ref, cur, list(ref.columns)


def _live_pair(
    city: str, pollutant: str, features: tuple[str, ...]
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]] | None:
    """Pull reference (training window) vs current (last 7d) from the feature view."""
    try:
        from sqlalchemy import text

        from vayunetra.storage.db import get_engine

        cols = ", ".join(features)
        base = f"""
            SELECT {cols} FROM mv_forecast_features
            WHERE city_id = :city AND pollutant = :pollutant
        """
        with get_engine().begin() as conn:
            cur = pd.read_sql(
                text(base + " AND ts > now() - interval '7 days'"),
                conn,
                params={"city": city, "pollutant": pollutant},
            )
            ref = pd.read_sql(
                text(base + " AND ts <= now() - interval '7 days'"),
                conn,
                params={"city": city, "pollutant": pollutant},
            )
        if cur.empty or ref.empty:
            return None
        present = [c for c in features if c in ref.columns and c in cur.columns]
        return ref[present], cur[present], present
    except Exception as e:
        log.warning("drift_live_unavailable", error=str(e)[:120])
        return None


def _parse_summary(snapshot_json: dict[str, Any]) -> dict[str, Any]:
    """Extract drifted-count/share and per-column p-values from Evidently JSON."""
    drifted_count: float | None = None
    drifted_share: float | None = None
    columns: dict[str, float] = {}
    for m in snapshot_json.get("metrics", []):
        name = str(m.get("metric_name", ""))
        val = m.get("value")
        if name.startswith("DriftedColumnsCount") and isinstance(val, dict):
            drifted_count = float(val.get("count", 0.0))
            drifted_share = float(val.get("share", 0.0))
        elif name.startswith("ValueDrift") and isinstance(val, (int, float)):
            cfg = m.get("config", {})
            col = cfg.get("column", name)
            columns[str(col)] = float(val)
    return {
        "drifted_columns": drifted_count,
        "drifted_share": drifted_share,
        "column_pvalues": columns,
    }


def generate_drift_report(
    reference: pd.DataFrame,
    current: pd.DataFrame,
    columns: list[str],
    out_dir: Path | None = None,
    *,
    source: str = "live",
) -> dict[str, Any]:
    """Run Evidently DataDriftPreset and persist HTML + JSON summary."""
    from evidently import DataDefinition, Dataset, Report
    from evidently.presets import DataDriftPreset

    out_dir = out_dir or _DRIFT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    numeric = [c for c in columns if pd.api.types.is_numeric_dtype(reference[c])]
    data_def = DataDefinition(numerical_columns=numeric)
    ref_ds = Dataset.from_pandas(reference[numeric], data_definition=data_def)
    cur_ds = Dataset.from_pandas(current[numeric], data_definition=data_def)

    report = Report([DataDriftPreset()])
    snapshot = report.run(reference_data=ref_ds, current_data=cur_ds)

    html_path = out_dir / f"drift_{stamp}.html"
    snapshot.save_html(str(html_path))

    raw = snapshot.json()
    parsed_raw = json.loads(raw) if isinstance(raw, str) else raw
    summary = _parse_summary(parsed_raw)
    summary.update(
        {
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": source,
            "n_reference": int(len(reference)),
            "n_current": int(len(current)),
            "columns": numeric,
            "html": html_path.name,
        }
    )
    json_path = out_dir / f"drift_{stamp}.json"
    json_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    log.info(
        "drift_report_done",
        source=source,
        drifted_columns=summary.get("drifted_columns"),
        drifted_share=summary.get("drifted_share"),
        html=str(html_path),
    )
    return summary


def run(
    city: str = "delhi",
    pollutant: str = "pm25",
    features: tuple[str, ...] = DEFAULT_FEATURES,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    pair = _live_pair(city, pollutant, features)
    source = "live"
    if pair is None:
        log.warning("drift_using_synthetic", reason="live feature view unavailable")
        pair = _synthetic_pair()
        source = "synthetic"
    ref, cur, cols = pair
    return generate_drift_report(ref, cur, cols, out_dir=out_dir, source=source)


def main() -> None:
    configure_logging()
    p = argparse.ArgumentParser(description="VayuNetra feature-drift report (Evidently)")
    p.add_argument("--city", default="delhi")
    p.add_argument("--pollutant", default="pm25")
    p.add_argument("--out-dir", default=str(_DRIFT_DIR))
    args = p.parse_args()
    summary = run(args.city, args.pollutant, out_dir=Path(args.out_dir))
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
