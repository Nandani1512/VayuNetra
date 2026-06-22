"""SHAP-based per-cell source attribution.

Loads the latest LUR booster, runs TreeSHAP on the feature vector at a target
cell + time, aggregates the contributions by source category using
conf/attribution/feature_to_source.yaml, and normalizes positive contributions
into a probability distribution. Negative contributions (e.g. wind dispersing
pollution) are reported separately as `mitigators`.

Returns:
  {
    "sources": { "vehicular": 0.42, "industrial": 0.18, ... },
    "mitigators": { "meteorological": 0.13 },
    "raw_shap": [{"feature": "road_density", "value": 12.3}, ...],
    "confidence": 0.78,
  }

Confidence is a heuristic: sum of |shap| / (|shap| + base_value), bounded [0,1].
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import lightgbm as lgb
import mlflow
import numpy as np
import pandas as pd
import shap
import yaml

from vayunetra.common.config import get_settings
from vayunetra.common.logging import get_logger
from vayunetra.features.lur_features import build_inference_rows

log = get_logger(__name__)

_CONF = Path("conf/attribution/feature_to_source.yaml")


def _load_mapping() -> dict[str, str]:
    if not _CONF.exists():
        return {}
    raw = yaml.safe_load(_CONF.read_text()) or {}
    return raw.get("feature_to_source", {})


def _load_lur_booster(city: str, pollutant: str) -> tuple[lgb.Booster, dict]:
    settings = get_settings()
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    exp = mlflow.get_experiment_by_name(f"lur_{city}_{pollutant}")
    if exp is None:
        raise RuntimeError(f"no LUR experiment for {city}/{pollutant}")
    runs = mlflow.search_runs([exp.experiment_id], order_by=["start_time DESC"], max_results=1)
    if runs.empty:
        raise RuntimeError("no LUR runs")
    local = mlflow.artifacts.download_artifacts(run_id=runs.iloc[0]["run_id"], artifact_path="model")
    meta = json.loads((Path(local) / "meta.json").read_text())
    booster = lgb.Booster(model_file=str(Path(local) / "booster.txt"))
    return booster, meta


def explain_cell(
    city: str,
    cell_id: str,
    ts: datetime,
    pollutant: str = "pm25",
) -> dict[str, Any]:
    booster, meta = _load_lur_booster(city, pollutant)
    feat_cols = meta["feature_columns"]
    grid_df = build_inference_rows(city, ts)
    row = grid_df[grid_df["cell_id"] == cell_id]
    if row.empty:
        raise ValueError(f"cell_id {cell_id} not in grid for {city}")

    X = row[[c for c in feat_cols if c in row.columns]]
    explainer = shap.TreeExplainer(booster)
    shap_values = explainer.shap_values(X)
    if isinstance(shap_values, list):
        shap_values = shap_values[0]
    sv = np.asarray(shap_values).ravel()
    base = float(explainer.expected_value if not isinstance(explainer.expected_value, (list, np.ndarray)) else np.mean(explainer.expected_value))

    mapping = _load_mapping()
    pos = defaultdict(float)
    neg = defaultdict(float)
    raw = []
    for col, val in zip(X.columns, sv):
        v = float(val)
        raw.append({"feature": col, "value": v})
        cat = mapping.get(col, "other")
        if v >= 0:
            pos[cat] += v
        else:
            neg[cat] += -v

    pos_total = sum(pos.values())
    sources = {k: v / pos_total for k, v in pos.items()} if pos_total > 0 else {}

    abs_total = pos_total + sum(neg.values())
    confidence = abs_total / (abs_total + abs(base) + 1e-9)
    confidence = float(max(0.0, min(confidence, 1.0)))

    raw.sort(key=lambda r: abs(r["value"]), reverse=True)
    return {
        "city": city,
        "cell_id": cell_id,
        "ts": ts.isoformat() if isinstance(ts, datetime) else str(ts),
        "pollutant": pollutant,
        "base_value": base,
        "sources": dict(sorted(sources.items(), key=lambda kv: -kv[1])),
        "mitigators": dict(sorted({k: v / sum(neg.values()) for k, v in neg.items()}.items(), key=lambda kv: -kv[1])) if neg else {},
        "raw_shap": raw[:12],
        "confidence": confidence,
    }
