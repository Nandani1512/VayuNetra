"""Leave-one-station-out cross-validation for the forecast model.

For each station, retrain on all other stations and evaluate at the holdout.
Reports per-station RMSE/MAE + an aggregate table. Used to prove the 1 km
downscaling generalizes beyond stations the model has seen.

CLI:
  python -m vayunetra.models.forecast.cv --city delhi --pollutant pm25 --horizon 24
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import yaml
from sklearn.metrics import mean_absolute_error, mean_squared_error

from vayunetra.common.logging import configure_logging, get_logger
from vayunetra.features.forecast_features import build_features, feature_columns
from vayunetra.models.forecast.baselines import persistence_predict

log = get_logger(__name__)


def _load_lgb_params() -> dict:
    f = Path("conf/model/forecast.yaml")
    return yaml.safe_load(f.read_text())["lightgbm"]


def _train_p50(df: pd.DataFrame, feat_cols: list[str], params: dict) -> lgb.Booster:
    p = dict(params)
    n_estimators = int(p.pop("n_estimators", 1000))
    p.pop("early_stopping_rounds", None)
    p.update({"objective": "quantile", "alpha": 0.5, "metric": "quantile"})
    cat_cols = [c for c in feat_cols if str(df[c].dtype) == "category"]
    dtrain = lgb.Dataset(
        df[feat_cols],
        label=df["target"],
        categorical_feature=cat_cols if cat_cols else "auto",
        free_raw_data=False,
    )
    return lgb.train(p, dtrain, num_boost_round=n_estimators)


def run_loso_cv(
    city: str,
    pollutant: str,
    horizon_h: int,
    history_days: int = 90,
) -> pd.DataFrame:
    until = datetime.now(timezone.utc)
    since = until - timedelta(days=history_days)
    df = build_features(city, pollutant, since, until, horizon_h)
    if df.empty:
        raise RuntimeError("no features built")
    feat_cols = feature_columns(df)
    params = _load_lgb_params()

    stations = df["station_id"].astype(str).unique().tolist()
    log.info("loso_cv_start", n_stations=len(stations))

    rows = []
    for sid in stations:
        mask = df["station_id"].astype(str) == sid
        train = df[~mask]
        holdout = df[mask]
        if train.empty or holdout.empty:
            continue
        booster = _train_p50(train, feat_cols, params)
        y_pred = booster.predict(holdout[feat_cols])
        y_true = holdout["target"].to_numpy()
        rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
        mae = float(mean_absolute_error(y_true, y_pred))
        y_pers = persistence_predict(holdout)
        rmse_pers = float(np.sqrt(mean_squared_error(y_true, y_pers)))
        rows.append(
            {
                "station_id": sid,
                "n": int(len(holdout)),
                "rmse": rmse,
                "mae": mae,
                "rmse_persistence": rmse_pers,
                "lift_vs_persistence": (rmse_pers - rmse) / rmse_pers if rmse_pers > 0 else float("nan"),
            }
        )

    out = pd.DataFrame(rows).sort_values("rmse")
    log.info(
        "loso_cv_done",
        mean_rmse=float(out["rmse"].mean()),
        mean_lift=float(out["lift_vs_persistence"].mean()),
    )
    return out


def main() -> None:
    configure_logging()
    p = argparse.ArgumentParser()
    p.add_argument("--city", required=True)
    p.add_argument("--pollutant", default="pm25")
    p.add_argument("--horizon", type=int, default=24)
    p.add_argument("--history-days", type=int, default=90)
    p.add_argument("--out", default="reports/forecast/loso_cv.csv")
    args = p.parse_args()
    res = run_loso_cv(args.city, args.pollutant, args.horizon, args.history_days)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    res.to_csv(args.out, index=False)
    print(json.dumps(
        {
            "stations": int(len(res)),
            "mean_rmse": float(res["rmse"].mean()),
            "mean_lift_vs_persistence": float(res["lift_vs_persistence"].mean()),
            "out": args.out,
        },
        indent=2,
    ))


if __name__ == "__main__":
    main()
