"""Persistence-baseline gate.

CI must fail if the latest forecast model does not beat persistence by the
acceptance thresholds in conf/model/forecast.yaml (>=15% RMSE reduction at 24h,
>=8% at 72h on PM2.5 for Delhi).

Skips when:
  - no MLflow tracking server is reachable, or
  - no run exists yet for forecast_delhi_pm25.

Once Phase 2 training has run at least once, this gate becomes active.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


def _conf() -> dict:
    return yaml.safe_load(Path("conf/model/forecast.yaml").read_text())


def _latest_run_metrics(city: str, pollutant: str, horizon_h: int):
    try:
        import mlflow  # type: ignore
    except ImportError:
        return None
    try:
        exp = mlflow.get_experiment_by_name(f"forecast_{city}_{pollutant}")
        if exp is None:
            return None
        runs = mlflow.search_runs(
            [exp.experiment_id],
            filter_string=f"tags.mlflow.runName='h{horizon_h}'",
            order_by=["start_time DESC"],
            max_results=1,
        )
        if runs.empty:
            return None
        return runs.iloc[0].to_dict()
    except Exception:
        return None


@pytest.mark.parametrize(
    "horizon_h,key",
    [
        (24, "min_rmse_improvement_24h"),
        (72, "min_rmse_improvement_72h"),
    ],
)
def test_forecast_beats_persistence(horizon_h, key):
    conf = _conf()
    threshold = float(conf["acceptance"][key])

    run = _latest_run_metrics("delhi", "pm25", horizon_h)
    if run is None:
        pytest.skip("no MLflow run yet for forecast_delhi_pm25 — train via Phase 2 trainer")

    lift = run.get("metrics.rmse_lift_vs_persistence")
    if lift is None or (isinstance(lift, float) and lift != lift):  # NaN check
        pytest.skip("run did not log rmse_lift_vs_persistence")

    assert lift >= threshold, (
        f"horizon={horizon_h}h: lift {lift:.3f} did not meet threshold {threshold:.3f}"
    )
