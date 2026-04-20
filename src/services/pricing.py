"""Pricing helpers — price per page is stored in the DB settings collection
and falls back to the PRICE_PER_PAGE env var / Config default."""

from src.core.config import Config
from src.core.database import db

_SETTINGS_KEY = "pricing"


async def get_price_per_page() -> float:
    """Return the active price per page from DB settings, or Config default."""
    doc = await db.db["settings"].find_one({"key": _SETTINGS_KEY})
    if doc and isinstance(doc.get("price_per_page"), (int, float)):
        return float(doc["price_per_page"])
    return Config.PRICE_PER_PAGE


async def set_price_per_page(price: float) -> float:
    """Persist a new price per page and return it."""
    await db.db["settings"].update_one(
        {"key": _SETTINGS_KEY},
        {"$set": {"key": _SETTINGS_KEY, "price_per_page": price}},
        upsert=True,
    )
    return price


def compute_cost(pages: int, price_per_page: float) -> float:
    """Return total cost rounded to 6 decimal places."""
    return round(pages * price_per_page, 6)
