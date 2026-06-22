"""LightGBM quantile trainer for per-station, per-pollutant forecasting.

One row per (station, ts). Three models per (city, pollutant, horizon) — one per
quantile (0.1, 0.5, 0.9). Categorical `station_id` lets a single model cover all
stations and cold-start for new sensors.

CLI:
  python -m vayunetra.models.forecast.lightgbm_trainer \
      --city delhi --pollutant pm25 --horizon 24
"""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import joblib
import lightgbm as lgb
import mlflow
import numpy as np
import pandas as pd
import yaml
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from vayunetra.common.config import get_settings
from vayunetra.common.logging import configure_logging, get_logger
from vayunetra.features.forecast_features import build_features, feature_columns

log = get_logger(__name__)

QUANTILES = (0.1, 0.5, 0.9)


@dataclass
class Split:
    train: pd.DataFrame
    val: pd.DataFrame
    test: pd.DataFrame


def _load_conf() -> dict[str, Any]:
    f = Path(__file__).resolve().parents[3].parent / "conf" / "model" / "forecast.yaml"
    if not f.exists():
        # When installed, conf is shipped next to the package
        f = Path("conf/model/forecast.yaml")
    return yaml.safe_load(f.read_text())


def _split_by_time(df: pd.DataFrame, val_days: int, test_days: int) -> Split:
    df = df.sort_values("ts_target")
    max_ts = df["ts_target"].max()
    test_start = max_ts - pd.Timedelta(days=test_days)
    val_start = test_start - pd.Timedelta(days=val_days)
    train = df[df["ts_target"] < val_start]
    val = df[(df["ts_target"] >= val_start) & (df["ts_target"] < test_start)]
    test = df[df["ts_target"] >= test_start]
    return Split(train=train, val=val, test=test)


def _bucket_aqi_pm25(v: float) -> int:
    # CPCB PM2.5 bands (24-hr avg). Used here for hourly purely as a coarse signal.
    if v <= 30:
        return 0  # Good
    if v <= 60:
        return 1  # Satisfactory
    if v <= 90:
        return 2  # Moderate
    if v <= 120:
        return 3  # Poor
    if v <= 250:
        return 4  # Very Poor
    return 5  # Severe


def _pod_far(y_true: np.ndarray, y_pred: np.ndarray, threshold: float = 90.0) -> tuple[float, float]:
    """Probability of Detection and False Alarm Ratio for 'Poor or worse'."""
    obs_event = y_true >= threshold
    pred_event = y_pred >= threshold
    hits = int(np.sum(obs_event & pred_event))
    misses = int(np.sum(obs_event & ~pred_event))
    false_alarms = int(np.sum(~obs_event & pred_event))
    pod = hits / (hits + misses) if (hits + misses) else float("nan")
    far = false_alarms / (hits + false_alarms) if (hits + false_alarms) else float("nan")
    return pod, far


def _train_one_quantile(
    train: pd.DataFrame,
    val: pd.DataFrame,
    feature_cols: list[str],
    alpha: float,
    params: dict[str, Any],
    n_estimators: int,
    early_stopping_rounds: int,
) -> lgb.Booster:
    lp = dict(params)
    lp.update({"objective": "quantile", "alpha": alpha, "metric": "quantile"})

    cat_cols = [c for c in feature_cols if str(train[c].dtype) == "category"]
    dtrain = lgb.Dataset(
        train[feature_cols],
        label=train["target"],
        categorical_feature=cat_cols if cat_cols else "auto",
        free_raw_data=False,
    )
    dval = lgb.Dataset(
        val[feature_cols],
        label=val["target"],
        categorical_feature=cat_cols if cat_cols else "auto",
        reference=dtrain,
        free_raw_data=False,
    )
    booster = lgb.train(
        lp,
        dtrain,
        num_boost_round=n_estimators,
        valid_sets=[dval],
        valid_names=["val"],
        callbacks=[lgb.early_stopping(early_stopping_rounds), lgb.log_evaluation(period=0)],
    )
    return booster


