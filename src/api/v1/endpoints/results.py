import logging
from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, HTTPException, Header, Path, Query
from pydantic import BaseModel, Field, field_validator

from src.core.auth import verify_jwt
from src.core.database import db
from src.core.response_wrapper import ApiResponse
from src.models.schemas import OCRPage
from src.services.cross_validation import run_cross_validation
from src.services.page_connections import build_page_connections
from src.utils import to_local_time

logger = logging.getLogger('shipping_bill_ocr')

router = APIRouter()


class PageTypeUpdateBody(BaseModel):
    """Arbitrary label for the page; no fixed vocabulary."""

    page_type: str = Field(..., max_length=512)

    @field_validator('page_type')
    @classmethod
    def strip_non_empty(cls, v: str) -> str:
        s = (v or '').strip()
        if not s:
            raise ValueError('page_type cannot be empty')
        return s


class Highlight(BaseModel):
    id: str
    canvasId: str
    ocrResultId: str
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


def _attach_checklists(serialized_doc: dict) -> dict:
    pages = serialized_doc.get('data') or []
    serialized_doc['checklists'] = [
        p.get('checklist') if isinstance(p, dict) else None for p in pages
    ]
    return serialized_doc


def _attach_connections(serialized_doc: dict) -> dict:
    pages_raw = serialized_doc.get('data') or []
    try:
        pages = [OCRPage(**p) for p in pages_raw if isinstance(p, dict)]
        results = build_page_connections(pages)
        serialized_doc['connections'] = [
            r.model_dump(by_alias=True) for r in results
        ] or None
    except Exception:
        logger.exception('Page connection building failed')
        serialized_doc['connections'] = None
    return serialized_doc


def _attach_cross_validation(serialized_doc: dict) -> dict:
    """
    Reconstruct OCRPage objects from the raw MongoDB dict, run the cross-
    validation engine, and attach the results under "cross_validation_results".
    Always produces the key (empty list on error) so the response shape is stable.
    """
    pages_raw = serialized_doc.get('data') or []
    try:
        pages = [OCRPage(**p) for p in pages_raw if isinstance(p, dict)]
        results = run_cross_validation(pages)
        serialized_doc['cross_validation_results'] = [r.model_dump()
                                                      for r in results]
    except Exception:
        logger.exception(
            'Cross-validation failed while building project detail')
        serialized_doc['cross_validation_results'] = []
    return serialized_doc


def _enrich_ocr_result(doc: dict, user_tz: str) -> dict:
    """Apply all enrichment helpers and localise timestamps on a single ocr_result doc."""
    doc = _enrich_confidence_fields(doc)
    _attach_checklists(doc)
    _attach_connections(doc)
    _attach_cross_validation(doc)
    dt_created = to_local_time(doc.get("created_at"), user_tz)
    dt_edited = to_local_time(doc.get("edited_at"), user_tz)
    if dt_created:
        doc["created_at"] = dt_created.isoformat()
    if dt_edited:
        doc["edited_at"] = dt_edited.isoformat()
    return doc


