import asyncio
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from src.core.auth import verify_service_key
from src.core.response_wrapper import ApiResponse
from src.services.ai.factory import get_classification_text_provider
from src.services.ai.gemini_provider import build_default_gemini_provider
from src.services.checklist_extraction import extract_checklist_sync
from src.services.cross_validation import run_cross_validation
from src.services.document_classification import DocumentTypeClassifier
from src.services.surya_ocr_pipeline import (
    load_images_from_upload,
    run_surya_ocr_with_classification,
    run_surya_ocr_with_forced_type,
)

router = APIRouter()

_document_classifier = DocumentTypeClassifier(get_classification_text_provider())

PageTypeEnum = Literal["MAWB", "HAWB", "IATA", "INVOICE", "CARGO_MANIFEST", "IMPORT_ENTRY"]


# ---------------------------------------------------------------------------
# Shared response models
# ---------------------------------------------------------------------------

class PageResult(BaseModel):
    paged_idx: int
    page_type: Optional[str]
    sub_page_type: Optional[str]
    page_confidence: Optional[float]
    raw_text: Optional[str]
    checklist: Optional[Dict[str, Any]]


class OcrExtractResponse(BaseModel):
    document_type: Optional[str]
    total_pages: int
    overall_confidence: Optional[float]
    pages: List[PageResult]
    cross_validation_results: List[Dict[str, Any]]


# ---------------------------------------------------------------------------
# POST /internal/extract-checklist
# Upload + forced page_type → Surya OCR + Gemini checklist (no classification)
# ---------------------------------------------------------------------------

class ChecklistOnlyResponse(BaseModel):
    page_type: str
    total_pages: int
    overall_confidence: Optional[float]
    pages: List[PageResult]


@router.post(
    "/internal/extract-checklist",
    response_model=ApiResponse[ChecklistOnlyResponse],
    dependencies=[Depends(verify_service_key)],
)
async def extract_checklist_from_file(
    file: UploadFile = File(...),
    page_type: PageTypeEnum = Form(...),
    sub_page_type: Optional[str] = Form(None),
) -> ApiResponse:
    """
    Upload a PDF/image, run Surya OCR, extract structured checklist fields with
    Gemini using a caller-supplied page_type. No classification step.
    Nothing is saved to DB or R2.
    """
    gemini = build_default_gemini_provider()
    if gemini is None:
        raise HTTPException(status_code=503, detail="Gemini is not configured.")

    contents = await file.read()
    images = load_images_from_upload(contents, file.content_type, file.filename)
    pipeline_result = await run_surya_ocr_with_forced_type(images, page_type)

    checklist_tasks = [
        run_in_threadpool(
            extract_checklist_sync,
            gemini,
            page_type,
            raw_text,
            14000,
            sub_page_type,
        )
        for raw_text in pipeline_result.raw_text_pages
    ]
    checklists = await asyncio.gather(*checklist_tasks)

    pages_out = [
        PageResult(
            paged_idx=page.paged_idx,
            page_type=page_type,
            sub_page_type=sub_page_type,
            page_confidence=page.page_confidence,
            raw_text=raw_text,
            checklist=checklist,
        )
        for page, raw_text, checklist in zip(
            pipeline_result.pages, pipeline_result.raw_text_pages, checklists
        )
    ]

    return ApiResponse.ok(
        data=ChecklistOnlyResponse(
            page_type=page_type,
            total_pages=len(pages_out),
            overall_confidence=pipeline_result.overall_confidence,
            pages=pages_out,
        ).model_dump()
    )


# ---------------------------------------------------------------------------
# POST /internal/ocr
# Full pipeline: Surya OCR → Gemini classification → Gemini checklists → cross-validation
# Nothing is saved to DB or R2.
# ---------------------------------------------------------------------------

@router.post(
    "/internal/ocr",
    response_model=ApiResponse[OcrExtractResponse],
    dependencies=[Depends(verify_service_key)],
)
async def ocr_extract(
    file: UploadFile = File(...),
) -> ApiResponse:
    """
    Full OCR pipeline: Surya OCR → Gemini page classification → Gemini checklist
    extraction → cross-validation. Returns structured results.
    Nothing is saved to DB or R2.
    """
    gemini = build_default_gemini_provider()
    if gemini is None:
        raise HTTPException(status_code=503, detail="Gemini is not configured.")

    contents = await file.read()
    images = load_images_from_upload(contents, file.content_type, file.filename)
    pipeline_result = await run_surya_ocr_with_classification(_document_classifier, images)

    checklist_tasks = [
        run_in_threadpool(
            extract_checklist_sync,
            gemini,
            page.page_type or '',
            raw_text,
            14000,
            page.sub_page_type,
        )
        for page, raw_text in zip(pipeline_result.pages, pipeline_result.raw_text_pages)
    ]
    checklists = await asyncio.gather(*checklist_tasks)

    for page, checklist in zip(pipeline_result.pages, checklists):
        if checklist is not None:
            page.checklist = checklist

    cv_results = run_cross_validation(pipeline_result.pages)

    pages_out = [
        PageResult(
            paged_idx=page.paged_idx,
            page_type=page.page_type,
            sub_page_type=page.sub_page_type,
            page_confidence=page.page_confidence,
            raw_text=raw_text,
            checklist=page.checklist,
        )
        for page, raw_text in zip(pipeline_result.pages, pipeline_result.raw_text_pages)
    ]

    return ApiResponse.ok(
        data=OcrExtractResponse(
            document_type=pipeline_result.document_type,
            total_pages=len(pages_out),
            overall_confidence=pipeline_result.overall_confidence,
            pages=pages_out,
            cross_validation_results=[r.model_dump() for r in cv_results],
        ).model_dump()
    )
