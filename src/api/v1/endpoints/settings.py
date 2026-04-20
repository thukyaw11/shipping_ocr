from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from src.core.auth import verify_jwt
from src.core.response_wrapper import ApiResponse
from src.services.pricing import get_price_per_page, set_price_per_page

router = APIRouter()


class PricingUpdateBody(BaseModel):
    price_per_page: float = Field(..., gt=0, description="Price in USD charged per scanned page")


@router.get("/pricing", response_model=ApiResponse[dict])
async def get_pricing(payload: dict = Depends(verify_jwt)):
    """Return the current price-per-page setting."""
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token: missing subject")

    price = await get_price_per_page()
    return ApiResponse.ok(data={"price_per_page": price})


@router.patch("/pricing", response_model=ApiResponse[dict])
async def update_pricing(
    body: PricingUpdateBody = Body(...),
    payload: dict = Depends(verify_jwt),
):
    """Update the price charged per scanned page."""
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token: missing subject")

    price = await set_price_per_page(body.price_per_page)
    return ApiResponse.ok(data={"price_per_page": price}, message="Pricing updated")