async def _assert_canvas_owned_by_user(canvas_id: str, user_id: str) -> None:
    try:
        oid = ObjectId(canvas_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid canvas ID format.")

    doc = await db.db["canvases"].find_one({"_id": oid, "user_id": user_id}, {"_id": 1})
    if not doc:
        raise HTTPException(status_code=404, detail="Canvas not found.")


async def _assert_ocr_result_in_canvas(ocr_result_id: str, canvas_id: str, user_id: str) -> None:
    try:
        oid = ObjectId(ocr_result_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ocr_result ID format.")

    doc = await db.db["ocr_results"].find_one(
        {"_id": oid, "canvas_id": canvas_id, "user_id": user_id},
        {"_id": 1},
    )
    if not doc:
        raise HTTPException(status_code=404, detail="PDF not found in canvas.")


# ---------------------------------------------------------------------------
# Canvas list
# ---------------------------------------------------------------------------

@router.get("", response_model=ApiResponse[list])
async def list_canvases(
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    customer_name: Optional[str] = Query(
        None, description="Filter by sub_page_type on any page of any PDF in the canvas"),
    x_timezone: Optional[str] = Header(None),
    payload: dict = Depends(verify_jwt),
):
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=401, detail="Invalid token: missing subject")

    user_tz = x_timezone or "UTC"
    skip = (page - 1) * pageSize

    canvas_query: dict = {"user_id": user_id}

    if customer_name:
        matching_ids = await db.db["ocr_results"].distinct(
            "canvas_id",
            {"user_id": user_id, "data.sub_page_type": customer_name},
        )
        canvas_query["_id"] = {"$in": [ObjectId(cid) for cid in matching_ids if cid]}

    cursor = db.db["canvases"].find(canvas_query).sort("edited_at", -1).skip(skip).limit(pageSize)

    docs = []
    async for canvas in cursor:
        canvas_id_str = str(canvas["_id"])
        serialized = _serialize(canvas)

        pdf_count = await db.db["ocr_results"].count_documents({"canvas_id": canvas_id_str})
        serialized["pdf_count"] = pdf_count

        edited_at_local = to_local_time(canvas.get("edited_at"), user_tz)
        created_at_local = to_local_time(canvas.get("created_at"), user_tz)
        if edited_at_local:
            serialized["edited_at"] = edited_at_local.isoformat()
        if created_at_local:
            serialized["created_at"] = created_at_local.isoformat()

        docs.append(serialized)

    return ApiResponse.ok(data=docs, message=f"Page {page}")


# ---------------------------------------------------------------------------
# Canvas detail — metadata + all child PDFs
# ---------------------------------------------------------------------------

@router.get("/{canvas_id}", response_model=ApiResponse[dict])
async def get_canvas_detail(
    canvas_id: str,
    x_timezone: Optional[str] = Header(None),
    payload: dict = Depends(verify_jwt),
):
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=401, detail="Invalid token: missing subject")

    await _assert_canvas_owned_by_user(canvas_id, user_id)

    try:
        canvas_oid = ObjectId(canvas_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid canvas ID format.")

    canvas = await db.db["canvases"].find_one({"_id": canvas_oid, "user_id": user_id})
    if not canvas:
        raise HTTPException(status_code=404, detail="Canvas not found.")

    user_tz = x_timezone or "UTC"

    serialized_canvas = _serialize(canvas)
    dt_created = to_local_time(canvas.get("created_at"), user_tz)
    dt_edited = to_local_time(canvas.get("edited_at"), user_tz)
    if dt_created:
        serialized_canvas["created_at"] = dt_created.isoformat()
    if dt_edited:
        serialized_canvas["edited_at"] = dt_edited.isoformat()

    pdfs = []
    cursor = db.db["ocr_results"].find(
        {"canvas_id": canvas_id, "user_id": user_id}
    ).sort("sort_order", 1)

    async for pdf_doc in cursor:
        serialized_pdf = _serialize(pdf_doc)
        serialized_pdf = _enrich_ocr_result(serialized_pdf, user_tz)
        pdfs.append(serialized_pdf)

    serialized_canvas["pdfs"] = pdfs
    return ApiResponse.ok(data=serialized_canvas)


# ---------------------------------------------------------------------------
# Delete canvas
# ---------------------------------------------------------------------------

@router.delete("/{canvas_id}", response_model=ApiResponse[dict])
async def delete_canvas(
    canvas_id: str,
    payload: dict = Depends(verify_jwt),
):
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=401, detail="Invalid token: missing subject")

    await _assert_canvas_owned_by_user(canvas_id, user_id)

    try:
        canvas_oid = ObjectId(canvas_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid canvas ID format.")

    await db.db["ocr_results"].delete_many({"canvas_id": canvas_id, "user_id": user_id})
    await db.db["highlights"].delete_many({"canvas_id": canvas_id, "user_id": user_id})
    await db.db["canvases"].delete_one({"_id": canvas_oid, "user_id": user_id})

    return ApiResponse.ok(data={}, message="Canvas deleted")


# ---------------------------------------------------------------------------
# Page type update — scoped to a specific PDF inside a canvas
# ---------------------------------------------------------------------------

@router.patch(
    '/{canvas_id}/pdfs/{ocr_result_id}/pages/{paged_idx}/page-type',
    response_model=ApiResponse[dict],
)
async def update_page_type(
    canvas_id: str,
    ocr_result_id: str,
    paged_idx: int = Path(
        ...,
        ge=1,
        description='1-based page index (same as data[].paged_idx).',
    ),
    payload: dict = Depends(verify_jwt),
    x_timezone: Optional[str] = Header(None),
    body: PageTypeUpdateBody = Body(...),
):
    """Set page_type for one page to any non-empty string."""
    user_id = payload.get('sub')
    if not user_id:
        raise HTTPException(
            status_code=401, detail='Invalid token: missing subject')

    await _assert_canvas_owned_by_user(canvas_id, user_id)
    await _assert_ocr_result_in_canvas(ocr_result_id, canvas_id, user_id)

    try:
        oid = ObjectId(ocr_result_id)
    except Exception:
        raise HTTPException(status_code=400, detail='Invalid ocr_result ID format.')

    doc = await db.db['ocr_results'].find_one({'_id': oid, 'user_id': user_id})
    if not doc:
        raise HTTPException(status_code=404, detail='PDF not found.')

    pages = doc.get('data') or []
    idx = next(
        (i for i, p in enumerate(pages) if p.get('paged_idx') == paged_idx),
        None,
    )
    if idx is None:
        raise HTTPException(status_code=404, detail='Page not found.')

    now = datetime.utcnow()
    await db.db['ocr_results'].update_one(
        {'_id': oid, 'user_id': user_id},
        {'$set': {f'data.{idx}.page_type': body.page_type, 'edited_at': now}},
    )
    await db.db['canvases'].update_one(
        {'_id': ObjectId(canvas_id)},
        {'$set': {'edited_at': now}},
    )

    updated = await db.db['ocr_results'].find_one({'_id': oid, 'user_id': user_id})
    if not updated:
        raise HTTPException(status_code=404, detail='PDF not found.')

    user_tz = x_timezone or 'UTC'
    serialized_doc = _serialize(updated)
    serialized_doc = _enrich_ocr_result(serialized_doc, user_tz)

    return ApiResponse.ok(data=serialized_doc, message='Page type updated')


# ---------------------------------------------------------------------------
# Highlights — per PDF inside a canvas
# ---------------------------------------------------------------------------

@router.get("/{canvas_id}/pdfs/{ocr_result_id}/highlights", response_model=ApiResponse[list])
async def get_highlights(
    canvas_id: str,
    ocr_result_id: str,
    payload: dict = Depends(verify_jwt),
):
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=401, detail="Invalid token: missing subject")

    await _assert_canvas_owned_by_user(canvas_id, user_id)
    await _assert_ocr_result_in_canvas(ocr_result_id, canvas_id, user_id)

    doc = await db.db["highlights"].find_one(
        {"user_id": user_id, "canvas_id": canvas_id, "ocr_result_id": ocr_result_id}
    )
    return ApiResponse.ok(data=(doc or {}).get("highlights", []))


