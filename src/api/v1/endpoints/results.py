from bson import ObjectId
from fastapi import APIRouter, HTTPException, Query

from src.core.database import db
from src.core.response_wrapper import ApiResponse

router = APIRouter()


def _serialize(doc: dict) -> dict:
    doc["id"] = str(doc.pop("_id"))
    return doc


@router.get("", response_model=ApiResponse[list])
async def list_history(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    skip = (page - 1) * limit
    cursor = db.db["ocr_results"].find({}, {"data": 0}).skip(skip).limit(limit)
    docs = [_serialize(d) async for d in cursor]
    return ApiResponse.ok(data=docs, message=f"Page {page}")


@router.get("/{doc_id}", response_model=ApiResponse[dict])
async def get_history(doc_id: str):
    try:
        oid = ObjectId(doc_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid document ID.")

    doc = await db.db["ocr_results"].find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    return ApiResponse.ok(data=_serialize(doc))
