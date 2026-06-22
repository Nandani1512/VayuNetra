from __future__ import annotations

from fastapi import APIRouter, HTTPException

from vayunetra.api.schemas import EnforceResponse
from vayunetra.enforcement.service import enforce as enforce_service

router = APIRouter(prefix="/enforce", tags=["enforce"])


@router.get("", response_model=EnforceResponse)
async def enforce(
    city: str,
    pollutant: str = "pm25",
    horizon: int = 24,
    top_k: int = 5,
    with_attribution: bool = True,
    with_brief: bool = True,
):
    try:
        out = await enforce_service(
            city=city,
            pollutant=pollutant,
            horizon_h=horizon,
            top_k=top_k,
            with_attribution=with_attribution,
            with_brief=with_brief,
            user_id="ui-demo",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"enforce_failed: {e}") from e
    return EnforceResponse(**out)
