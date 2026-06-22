from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from vayunetra.api.schemas import AttributionResponse
from vayunetra.models.attribution.overlay import attribute_cell

router = APIRouter(prefix="/attribution", tags=["attribution"])


@router.get("", response_model=AttributionResponse)
async def attribution(
    city: str,
    cell_id: str,
    pollutant: str = "pm25",
    hours_back: int = 48,
):
    try:
        out = await attribute_cell(
            city=city,
            cell_id=cell_id,
            ts=datetime.now(timezone.utc),
            pollutant=pollutant,
            hours_back=hours_back,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"attribution_failed: {e}") from e
    return AttributionResponse(**out)
