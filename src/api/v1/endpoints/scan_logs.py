from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, Header, HTTPException, Query

from src.core.auth import verify_jwt
from src.core.database import db
from src.core.response_wrapper import ApiResponse
from src.utils import to_local_time

router = APIRouter()


def _serialize(doc: dict) -> dict:
    doc["id"] = str(doc.pop("_id"))
    return doc


@router.get("", response_model=ApiResponse[dict])
async def list_scan_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None, description="Filter by status: success | failed"),
    x_timezone: Optional[str] = Header(None),
    payload: dict = Depends(verify_jwt),
):
    """List the authenticated user's scan history, newest first."""
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token: missing subject")

    user_tz = x_timezone or "UTC"
    skip = (page - 1) * page_size

    query: dict = {"user_id": user_id}
    if status in ("success", "failed"):
        query["status"] = status

    total = await db.db["scan_logs"].count_documents(query)
    cursor = db.db["scan_logs"].find(query).sort("created_at", -1).skip(skip).limit(page_size)

    docs = []
    async for doc in cursor:
        serialized = _serialize(doc)
        local_dt = to_local_time(doc.get("created_at"), user_tz)
        if local_dt:
            serialized["created_at"] = local_dt.isoformat()
        docs.append(serialized)

    return ApiResponse.ok(
        data={"total": total, "page": page, "page_size": page_size, "items": docs},
        message=f"Page {page}",
    )


@router.get("/{log_id}", response_model=ApiResponse[dict])
async def get_scan_log(
    log_id: str,
    x_timezone: Optional[str] = Header(None),
    payload: dict = Depends(verify_jwt),
):
    """Get a single scan log entry by ID."""
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token: missing subject")

    try:
        oid = ObjectId(log_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid log ID format.")

    doc = await db.db["scan_logs"].find_one({"_id": oid, "user_id": user_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Scan log not found.")

    user_tz = x_timezone or "UTC"
    serialized = _serialize(doc)
    local_dt = to_local_time(doc.get("created_at"), user_tz)
    if local_dt:
        serialized["created_at"] = local_dt.isoformat()

    return ApiResponse.ok(data=serialized)
