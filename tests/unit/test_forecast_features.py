import pandas as pd

from vayunetra.features.forecast_features import _add_lags_and_rolling, _calendar_flags


def test_lags_filled_after_group_shift():
    ts = pd.date_range("2026-06-01", periods=80, freq="h", tz="UTC")
    df = pd.DataFrame({"station_id": ["s1"] * 80, "ts": ts, "y": range(80)})
    out = _add_lags_and_rolling(df)
    # 72h lag should have NaN for first 72 rows then valid values.
    assert out["y_lag_72h"].iloc[:72].isna().all()
    assert out["y_lag_72h"].iloc[72:].notna().all()


def test_calendar_flags_have_required_columns():
    ts = pd.Series(pd.date_range("2026-10-20", periods=24, freq="h", tz="UTC"))
    cal = _calendar_flags("delhi", ts)
    for col in (
        "sin_hour",
        "cos_hour",
        "sin_dow",
        "cos_dow",
        "is_weekend",
        "is_diwali",
        "is_crop_burn_season",
    ):
        assert col in cal.columns
    # Late October is in Delhi's crop burn window.
    assert cal["is_crop_burn_season"].sum() == len(cal)
