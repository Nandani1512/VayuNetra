"""End-to-end enforcement service.

Composes hotspot detection + ranking + (optional) attribution + LLM brief.
Used by both the FastAPI /enforce endpoint and the demo scripts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from vayunetra.common.logging import get_logger
from vayunetra.enforcement.brief import generate_brief
from vayunetra.enforcement.hotspots import detect_hotspots
from vayunetra.enforcement.ranker import rank_clusters
from vayunetra.models.attribution.overlay import attribute_cell

log = get_logger(__name__)


async def enforce(
    city: str,
    pollutant: str = "pm25",
    horizon_h: int = 24,
    top_k: int = 5,
    user_id: str = "demo",
    with_attribution: bool = True,
    with_brief: bool = True,
) -> dict[str, Any]:
    detected = detect_hotspots(city, pollutant=pollutant, horizon_h=horizon_h)
    ranked = rank_clusters(city, detected.get("clusters", []))[:top_k]

    items: list[dict[str, Any]] = []
    for c in ranked:
        item: dict[str, Any] = {"cluster": c}
        attribution = None
        if with_attribution and c["cells"]:
            try:
                attribution = await attribute_cell(
                    city,
                    cell_id=c["cells"][0],
                    ts=datetime.now(timezone.utc),
                    pollutant=pollutant,
                )
                item["attribution"] = attribution
            except Exception as e:
                log.warning("attribution_skipped", error=str(e))
                item["attribution_error"] = str(e)

        if with_brief:
            try:
                brief_out = await generate_brief(
                    user_id=user_id,
                    city=city,
                    cluster=c,
                    attribution=attribution,
                    horizon_h=horizon_h,
                    pollutant=pollutant,
                )
                item["brief"] = brief_out["brief"]
                item["llm"] = brief_out["model"]
            except Exception as e:
                log.warning("brief_failed", error=str(e))
                item["brief_error"] = str(e)
        items.append(item)

    return {
        "city": city,
        "pollutant": pollutant,
        "horizon_h": horizon_h,
        "total_cells": detected.get("total_cells", 0),
        "hot_cells": detected.get("hot_cells", 0),
        "n_clusters": len(items),
        "items": items,
    }
