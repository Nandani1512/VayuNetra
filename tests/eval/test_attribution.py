"""Attribution sanity test.

Compares blended category mix to SAFAR 2018 Delhi PM2.5 source-apportionment.
Skips if LUR model / data not present. Threshold loose (≤25 pp deviation) since
SHAP overlays and PMF use different methodologies.
"""

from __future__ import annotations

import pytest

SAFAR_DELHI_PM25 = {
    "vehicular": 0.41,
    "industrial": 0.18,
    "biomass_burning": 0.18,
    "construction_dust": 0.08,
    "secondary": 0.10,
    "dust_mixed": 0.05,
}


def _available() -> bool:
    try:
        import mlflow  # type: ignore

        client = mlflow.tracking.MlflowClient()
        return any(client.search_experiments(filter_string="name='lur_delhi_pm25'"))
    except Exception:
        return False


@pytest.mark.skipif(not _available(), reason="LUR not yet trained")
def test_attribution_deviation_below_threshold():
    import asyncio
    from datetime import datetime, timezone

    from vayunetra.models.attribution.overlay import attribute_cell
    from vayunetra.storage.db import get_engine
    from sqlalchemy import text

    with get_engine().begin() as conn:
        row = conn.execute(
            text("SELECT cell_id FROM grid_cell WHERE city_id='delhi' LIMIT 1")
        ).fetchone()
    assert row, "delhi grid empty — run build_grid"

    out = asyncio.run(
        attribute_cell("delhi", row.cell_id, datetime.now(timezone.utc), "pm25")
    )
    blended = out["blended_sources"]
    total_dev = 0.0
    for cat, ref in SAFAR_DELHI_PM25.items():
        total_dev += abs(blended.get(cat, 0.0) - ref)
    avg_dev = total_dev / len(SAFAR_DELHI_PM25)
    assert avg_dev <= 0.25, f"mean category deviation {avg_dev:.3f} exceeds 0.25"
