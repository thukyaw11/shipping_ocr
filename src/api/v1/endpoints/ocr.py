from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from src.core.auth import verify_jwt
from src.core.response_wrapper import ApiResponse
from src.models.schemas import OCRDocument, OCRPage, OCRLine
from src.core.database import db
from datetime import datetime
import io
import os
import ollama
from PIL import Image
from src.utils import pdf_to_images
from src.services.s3_service import s3_service

from src.services.ocr_service import (
    ExtractedInfo,
    build_layout_text,
    det_predictor,
    rec_predictor,
)

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

router = APIRouter()

ALLOWED_DOCUMENT_TYPES = {"MAWB", "HAWB", "TATA", "INVOICE", "UNKNOWN"}
DOC_TYPE_MODEL = os.getenv("DOC_TYPE_MODEL", "llama3:latest")


def _sanitize_ocr_text(ocr_text: str) -> str:
    if not ocr_text.strip():
        return ocr_text
    # Lightweight local cleanup so classifier sees stable tokens.
    normalized = " ".join(ocr_text.split())
    return normalized[:12000]


def _call_qwen3_classify(prompt: str) -> str:
    response = ollama.chat(
        model=DOC_TYPE_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "Classify logistics/air-cargo OCR text into exactly one label: "
                    "MAWB, HAWB, TATA, INVOICE, UNKNOWN. "
                    "Return only the label."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    )
    raw = str(response.message.content or "").upper()
    # Robust parsing: allow extra words/punctuation, pick first known label.
    for label in ("MAWB", "HAWB", "TATA", "INVOICE", "UNKNOWN"):
        if label in raw:
            return label
    return "UNKNOWN"


def _classify_document_type_from_text(ocr_text: str) -> str:
    if not ocr_text.strip():
        return "UNKNOWN"

    try:
        clean_text = _sanitize_ocr_text(ocr_text)
        prompt = f"Classify this whole document OCR text:\n\n{clean_text}"
        return _call_qwen3_classify(prompt)
    except Exception:
        return "UNKNOWN"


def _classify_page_type_from_text(page_ocr_text: str, page_num: int) -> str:
    if not page_ocr_text.strip():
        return "UNKNOWN"
    try:
        clean_text = _sanitize_ocr_text(page_ocr_text)
        prompt = f"Classify this page OCR text (page {page_num}):\n\n{clean_text}"
        return _call_qwen3_classify(prompt)
    except Exception:
        return "UNKNOWN"


@router.post("/surya", response_model=ApiResponse[OCRDocument])
async def ocr_file_surya(
    file: UploadFile = File(...),
    payload: dict = Depends(verify_jwt),
):
    contents = await file.read()

    # uploading to R2
    import io
    file_buffer = io.BytesIO(contents)

    unique_filename = f"{datetime.utcnow().timestamp()}_{file.filename}"

    file_url = await run_in_threadpool(
        s3_service.upload_file,
        file_buffer,
        unique_filename,
        file.content_type
    )
    # uploading to R2 end

    # OCR process start
    is_pdf = (file.content_type == "application/pdf"
              or (file.filename or "").lower().endswith(".pdf"))

    if is_pdf:
        images = await run_in_threadpool(pdf_to_images, contents)
    elif (file.content_type or "").startswith("image/"):
        images = [Image.open(io.BytesIO(contents)).convert("RGB")]
    else:
        raise HTTPException(
            status_code=400, detail="Upload must be an image or PDF.")

    predictions = await run_in_threadpool(
        lambda: rec_predictor(images, det_predictor=det_predictor)
    )

    # 3. Data Formatting using Pydantic Models
    pages_list = []
    raw_text_pages = []
    confidence_sum = 0.0
    confidence_count = 0
    for page_num, pred in enumerate(predictions, start=1):
        lines = []
        page_text = build_layout_text(pred.text_lines)
        raw_text_pages.append(page_text)
        page_confidence_sum = 0.0
        page_confidence_count = 0
        for line in pred.text_lines:
            if not line.text.strip():
                continue

            line_confidence = float(line.confidence)
            confidence_sum += line_confidence
            confidence_count += 1
            page_confidence_sum += line_confidence
            page_confidence_count += 1
            lines.append(OCRLine(
                text=line.text,
                confidence=line_confidence,
                bbox=line.bbox,
                polygon=[list(pt) for pt in line.polygon]
            ))

        page_confidence = (
            round(page_confidence_sum / page_confidence_count, 6)
            if page_confidence_count > 0
            else None
        )
        page_type = await run_in_threadpool(
            _classify_page_type_from_text,
            page_text,
            page_num,
        )
        pages_list.append(OCRPage(
            paged_idx=page_num,
            page_confidence=page_confidence,
            page_type=page_type,
            image_bbox=pred.image_bbox,
            text_lines=lines
        ))

    overall_confidence = (
        round(confidence_sum / confidence_count, 6) if confidence_count > 0 else None
    )
    doc_raw_text = "\n\n".join(raw_text_pages)
    document_type = await run_in_threadpool(_classify_document_type_from_text, doc_raw_text)

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token: missing subject")

    ocr_doc = OCRDocument(
        user_id=user_id,
        filename=file.filename or "unknown",
        total_pages=len(pages_list),
        overall_confidence=overall_confidence,
        document_type=document_type,
        data=pages_list,
        status="success",
        created_at=datetime.utcnow(),
        url=file_url,
        type=file.content_type,
    )

    insert_result = await db.db["ocr_results"].insert_one(ocr_doc.model_dump())

    return ApiResponse.ok(data=ocr_doc)
