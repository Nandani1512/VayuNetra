"""Rolling-origin walk-forward evaluation of the forecast model vs baselines.

For each issue time t over the last `eval_days`:
  - load the trained model for (city, pollutant, horizon)
  - predict y(t + h)
  - compare to observed y(t + h) and to persistence/climatology baselines

Emits a per-horizon RMSE/MAE/lift table and a PNG.

CLI:
  python -m vayunetra.eval.walk_forward --city delhi --pollutant pm25
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error

from vayunetra.common.logging import configure_logging, get_logger
from vayunetra.features.forecast_features import build_features, feature_columns
from vayunetra.models.forecast.baselines import climatology_predict, persistence_predict

log = get_logger(__name__)

HORIZONS = (24, 48, 72)


def _load_booster_for(city: str, pollutant: str, horizon_h: int):
    """Best-effort: load the latest MLflow run for this (city, pollutant, h)."""
    import mlflow  # type: ignore
    import lightgbm as lgb  # type: ignore

    exp_name = f"forecast_{city}_{pollutant}"
    exp = mlflow.get_experiment_by_name(exp_name)
    if exp is None:
        return None, None
    runs = mlflow.search_runs(
        [exp.experiment_id],
        filter_string=f"tags.mlflow.runName='h{horizon_h}'",
        order_by=["start_time DESC"],
        max_results=1,
    )
    if runs.empty:
        return None, None
    local = mlflow.artifacts.download_artifacts(
        run_id=runs.iloc[0]["run_id"], artifact_path="model"
    )
    meta = json.loads((Path(local) / "meta.json").read_text())
    booster = lgb.Booster(model_file=str(Path(local) / "booster_q50.txt"))
    return booster, meta


def _metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    if len(y_true) == 0:
        return {"rmse": float("nan"), "mae": float("nan"), "n": 0}
    return {
        "rmse": float(np.sqrt(mean_squared_error(y_true, y_pred))),
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "n": int(len(y_true)),
    }


def walk_forward(
    city: str,
    pollutant: str,
    eval_days: int = 14,
    history_days: int = 90,
    horizons: tuple[int, ...] = HORIZONS,
) -> pd.DataFrame:
    until = datetime.now(timezone.utc)
    since = until - timedelta(days=history_days + eval_days)
    rows: list[dict] = []

    for h in horizons:
        df = build_features(city, pollutant, since, until, h)
        if df.empty:
            log.warning("no_features_for_horizon", horizon_h=h)
            continue
        # Restrict to the evaluation window.
        cutoff = until - timedelta(days=eval_days)
        ev = df[df["ts_target"] >= cutoff].copy()
        if ev.empty:
            log.warning("empty_eval_window", horizon_h=h)
            continue

        y_true = ev["target"].to_numpy()
        y_pers = persistence_predict(ev)
        y_clim = climatology_predict(ev)

        booster, meta = _load_booster_for(city, pollutant, h)
        if booster is not None and meta is not None:
            cols = [c for c in meta["feature_columns"] if c in ev.columns]
            y_model = booster.predict(ev[cols])
            m_model = _metrics(y_true, y_model)
        else:
            m_model = {"rmse": float("nan"), "mae": float("nan"), "n": int(len(y_true))}

        m_pers = _metrics(y_true, y_pers)
        m_clim = _metrics(y_true, y_clim)
        rows.append(
            {
                "horizon_h": h,
                "n": m_pers["n"],
                "rmse_model": m_model["rmse"],
                "rmse_persistence": m_pers["rmse"],
                "rmse_climatology": m_clim["rmse"],
                "mae_model": m_model["mae"],
                "lift_vs_persistence": (
                    (m_pers["rmse"] - m_model["rmse"]) / m_pers["rmse"]
                    if m_pers["rmse"] > 0 and not np.isnan(m_model["rmse"])
                    else float("nan")
                ),
            }
        )

    return pd.DataFrame(rows)


def plot_results(df: pd.DataFrame, out_png: Path) -> None:
    if df.empty:
        return
    fig, ax = plt.subplots(figsize=(7, 4))
    x = df["horizon_h"]
    ax.plot(x, df["rmse_persistence"], "o-", label="Persistence")
    ax.plot(x, df["rmse_climatology"], "s-", label="Climatology")
    if df["rmse_model"].notna().any():
        ax.plot(x, df["rmse_model"], "^-", label="Model (p50)")
    ax.set_xlabel("Horizon (h)")
    ax.set_ylabel("RMSE")
    ax.set_title("Walk-forward RMSE vs baselines")
    ax.legend()
    ax.grid(True, alpha=0.3)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_png, dpi=120)
    plt.close(fig)


def main() -> None:
    configure_logging()
    p = argparse.ArgumentParser()
    p.add_argument("--city", required=True)
    p.add_argument("--pollutant", default="pm25")
    p.add_argument("--eval-days", type=int, default=14)
    p.add_argument("--history-days", type=int, default=90)
    p.add_argument("--out", default="reports/forecast/walk_forward.csv")
    args = p.parse_args()

    df = walk_forward(args.city, args.pollutant, args.eval_days, args.history_days)
    out_csv = Path(args.out)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index=False)
    plot_results(df, out_csv.with_suffix(".png"))
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
