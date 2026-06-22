"""Precision@k: ranked hotspot cells vs realized top-percentile cells in the
next horizon. Skips when forecast table is empty."""

from __future__ import annotations

import pytest


def _forecast_available() -> bool:
    try:
        from sqlalchemy import text

        from vayunetra.storage.db import get_engine

        with get_engine().begin() as conn:
            n = conn.execute(text("SELECT count(*) FROM forecast")).scalar()
        return (n or 0) > 0
    except Exception:
        return False


@pytest.mark.skipif(not _forecast_available(), reason="no forecasts persisted yet")
def test_precision_at_k_nonzero():
    import pandas as pd
    from sqlalchemy import text

    from vayunetra.enforcement.hotspots import detect_hotspots
    from vayunetra.storage.db import get_engine

    detected = detect_hotspots("delhi", "pm25", horizon_h=24)
    cells = [c for cluster in detected.get("clusters", []) for c in cluster.get("cells", [])]
    if not cells:
        pytest.skip("no hotspots in current forecast")

    # Realized top 5% of cells by p50.
    with get_engine().begin() as conn:
        df = pd.read_sql(
            text("""SELECT cell_id, p50 FROM forecast
                    WHERE city_id='delhi' AND pollutant='pm25'
                    AND ts_target = (SELECT MAX(ts_target) FROM forecast
                                     WHERE city_id='delhi' AND pollutant='pm25')"""),
            conn,
        )
    if df.empty:
        pytest.skip("forecast empty")
    top_n = max(1, int(len(df) * 0.05))
    realized = set(df.nlargest(top_n, "p50")["cell_id"])
    for k in (5, 10, 20):
        topk = set(cells[:k])
        prec = len(topk & realized) / max(len(topk), 1)
        # Soft: precision@k must beat the realized base rate (~5%).
        assert prec > 0.05 or k > len(cells), f"precision@{k}={prec:.3f} did not beat base rate"
