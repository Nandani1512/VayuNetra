import numpy as np
import pandas as pd

from vayunetra.models.forecast.baselines import climatology_predict, persistence_predict


def _fake_df():
    ts = pd.date_range("2026-06-01", periods=10, freq="h", tz="UTC")
    return pd.DataFrame(
        {
            "station_id": ["s1"] * 10,
            "ts": ts,
            "ts_target": ts + pd.Timedelta(hours=24),
            "y": np.arange(10, dtype=float),
            "target": np.arange(10, dtype=float) + 24,
        }
    )


def test_persistence_returns_current_value():
    df = _fake_df()
    np.testing.assert_array_equal(persistence_predict(df), df["y"].to_numpy())


def test_climatology_is_finite_and_correct_length():
    df = _fake_df()
    preds = climatology_predict(df)
    assert preds.shape == (len(df),)
    assert np.isfinite(preds).all()


def test_climatology_falls_back_for_singleton_bucket():
    ts = pd.date_range("2026-06-01", periods=1, freq="h", tz="UTC")
    df = pd.DataFrame(
        {
            "station_id": ["s1"],
            "ts": ts,
            "ts_target": ts + pd.Timedelta(hours=24),
            "y": [42.0],
            "target": [50.0],
        }
    )
    preds = climatology_predict(df)
    # Single row → falls back to per-station mean of y.
    assert np.isfinite(preds).all()
    assert preds[0] == 42.0
