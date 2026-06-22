"""Tests for the Phase 10 MLOps modules (data quality + Evidently drift)."""

from __future__ import annotations

import pandas as pd

from vayunetra.mlops import data_quality as dq
from vayunetra.mlops import drift


def test_data_quality_passes_clean_observations() -> None:
    df = pd.DataFrame(
        {
            "station_id": ["a", "b", "c"],
            "ts": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
            "pollutant": ["pm25", "pm25", "pm25"],
            "value": [50.0, 60.0, 70.0],
            "unit": ["ug/m3"] * 3,
        }
    )
    res = dq.validate(df, dq.observation_suite())
    assert res.success
    assert res.n_critical_failures == 0


def test_data_quality_flags_nulls_and_duplicates() -> None:
    df = pd.DataFrame(
        {
            "station_id": ["a", "a"],
            "ts": pd.to_datetime(["2026-01-01", "2026-01-01"]),
            "pollutant": ["pm25", "pm25"],
            "value": [None, 50000.0],
            "unit": ["ug/m3", "ug/m3"],
        }
    )
    res = dq.validate(df, dq.observation_suite())
    assert not res.success
    # null fraction + duplicate uniqueness are both critical.
    assert res.n_critical_failures >= 2
    # out-of-bounds value is a non-critical expectation that still fails.
    oob = next(r for r in res.results if r.name.startswith("0.0 <= value"))
    assert not oob.success


def test_all_suites_constructible() -> None:
    for name, factory in dq.SUITES.items():
        suite = factory()
        assert suite.table == name
        assert suite.expectations


def test_drift_synthetic_report(tmp_path) -> None:
    summary = drift.run(out_dir=tmp_path)
    assert summary["source"] == "synthetic"
    # The synthetic pair injects shifts in 'value' and 'pbl_m', so drift must
    # be detected on at least those columns.
    assert summary["drifted_columns"] is not None
    assert summary["drifted_columns"] >= 2
    # HTML + JSON artifacts written.
    assert (tmp_path / summary["html"]).exists()
    assert any(p.suffix == ".json" for p in tmp_path.iterdir())


def test_drift_summary_parser() -> None:
    snap = {
        "metrics": [
            {"metric_name": "DriftedColumnsCount(drift_share=0.5)", "value": {"count": 2.0, "share": 0.33}},
            {
                "metric_name": "ValueDrift(column=value)",
                "config": {"column": "value"},
                "value": 0.001,
            },
        ]
    }
    out = drift._parse_summary(snap)
    assert out["drifted_columns"] == 2.0
    assert out["drifted_share"] == 0.33
    assert out["column_pvalues"]["value"] == 0.001
