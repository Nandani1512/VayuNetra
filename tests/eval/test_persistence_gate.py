"""Persistence-baseline gate.

Lands in CI from day one and is `pytest.skip`-ed until Phase 2 produces a real
model. Once a model is registered in MLflow under `forecast_delhi_pm25` with
stage `Production`, this test will actually load it and assert RMSE improves
≥ 15% over persistence at 24h. Removing this file is prohibited.
"""

from __future__ import annotations

import pytest


def _model_available() -> bool:
    try:
        import mlflow  # type: ignore

        client = mlflow.tracking.MlflowClient()
        versions = client.get_latest_versions("forecast_delhi_pm25", stages=["Production"])
        return bool(versions)
    except Exception:
        return False


@pytest.mark.skipif(not _model_available(), reason="Phase 2 model not registered yet")
def test_forecast_beats_persistence_24h():
    # Implemented in Phase 2 — see implementation_plan.md §Phase 2.5.
    raise AssertionError("persistence gate logic not yet implemented")