def _evaluate(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
    mae = float(mean_absolute_error(y_true, y_pred))
    try:
        r2 = float(r2_score(y_true, y_pred))
    except ValueError:
        r2 = float("nan")
    pod, far = _pod_far(y_true, y_pred)
    bucket_acc = float(np.mean(
        [_bucket_aqi_pm25(a) == _bucket_aqi_pm25(b) for a, b in zip(y_true, y_pred)]
    )) if len(y_true) else float("nan")
    return {
        "rmse": rmse,
        "mae": mae,
        "r2": r2,
        "pod_poor_plus": pod,
        "far_poor_plus": far,
        "bucket_accuracy": bucket_acc,
    }


def _persistence(df: pd.DataFrame) -> np.ndarray:
    # Persistence baseline: prediction = last observed value (`y` at issuance).
    return df["y"].to_numpy()


def train(
    city: str,
    pollutant: str,
    horizon_h: int,
    history_days: int = 180,
) -> dict[str, Any]:
    conf = _load_conf()
    lgb_params = conf["lightgbm"]
    n_estimators = int(lgb_params.pop("n_estimators", 2000))
    early_stop = int(lgb_params.pop("early_stopping_rounds", 100))
    val_days = int(conf["split"]["val_days"])
    test_days = int(conf["split"]["test_days"])

    until = datetime.now(timezone.utc)
    since = until - timedelta(days=history_days)

    log.info("build_features_start", city=city, pollutant=pollutant, horizon_h=horizon_h)
    df = build_features(city, pollutant, since, until, horizon_h)
    if df.empty:
        raise RuntimeError("no features built — run ingestion first")
    feat_cols = feature_columns(df)
    log.info("features_built", rows=len(df), cols=len(feat_cols))

    split = _split_by_time(df, val_days=val_days, test_days=test_days)
    if split.train.empty or split.val.empty or split.test.empty:
        raise RuntimeError(
            f"insufficient data: train={len(split.train)} val={len(split.val)} test={len(split.test)}"
        )

    settings = get_settings()
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    mlflow.set_experiment(f"forecast_{city}_{pollutant}")

    boosters: dict[float, lgb.Booster] = {}
    metrics: dict[str, float] = {}

    with mlflow.start_run(run_name=f"h{horizon_h}") as run:
        mlflow.log_params(
            {
                "city": city,
                "pollutant": pollutant,
                "horizon_h": horizon_h,
                "history_days": history_days,
                "rows_train": len(split.train),
                "rows_val": len(split.val),
                "rows_test": len(split.test),
                **{f"lgb_{k}": v for k, v in lgb_params.items()},
                "early_stopping_rounds": early_stop,
                "n_estimators": n_estimators,
            }
        )

        for q in QUANTILES:
            booster = _train_one_quantile(
                split.train, split.val, feat_cols, q, lgb_params, n_estimators, early_stop
            )
            boosters[q] = booster

        # Test-set predictions for all three quantiles.
        preds = {q: boosters[q].predict(split.test[feat_cols]) for q in QUANTILES}
        y_true = split.test["target"].to_numpy()
        best_alpha = 1.0

        m_p50 = _evaluate(y_true, preds[0.5])
        metrics.update({f"test_p50_{k}": v for k, v in m_p50.items()})

        # Persistence baseline on the same test slice.
        y_pers = _persistence(split.test)
        m_pers = _evaluate(y_true, y_pers)
        metrics.update({f"test_persistence_{k}": v for k, v in m_pers.items()})

        # Headline lift over persistence.
        if m_pers["rmse"] > 0:
            metrics["rmse_lift_vs_persistence"] = (m_pers["rmse"] - m_p50["rmse"]) / m_pers["rmse"]

        # Coverage of the p10/p90 band on test.
        in_band = float(np.mean((y_true >= preds[0.1]) & (y_true <= preds[0.9])))
        metrics["test_p10_p90_coverage"] = in_band

        mlflow.log_metrics(metrics)

        # Save and log the bundle as a single artifact (3 boosters + meta).
        with tempfile.TemporaryDirectory() as td:
            for q, b in boosters.items():
                b.save_model(str(Path(td) / f"booster_q{int(q*100):02d}.txt"))
            meta = {
                "city": city,
                "pollutant": pollutant,
                "horizon_h": horizon_h,
                "feature_columns": feat_cols,
                "categorical_columns": [c for c in feat_cols if str(df[c].dtype) == "category"],
                "quantiles": list(QUANTILES),
                "blend_alpha": float(best_alpha),
                "use_init_score": False,
                "trained_at": datetime.now(timezone.utc).isoformat(),
            }
            (Path(td) / "meta.json").write_text(json.dumps(meta, indent=2))
            mlflow.log_artifacts(td, artifact_path="model")

        # Register p50 booster as the primary served model.
        model_name = f"forecast_{city}_{pollutant}_h{horizon_h}"
        try:
            with tempfile.TemporaryDirectory() as td:
                joblib.dump({"meta": meta, "boosters_paths": list(meta["feature_columns"])}, Path(td) / "model.joblib")
                mlflow.log_artifact(str(Path(td) / "model.joblib"), artifact_path="serving")
            mlflow.set_tag("registered_name", model_name)
        except Exception as e:
            log.warning("model_registration_skipped", error=str(e))

        log.info(
            "training_done",
            run_id=run.info.run_id,
            rmse_p50=m_p50["rmse"],
            rmse_persistence=m_pers["rmse"],
            lift=metrics.get("rmse_lift_vs_persistence"),
        )

    return {"metrics": metrics, "run_id": run.info.run_id}


def predict(
    city: str,
    pollutant: str,
    horizon_h: int,
    ts_issued: datetime,
    history_days: int = 7,
) -> pd.DataFrame:
    """Loads the latest production-stage MLflow run for the (city, pollutant,
    horizon) and predicts at the given issuance time for every station.

    Returns a DataFrame: station_id, ts_target, p10, p50, p90.
    """
    settings = get_settings()
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    experiment = f"forecast_{city}_{pollutant}"
    exp = mlflow.get_experiment_by_name(experiment)
    if exp is None:
        raise RuntimeError(f"experiment {experiment} not found — train first")
    runs = mlflow.search_runs([exp.experiment_id], filter_string=f"tags.mlflow.runName='h{horizon_h}'",
                              order_by=["start_time DESC"], max_results=1)
    if runs.empty:
        raise RuntimeError(f"no runs for horizon {horizon_h}")
    run_id = runs.iloc[0]["run_id"]

    local = mlflow.artifacts.download_artifacts(run_id=run_id, artifact_path="model")
    meta = json.loads((Path(local) / "meta.json").read_text())
    boosters = {q: lgb.Booster(model_file=str(Path(local) / f"booster_q{int(q*100):02d}.txt"))
                for q in meta["quantiles"]}
    feat_cols = meta["feature_columns"]

    # Build features for the issuance window.
    until = ts_issued
    since = ts_issued - timedelta(days=history_days)
    df = build_features(city, pollutant, since, until, horizon_h)
    if df.empty:
        return df
    # Take only the most recent row per station (ts == ts_issued floored).
    latest = df.sort_values("ts").groupby("station_id").tail(1)
    X = latest[feat_cols]

    p10 = boosters[0.1].predict(X)
    p50 = boosters[0.5].predict(X)
    p90 = boosters[0.9].predict(X)

    out = pd.DataFrame(
        {
            "station_id": latest["station_id"].astype(str).values,
            "ts_target": latest["ts"].values + pd.Timedelta(hours=horizon_h),
            "p10": p10,
            "p50": p50,
            "p90": p90,
        }
    )
    return out


def main() -> None:
    configure_logging()
    p = argparse.ArgumentParser()
    p.add_argument("--city", required=True)
    p.add_argument("--pollutant", default="pm25")
    p.add_argument("--horizon", type=int, default=24)
    p.add_argument("--history-days", type=int, default=180)
    args = p.parse_args()
    res = train(args.city, args.pollutant, args.horizon, args.history_days)
    print(json.dumps(res["metrics"], indent=2))


if __name__ == "__main__":
    main()
