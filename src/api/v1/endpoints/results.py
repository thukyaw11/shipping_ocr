from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Header, Query
from pydantic import BaseModel, Field

from src.core.auth import verify_jwt
from src.core.database import db
from src.core.response_wrapper import ApiResponse
from src.utils import to_local_time

router = APIRouter()


class Highlight(BaseModel):
    id: str
    projectId: str
    pageIndex: int = Field(..., ge=0)
    left: float = Field(..., ge=0, le=100)
    top: float = Field(..., ge=0, le=100)
    width: float = Field(..., ge=0, le=100)
    height: float = Field(..., ge=0, le=100)
    color: Optional[str] = None
    note: Optional[str] = None
    createdAt: str
    updatedAt: str


class HighlightUpsert(BaseModel):
    id: Optional[str] = None
    pageIndex: int = Field(..., ge=0)
    left: float = Field(..., ge=0, le=100)
    top: float = Field(..., ge=0, le=100)
    width: float = Field(..., ge=0, le=100)
    height: float = Field(..., ge=0, le=100)
    color: Optional[str] = None
    note: Optional[str] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None


class HighlightSetPayload(BaseModel):
    highlights: List[HighlightUpsert]


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


async def _assert_project_owned_by_user(project_id: str, user_id: str) -> None:
    try:
        oid = ObjectId(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project ID format.")

    doc = await db.db["ocr_results"].find_one({"_id": oid, "user_id": user_id}, {"_id": 1})
    if not doc:
        raise HTTPException(status_code=404, detail="Project not found.")


@router.get("/{project_id}/highlights", response_model=ApiResponse[list])
async def get_project_highlights(
    project_id: str,
    payload: dict = Depends(verify_jwt),
):
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token: missing subject")

    await _assert_project_owned_by_user(project_id, user_id)

    doc = await db.db["highlights"].find_one({"user_id": user_id, "project_id": project_id})
    return ApiResponse.ok(data=(doc or {}).get("highlights", []))


@router.put("/{project_id}/highlights", response_model=ApiResponse[list])
async def replace_project_highlights(
    project_id: str,
    body: HighlightSetPayload,
    payload: dict = Depends(verify_jwt),
):
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token: missing subject")

    await _assert_project_owned_by_user(project_id, user_id)

    now = datetime.utcnow().isoformat()
    existing = await db.db["highlights"].find_one({"user_id": user_id, "project_id": project_id})
    prev = {}
    if existing:
        prev = {h.get("id"): h for h in existing.get("highlights", []) if h.get("id")}

    normalized = []
    for h in body.highlights:
        hid = h.id or str(uuid4())
        prior = prev.get(hid) or {}
        created_at = prior.get("createdAt") or h.createdAt or now
        updated_at = now
        normalized.append(
            Highlight(
                id=hid,
                projectId=project_id,
                pageIndex=h.pageIndex,
                left=h.left,
                top=h.top,
                width=h.width,
                height=h.height,
                color=h.color,
                note=h.note,
                createdAt=created_at,
                updatedAt=updated_at,
            ).model_dump()
        )

    await db.db["highlights"].update_one(
        {"user_id": user_id, "project_id": project_id},
        {
            "$set": {
                "user_id": user_id,
                "project_id": project_id,
                "highlights": normalized,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )

    return ApiResponse.ok(data=normalized, message="Highlights replaced")


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
