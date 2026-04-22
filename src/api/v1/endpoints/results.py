import logging
from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, HTTPException, Header, Path, Query
from pydantic import BaseModel, Field, field_validator

from src.core.auth import verify_jwt
from src.core.response_wrapper import ApiResponse
from src.repositories.canvas_repository import canvas_repo
from src.repositories.highlight_repository import highlight_repo
from src.repositories.ocr_result_repository import ocr_result_repo
from src.services.ocr_result_enricher import enrich_ocr_result
from src.utils import to_local_time

logger = logging.getLogger('shipping_bill_ocr')

router = APIRouter()


class CanvasNameUpdateBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=512)

    @field_validator('name')
    @classmethod
    def strip_non_empty(cls, v: str) -> str:
        s = (v or '').strip()
        if not s:
            raise ValueError('name cannot be empty')
        return s


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


class SubPageTypeUpdateBody(BaseModel):
    """Arbitrary sub-label for the page; no fixed vocabulary."""

    sub_page_type: str = Field(..., max_length=512)

    @field_validator('sub_page_type')
    @classmethod
    def strip_non_empty(cls, v: str) -> str:
        s = (v or '').strip()
        if not s:
            raise ValueError('sub_page_type cannot be empty')
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


# ---------------------------------------------------------------------------
# HTTP-layer ownership guards (raise HTTPException — belong here, not in repos)
# ---------------------------------------------------------------------------

