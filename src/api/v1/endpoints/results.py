from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Header, Query

from src.core.auth import verify_jwt
from src.core.database import db
from src.core.response_wrapper import ApiResponse
from src.utils import to_local_time

router = APIRouter()


def _serialize(doc: dict) -> dict:
    doc["id"] = str(doc.pop("_id"))
    return doc


def _enrich_confidence_fields(doc: dict) -> dict:
    pages = doc.get("data") or []
    total_sum = 0.0
    total_count = 0

    for page in pages:
        lines = page.get("text_lines") or []
        page_sum = 0.0
        page_count = 0
        for line in lines:
            conf = line.get("confidence")
            if isinstance(conf, (int, float)):
                page_sum += float(conf)
                page_count += 1
                total_sum += float(conf)
                total_count += 1

        if page.get("page_confidence") is None and page_count > 0:
            page["page_confidence"] = round(page_sum / page_count, 6)

    if doc.get("overall_confidence") is None and total_count > 0:
        doc["overall_confidence"] = round(total_sum / total_count, 6)

    return doc


@router.get("", response_model=ApiResponse[list])
async def list_history(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    x_timezone: Optional[str] = Header(None),
    payload: dict = Depends(verify_jwt),
):
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token: missing subject")

    user_tz = x_timezone or "UTC"
    skip = (page - 1) * limit

    cursor = db.db["ocr_results"].find(
        {"user_id": user_id},
        {"data": 0},
    ).sort("edited_at", -1).skip(skip).limit(limit)

    docs = []
    async for d in cursor:
        serialized_doc = _serialize(d)

        edited_at_local = to_local_time(d.get("edited_at"), user_tz)
        created_at_local = to_local_time(d.get("created_at"), user_tz)

        if edited_at_local:
            serialized_doc["edited_at"] = edited_at_local.isoformat()
        if created_at_local:
            serialized_doc["created_at"] = created_at_local.isoformat()

        docs.append(serialized_doc)

    return ApiResponse.ok(data=docs, message=f"Page {page}")


@router.get("/{doc_id}", response_model=ApiResponse[dict])
async def get_history_detail(
    doc_id: str,
    x_timezone: Optional[str] = Header(None),
    payload: dict = Depends(verify_jwt),
):
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token: missing subject")

    try:
        oid = ObjectId(doc_id)
    except Exception:
        raise HTTPException(
            status_code=400, detail="Invalid document ID format.")

    doc = await db.db["ocr_results"].find_one({"_id": oid, "user_id": user_id})

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    serialized_doc = _serialize(doc)
    serialized_doc = _enrich_confidence_fields(serialized_doc)

    user_tz = x_timezone or "UTC"

    dt_created = to_local_time(doc.get("created_at"), user_tz)
    dt_edited = to_local_time(doc.get("edited_at"), user_tz)

    if dt_created:
        serialized_doc["created_at"] = dt_created.isoformat()
    if dt_edited:
        serialized_doc["edited_at"] = dt_edited.isoformat()

    return ApiResponse.ok(data=serialized_doc)
