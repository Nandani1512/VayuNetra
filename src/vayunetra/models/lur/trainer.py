"""LightGBM LUR trainer.

Target: observed surface PM2.5 (or any pollutant). Features per
`lur_features.build_training_rows`. Reports leave-one-station-out CV as the
primary acceptance metric: rmse / std(observed) ≤ 0.7.

CLI:
  python -m vayunetra.models.lur.trainer --city delhi --pollutant pm25
"""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import lightgbm as lgb
import mlflow
import numpy as np
import pandas as pd
import yaml
from sklearn.metrics import mean_absolute_error, mean_squared_error

from vayunetra.common.config import get_settings
from vayunetra.common.logging import configure_logging, get_logger
from vayunetra.features.lur_features import build_training_rows, lur_feature_columns

log = get_logger(__name__)


def _load_conf() -> dict:
    return yaml.safe_load(Path("conf/model/lur.yaml").read_text())


def _train_one(df: pd.DataFrame, feat_cols: list[str], lgb_params: dict,
               n_estimators: int) -> lgb.Booster:
    p = dict(lgb_params)
    p.update({"objective": "regression", "metric": "rmse"})
    cat = [c for c in feat_cols if str(df[c].dtype) == "category"]
    dset = lgb.Dataset(
        df[feat_cols],
        label=df["target"],
        categorical_feature=cat if cat else "auto",
        free_raw_data=False,
    )
    return lgb.train(p, dset, num_boost_round=n_estimators)


def loso_cv(df: pd.DataFrame, feat_cols: list[str], lgb_params: dict,
            n_estimators: int, max_stations: int = 15) -> pd.DataFrame:
    rows = []
    stations = df["station_id"].astype(str).unique().tolist()
    # Cap CV iterations for hackathon demo runs; full LOSO is O(N) training runs.
    stations = stations[:max_stations]
    for sid in stations:
        mask = df["station_id"].astype(str) == sid
        train = df[~mask]
        hold = df[mask]
        if train.empty or hold.empty:
            continue
        booster = _train_one(train, feat_cols, lgb_params, n_estimators)
        y_pred = booster.predict(hold[feat_cols])
        y_true = hold["target"].to_numpy()
        rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
        mae = float(mean_absolute_error(y_true, y_pred))
        std = float(np.std(y_true)) if len(y_true) > 1 else float("nan")
        rows.append(
            {
                "station_id": sid,
                "n": int(len(hold)),
                "rmse": rmse,
                "mae": mae,
                "std_observed": std,
                "rmse_over_std": rmse / std if std and std > 0 else float("nan"),
            }
        )
    return pd.DataFrame(rows).sort_values("rmse")


def train(city: str, pollutant: str = "pm25", history_days: int = 180) -> dict:
    conf = _load_conf()
    lgb_params = dict(conf["lightgbm"])
    n_estimators = int(lgb_params.pop("n_estimators", 1500))
    lgb_params.pop("early_stopping_rounds", None)

    until = datetime.now(timezone.utc)
    since = until - timedelta(days=history_days)
    df = build_training_rows(city, pollutant, since, until)
    if df.empty:
        raise RuntimeError("no LUR training rows; check ingestion")
    feat_cols = lur_feature_columns(df)
    log.info("lur_rows", n=len(df), features=len(feat_cols))

    settings = get_settings()
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    mlflow.set_experiment(f"lur_{city}_{pollutant}")

    with mlflow.start_run(run_name=f"{city}_{pollutant}") as run:
        mlflow.log_params({
            "city": city,
            "pollutant": pollutant,
            "rows": int(len(df)),
            "features": len(feat_cols),
            "n_estimators": n_estimators,
            **{f"lgb_{k}": v for k, v in lgb_params.items()},
        })

        # Fit one model on all stations for grid inference …
        booster = _train_one(df, feat_cols, lgb_params, n_estimators)

        # … and report LOSO-CV as the acceptance metric.
        cv = loso_cv(df, feat_cols, lgb_params, n_estimators)
        cv_path = Path("reports/lur/loso_cv.csv")
        cv_path.parent.mkdir(parents=True, exist_ok=True)
        cv.to_csv(cv_path, index=False)
        mlflow.log_artifact(str(cv_path), artifact_path="loso_cv")

        mean_rmse = float(cv["rmse"].mean())
        mean_rmse_over_std = float(cv["rmse_over_std"].mean())
        max_rmse_over_std = float(cv["rmse_over_std"].max())
        mlflow.log_metrics(
            {
                "loso_mean_rmse": mean_rmse,
                "loso_mean_rmse_over_std": mean_rmse_over_std,
                "loso_max_rmse_over_std": max_rmse_over_std,
                "loso_stations": int(len(cv)),
            }
        )

        with tempfile.TemporaryDirectory() as td:
            model_path = Path(td) / "booster.txt"
            booster.save_model(str(model_path))
            meta = {
                "city": city,
                "pollutant": pollutant,
                "feature_columns": feat_cols,
                "categorical_columns": [c for c in feat_cols if str(df[c].dtype) == "category"],
                "trained_at": datetime.now(timezone.utc).isoformat(),
            }
            (Path(td) / "meta.json").write_text(json.dumps(meta, indent=2))
            mlflow.log_artifacts(td, artifact_path="model")

        accept = float(conf["acceptance"]["max_rmse_over_std"])
        passed = mean_rmse_over_std <= accept
        mlflow.set_tag("loso_passed", str(passed).lower())
        log.info(
            "lur_done",
            run_id=run.info.run_id,
            mean_rmse=mean_rmse,
            mean_rmse_over_std=mean_rmse_over_std,
            accept_thresh=accept,
            passed=passed,
        )

    return {
        "run_id": run.info.run_id,
        "mean_rmse": mean_rmse,
        "mean_rmse_over_std": mean_rmse_over_std,
        "passed_acceptance": passed,
    }


def main() -> None:
    configure_logging()
    p = argparse.ArgumentParser()
    p.add_argument("--city", required=True)
    p.add_argument("--pollutant", default="pm25")
    p.add_argument("--history-days", type=int, default=180)
    args = p.parse_args()
    print(json.dumps(train(args.city, args.pollutant, args.history_days), indent=2))


if __name__ == "__main__":
    main()
