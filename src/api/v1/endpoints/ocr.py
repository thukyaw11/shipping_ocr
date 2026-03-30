from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from src.core.auth import verify_jwt
from src.core.config import Config
from src.core.response_wrapper import ApiResponse
from src.models.schemas import OCRDocument, OCRPage, OCRLine
from src.core.database import db
from datetime import datetime
import asyncio
import io
import json
import logging
import os
import ollama
import time
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

ALLOWED_DOCUMENT_TYPES = {
    "MAWB",
    "HAWB",
    "IATA",
    "INVOICE",
    "CARGO_MANIFEST",
    "UNKNOWN",
}
DOC_TYPE_MODEL = os.getenv("DOC_TYPE_MODEL", "qwen3.5:397b-cloud")
QWEN3_API_URL = Config.QWEN3_API_URL
QWEN3_API_TOKEN = Config.QWEN3_API_TOKEN
DEBUG_CLASSIFICATION = os.getenv(
    "DEBUG_CLASSIFICATION", "false").lower() == "true"

try:
    from google import genai

    _GEMINI_AVAILABLE = True
except ImportError:
    _GEMINI_AVAILABLE = False

GEMINI_API_KEY = Config.GEMINI_API_KEY
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

logger = logging.getLogger("shipping_bill_ocr")


def _sanitize_page_with_log(page_num: int, page_text: str) -> str:
    if DEBUG_CLASSIFICATION:
        print(f"[classify] sanitize page {page_num} start")
    cleaned = _sanitize_ocr_text(page_text)
    if DEBUG_CLASSIFICATION:
        print(
            f"[classify] sanitize page {page_num} done ({len(cleaned) if cleaned else 0} chars)"
        )
    return cleaned


def _sanitize_ocr_text(ocr_text: str) -> str:
    if not ocr_text.strip():
        return ocr_text
    # Local fast cleanup only (no external sanitize model call).
    normalized = " ".join(ocr_text.split())
    return normalized[:12000]


def _call_qwen3_classify(prompt: str) -> str:
    system_prompt = (
        "Classify logistics/air-cargo OCR text into exactly one label: "
        "MAWB, HAWB, IATA, INVOICE, CARGO_MANIFEST, UNKNOWN. "
        "Use these rules: "
        "CARGO_MANIFEST usually has a table/list of multiple shipments or items, often with shipper and consignee columns/entries; "
        "MAWB/HAWB are air waybill documents with AWB numbers and airway bill fields; "
        "INVOICE is billing-focused with invoice totals/tax/amount due; "
        "IATA documents usually follow IATA-standard air cargo/air waybill formats and terminology. "
        "Return only one label with no explanation."
    )

    # 1) Gemini (text-only)
    if GEMINI_API_KEY and _GEMINI_AVAILABLE:
        started = time.monotonic()
        print(
            f"[classify] Gemini start model={GEMINI_MODEL} prompt_chars={len(prompt or '')}"
        )
        try:
            client = genai.Client(api_key=GEMINI_API_KEY)
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=f"{system_prompt}\n\n{prompt}",
            )
            raw = str(getattr(resp, "text", "") or "").upper()
            label_found = "UNKNOWN"
            for label in ("MAWB", "HAWB", "IATA", "INVOICE", "CARGO_MANIFEST", "UNKNOWN"):
                if label in raw:
                    label_found = label
                    break
            print(
                f"[classify] Gemini done label={label_found} took_ms={(time.monotonic() - started) * 1000.0:.1f} raw_snip={raw[:60].replace(chr(10), ' ')}"
            )
            return label_found
        except Exception as e:
            print(f"[classify] Gemini failed: {str(e)}")
            # fallthrough to Ollama
            pass

    # 2) Fallback to Ollama classifier
    started = time.monotonic()
    if DEBUG_CLASSIFICATION:
        print(
            f"[classify] Ollama start model={DOC_TYPE_MODEL} prompt_chars={len(prompt or '')}"
        )
    response = ollama.chat(
        model=DOC_TYPE_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
    )
    raw = str(response.message.content or "").upper()
    label_found = "UNKNOWN"
    for label in ("MAWB", "HAWB", "IATA", "INVOICE", "CARGO_MANIFEST", "UNKNOWN"):
        if label in raw:
            label_found = label
            break
    if DEBUG_CLASSIFICATION:
        print(
            f"[classify] Ollama done label={label_found} took_ms={(time.monotonic() - started) * 1000.0:.1f} raw_snip={raw[:60].replace(chr(10), ' ')}"
        )
    return label_found


def _classify_document_type_from_clean_text(clean_text: str) -> str:
    if not clean_text.strip():
        return "UNKNOWN"

    try:
        prompt = f"Classify this whole document OCR text:\n\n{clean_text}"
        return _call_qwen3_classify(prompt)
    except Exception:
        return "UNKNOWN"


def _classify_page_type_from_clean_text(clean_text: str, page_num: int) -> str:
    if not clean_text.strip():
        return "UNKNOWN"
    try:
        prompt = f"Classify this page OCR text (page {page_num}):\n\n{clean_text}"
        return _call_qwen3_classify(prompt)
    except Exception:
        return "UNKNOWN"


@router.post("/surya", response_model=ApiResponse[OCRDocument])
async def ocr_file_surya(
    file: UploadFile = File(...),
    payload: dict = Depends(verify_jwt),
):
    started_at = time.monotonic()
    print(f"[ocr] start file={file.filename} content_type={file.content_type}")
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
    print("[ocr] uploaded file_url ready")

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
    print(
        f"[ocr] surya predictions ready pages={len(predictions) if predictions else 0}"
    )

    # 3. Data Formatting using Pydantic Models
    pages_list = []
    raw_text_pages = []
    confidence_sum = 0.0
    confidence_count = 0
    print("[ocr] building page texts/confidence...")
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
        pages_list.append(OCRPage(
            paged_idx=page_num,
            page_confidence=page_confidence,
            page_type="UNKNOWN",
            image_bbox=pred.image_bbox,
            text_lines=lines
        ))

    print("[ocr] built page objects. computing confidence/type...")

    overall_confidence = (
        round(confidence_sum / confidence_count,
              6) if confidence_count > 0 else None
    )
    # Sanitize each page once, then reuse for both page/doc classification.
    print(
        f"[ocr] sanitizing {len(raw_text_pages)} page(s) for type classification..."
    )
    sanitized_page_texts = await asyncio.gather(
        *[
            run_in_threadpool(_sanitize_page_with_log, idx + 1, t)
            for idx, t in enumerate(raw_text_pages)
        ]
    )

    page_type_tasks = [
        run_in_threadpool(_classify_page_type_from_clean_text, txt, idx + 1)
        for idx, txt in enumerate(sanitized_page_texts)
    ]
    print(
        f"[ocr] classifying {len(page_type_tasks)} page(s) for page_type..."
    )
    page_types = await asyncio.gather(*page_type_tasks)
    for idx, page_type in enumerate(page_types):
        pages_list[idx].page_type = page_type

    print("[ocr] page types classified")

    doc_clean_text = "\n\n".join(sanitized_page_texts)
    print("[ocr] classifying document_type...")
    document_type = await run_in_threadpool(
        _classify_document_type_from_clean_text,
        doc_clean_text,
    )
    print(f"[ocr] document type classified={document_type}")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=401, detail="Invalid token: missing subject")

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

    print(f"[ocr] insert done in {time.monotonic() - started_at:.2f}s")
    return ApiResponse.ok(data=ocr_doc)