@router.put("/{canvas_id}/pdfs/{ocr_result_id}/highlights", response_model=ApiResponse[list])
async def replace_highlights(
    canvas_id: str,
    ocr_result_id: str,
    body: HighlightSetPayload,
    payload: dict = Depends(verify_jwt),
):
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=401, detail="Invalid token: missing subject")

    await _assert_canvas_owned_by_user(canvas_id, user_id)
    await _assert_ocr_result_in_canvas(ocr_result_id, canvas_id, user_id)

    now = datetime.utcnow().isoformat()
    existing = await db.db["highlights"].find_one(
        {"user_id": user_id, "canvas_id": canvas_id, "ocr_result_id": ocr_result_id}
    )
    prev = {}
    if existing:
        prev = {h.get("id"): h for h in existing.get("highlights", []) if h.get("id")}

    normalized = []
    for h in body.highlights:
        hid = h.id or str(uuid4())
        prior = prev.get(hid) or {}
        created_at = prior.get("createdAt") or h.createdAt or now
        normalized.append(
            Highlight(
                id=hid,
                canvasId=canvas_id,
                ocrResultId=ocr_result_id,
                pageIndex=h.pageIndex,
                left=h.left,
                top=h.top,
                width=h.width,
                height=h.height,
                color=h.color,
                note=h.note,
                createdAt=created_at,
                updatedAt=now,
            ).model_dump()
        )

    await db.db["highlights"].update_one(
        {"user_id": user_id, "canvas_id": canvas_id, "ocr_result_id": ocr_result_id},
        {
            "$set": {
                "user_id": user_id,
                "canvas_id": canvas_id,
                "ocr_result_id": ocr_result_id,
                "highlights": normalized,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )

    return ApiResponse.ok(data=normalized, message="Highlights replaced")