async def _assert_canvas_owned_by_user(canvas_id: str, user_id: str) -> None:
    try:
        ObjectId(canvas_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid canvas ID format.")
    doc = await canvas_repo.get_by_id(canvas_id, user_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Canvas not found.")


async def _assert_ocr_result_in_canvas(
    ocr_result_id: str, canvas_id: str, user_id: str
) -> None:
    try:
        ObjectId(ocr_result_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ocr_result ID format.")
    doc = await ocr_result_repo.find_by_id_in_canvas(ocr_result_id, canvas_id, user_id)
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
        raise HTTPException(status_code=401, detail="Invalid token: missing subject")

    user_tz = x_timezone or "UTC"
    skip = (page - 1) * pageSize

    filter_ids = None
    if customer_name:
        matching = await ocr_result_repo.distinct_canvas_ids_by_sub_page_type(
            user_id, customer_name
        )
        filter_ids = [ObjectId(cid) for cid in matching if cid]

    canvases = await canvas_repo.list_paginated(user_id, skip, pageSize, filter_ids)

    docs = []
    for canvas in canvases:
        canvas_id_str = str(canvas["_id"])
        serialized = _serialize(canvas)
        serialized["pdf_count"] = await ocr_result_repo.count_in_canvas(canvas_id_str)
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
        raise HTTPException(status_code=401, detail="Invalid token: missing subject")

    await _assert_canvas_owned_by_user(canvas_id, user_id)

    canvas = await canvas_repo.get_by_id(canvas_id, user_id)
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
    for pdf_doc in await ocr_result_repo.find_in_canvas(canvas_id, user_id):
        serialized_pdf = _serialize(pdf_doc)
        serialized_pdf = enrich_ocr_result(serialized_pdf, user_tz)
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
        raise HTTPException(status_code=401, detail="Invalid token: missing subject")

    await _assert_canvas_owned_by_user(canvas_id, user_id)
    await canvas_repo.soft_delete(canvas_id, user_id)
    return ApiResponse.ok(data={}, message="Canvas deleted")


# ---------------------------------------------------------------------------
# Delete one PDF from canvas
# ---------------------------------------------------------------------------

@router.delete("/{canvas_id}/pdfs/{ocr_result_id}", response_model=ApiResponse[dict])
async def delete_pdf_from_canvas(
    canvas_id: str,
    ocr_result_id: str,
    payload: dict = Depends(verify_jwt),
):
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token: missing subject")

    await _assert_canvas_owned_by_user(canvas_id, user_id)
    await _assert_ocr_result_in_canvas(ocr_result_id, canvas_id, user_id)
    await ocr_result_repo.soft_delete(ocr_result_id, user_id)
    await canvas_repo.touch(canvas_id)
    return ApiResponse.ok(data={}, message="PDF removed from canvas")


# ---------------------------------------------------------------------------
# Rename canvas
# ---------------------------------------------------------------------------

@router.patch("/{canvas_id}/name", response_model=ApiResponse[dict])
async def rename_canvas(
    canvas_id: str,
    body: CanvasNameUpdateBody = Body(...),
    payload: dict = Depends(verify_jwt),
):
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token: missing subject")

    await _assert_canvas_owned_by_user(canvas_id, user_id)
    updated = await canvas_repo.rename(canvas_id, user_id, body.name)
    if not updated:
        raise HTTPException(status_code=404, detail="Canvas not found.")

    return ApiResponse.ok(data=_serialize(updated), message="Canvas renamed")


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
    paged_idx: int = Path(..., ge=1, description='1-based page index (same as data[].paged_idx).'),
    payload: dict = Depends(verify_jwt),
    x_timezone: Optional[str] = Header(None),
    body: PageTypeUpdateBody = Body(...),
):
    """Set page_type for one page to any non-empty string."""
    user_id = payload.get('sub')
    if not user_id:
        raise HTTPException(status_code=401, detail='Invalid token: missing subject')

    await _assert_canvas_owned_by_user(canvas_id, user_id)
    await _assert_ocr_result_in_canvas(ocr_result_id, canvas_id, user_id)

    doc = await ocr_result_repo.get_by_id(ocr_result_id, user_id)
    if not doc:
        raise HTTPException(status_code=404, detail='PDF not found.')

    pages = doc.get('data') or []
    idx = next((i for i, p in enumerate(pages) if p.get('paged_idx') == paged_idx), None)
    if idx is None:
        raise HTTPException(status_code=404, detail='Page not found.')

    updated = await ocr_result_repo.update_page_field(ocr_result_id, user_id, idx, 'page_type', body.page_type)
    await canvas_repo.touch(canvas_id)

    if not updated:
        raise HTTPException(status_code=404, detail='PDF not found.')

    user_tz = x_timezone or 'UTC'
    return ApiResponse.ok(data=enrich_ocr_result(_serialize(updated), user_tz), message='Page type updated')


# ---------------------------------------------------------------------------
# Sub-page type update — scoped to a specific PDF inside a canvas
# ---------------------------------------------------------------------------

@router.patch(
    '/{canvas_id}/pdfs/{ocr_result_id}/pages/{paged_idx}/sub-page-type',
    response_model=ApiResponse[dict],
)
async def update_sub_page_type(
    canvas_id: str,
    ocr_result_id: str,
    paged_idx: int = Path(..., ge=1, description='1-based page index (same as data[].paged_idx).'),
    payload: dict = Depends(verify_jwt),
    x_timezone: Optional[str] = Header(None),
    body: SubPageTypeUpdateBody = Body(...),
):
    """Set sub_page_type for one page to any non-empty string."""
    user_id = payload.get('sub')
    if not user_id:
        raise HTTPException(status_code=401, detail='Invalid token: missing subject')

    await _assert_canvas_owned_by_user(canvas_id, user_id)
    await _assert_ocr_result_in_canvas(ocr_result_id, canvas_id, user_id)

    doc = await ocr_result_repo.get_by_id(ocr_result_id, user_id)
    if not doc:
        raise HTTPException(status_code=404, detail='PDF not found.')

    pages = doc.get('data') or []
    idx = next((i for i, p in enumerate(pages) if p.get('paged_idx') == paged_idx), None)
    if idx is None:
        raise HTTPException(status_code=404, detail='Page not found.')

    updated = await ocr_result_repo.update_page_field(ocr_result_id, user_id, idx, 'sub_page_type', body.sub_page_type)
    await canvas_repo.touch(canvas_id)

    if not updated:
        raise HTTPException(status_code=404, detail='PDF not found.')

    user_tz = x_timezone or 'UTC'
    return ApiResponse.ok(data=enrich_ocr_result(_serialize(updated), user_tz), message='Sub-page type updated')


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
        raise HTTPException(status_code=401, detail="Invalid token: missing subject")

    await _assert_canvas_owned_by_user(canvas_id, user_id)
    await _assert_ocr_result_in_canvas(ocr_result_id, canvas_id, user_id)

    doc = await highlight_repo.get(user_id, canvas_id, ocr_result_id)
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
        raise HTTPException(status_code=401, detail="Invalid token: missing subject")

    await _assert_canvas_owned_by_user(canvas_id, user_id)
    await _assert_ocr_result_in_canvas(ocr_result_id, canvas_id, user_id)

    now = datetime.utcnow().isoformat()
    existing = await highlight_repo.get(user_id, canvas_id, ocr_result_id)
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

    await highlight_repo.upsert(user_id, canvas_id, ocr_result_id, normalized, now)
    return ApiResponse.ok(data=normalized, message="Highlights replaced")
