from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel

from src.core.auth import verify_service_key
from src.core.response_wrapper import ApiResponse
from src.services.surya_ocr_pipeline import (
    load_images_from_upload,
    run_surya_ocr_plain,
)

router = APIRouter()


class OcrInternalPage(BaseModel):
    paged_idx: int
    page_type: Optional[str]
    sub_page_type: Optional[str] = None
    page_confidence: Optional[float]
    raw_text: Optional[str]
    checklist: Optional[Dict[str, Any]]


class OcrInternalData(BaseModel):
    document_type: Optional[str]
    total_pages: int
    overall_confidence: Optional[float]
    pages: List[OcrInternalPage]


@router.post(
    "/internal/ocr",
    response_model=ApiResponse[OcrInternalData],
    dependencies=[Depends(verify_service_key)],
)
async def ocr_extract(
    file: UploadFile = File(...),
) -> ApiResponse:
    """
    Run Surya OCR on an uploaded PDF or image.
    Returns raw OCR text per page — no classification, no extraction.
    Nothing is saved to DB or R2.
    """
    contents = await file.read()
    images = load_images_from_upload(contents, file.content_type, file.filename)
    result = await run_surya_ocr_plain(images)

    pages_out = [
        OcrInternalPage(
            paged_idx=page.paged_idx,
            page_type=None,
            sub_page_type=None,
            page_confidence=page.page_confidence,
            raw_text=raw_text,
            checklist=None,
        )
        for page, raw_text in zip(result.pages, result.raw_text_pages)
    ]

    return ApiResponse.ok(
        data=OcrInternalData(
            document_type=None,
            total_pages=len(pages_out),
            overall_confidence=result.overall_confidence,
            pages=pages_out,
        ).model_dump()
    )
