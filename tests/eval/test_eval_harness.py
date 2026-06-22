"""Tests for the Phase 9 evaluation harness (``vayunetra.eval.run``).

These run without a live PostGIS/MLflow/Redis stack — they exercise the
graceful-degradation paths (synthetic forecast benchmark, frozen demo
fixtures, in-process latency) and assert that a complete ``summary.md`` plus
artifacts are always produced.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from vayunetra.eval import run as harness


def test_chrf_identity_and_disjoint() -> None:
    s = "PM2.5 in Delhi is high today"
    assert harness._chrf(s, s) == pytest.approx(100.0, abs=1e-6)
    assert harness._chrf("hello world", "xyz qrs tuv") < 30.0


def test_metric_helpers() -> None:
    import numpy as np

    y = np.array([1.0, 2.0, 3.0, 4.0])
    assert harness._rmse(y, y) == 0.0
    assert harness._mae(y, y) == 0.0
    assert harness._r2(y, y) == pytest.approx(1.0)


def test_synthetic_forecast_beats_persistence() -> None:
    """The seeded synthetic benchmark must genuinely beat persistence."""
    df = harness._synthetic_forecast_table(seed=7)
    assert set(df["horizon_h"]) == set(harness.HORIZONS)
    assert (df["rmse_model"] < df["rmse_persistence"]).all()
    lift24 = float(df[df["horizon_h"] == 24]["lift_vs_persistence"].iloc[0])
    assert lift24 >= harness.TARGET_LIFT_24H


def test_eval_advisory(tmp_path) -> None:
    res = harness.eval_advisory(tmp_path)
    assert res.name == "advisory"
    assert res.metrics["citation_rate"] == pytest.approx(1.0)
    assert (tmp_path / "advisory_coverage.csv").exists()
    assert (tmp_path / "advisory_coverage.png").exists()


def test_eval_latency_in_process(tmp_path) -> None:
    res = harness.eval_latency(tmp_path, n=3)
    assert res.name == "latency"
    assert res.status in {"ok", "warn"}
    assert (tmp_path / "latency.csv").exists()
    # healthz must always be reachable in-process.
    by_ep = {r["endpoint"]: r for r in res.metrics["per_endpoint"]}
    assert by_ep["healthz"]["status"] == 200


def test_eval_attribution_fixture(tmp_path) -> None:
    res = harness.eval_attribution(tmp_path, city="delhi", pollutant="pm25")
    # Either live or demo-fixture, but never crash; deviation must be finite.
    assert res.name == "attribution"
    if res.status != "skipped":
        assert 0.0 <= res.metrics["mean_deviation"] <= 1.0
        assert (tmp_path / "attribution_deviation.csv").exists()


def test_full_run_writes_summary(tmp_path) -> None:
    summary = harness.run("all", city="delhi", pollutant="pm25", out_root=tmp_path)
    assert summary.exists()
    text = summary.read_text(encoding="utf-8")
    assert "# VayuNetra — Evaluation Summary" in text
    assert "## Scorecard" in text
    for section in ("Forecast", "Attribution", "Enforcement", "Advisory", "Latency"):
        assert f"## {section}" in text
    # Machine-readable sidecar must parse and cover all 5 steps.
    sidecar = summary.parent / "results.json"
    data = json.loads(sidecar.read_text())
    assert {r["name"] for r in data} == set(harness._STEPS)


class TestResponseTimeBenchmark:
    """G11: signal-to-intervention response-time SLOs."""

    @pytest.fixture(scope="class")
    def bench_results(self):
        import sys, importlib.util
        spec = importlib.util.spec_from_file_location(
            "bench", str(pathlib.Path(__file__).resolve().parents[2] / "scripts" / "benchmark_response_time.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.run_benchmark()

    def test_total_p95_under_30s(self, bench_results):
        assert bench_results["total"]["p95"] < 30.0

    def test_forecast_p95_under_5s(self, bench_results):
        assert bench_results["forecast"]["p95"] < 5.0

    def test_advisory_p95_under_2s(self, bench_results):
        assert bench_results["advisory"]["p95"] < 2.0
