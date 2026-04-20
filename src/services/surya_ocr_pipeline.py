import asyncio
import io
import os
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from PIL import Image

from src.models.schemas import OCRLine, OCRPage
from src.services.document_classification import (
    DocumentTypeClassifier,
    sanitize_page_with_log,
)
from src.services.ocr_service import (
    build_layout_text,
    det_predictor,
    rec_predictor,
)
from src.utils import pdf_to_images

_RECOGNITION_BATCH_SIZE: Optional[int] = (
    int(os.environ["RECOGNITION_BATCH_SIZE"])
    if os.environ.get("RECOGNITION_BATCH_SIZE")
    else None
)
_DETECTOR_BATCH_SIZE: Optional[int] = (
    int(os.environ["DETECTOR_BATCH_SIZE"])
    if os.environ.get("DETECTOR_BATCH_SIZE")
    else None
)
_MAX_IMAGE_DIM = 2000


@dataclass
class SuryaOcrPipelineResult:
    pages: List[OCRPage]
    document_type: str
    overall_confidence: Optional[float]
    raw_text_pages: List[str]


def _cap_image_size(img: Image.Image, max_dim: int = _MAX_IMAGE_DIM) -> Image.Image:
    """Downscale an image so its longest side does not exceed max_dim."""
    w, h = img.size
    longest = max(w, h)
    if longest <= max_dim:
        return img
    scale = max_dim / longest
    return img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)


def load_images_from_upload(
    contents: bytes,
    content_type: Optional[str],
    filename: Optional[str],
) -> List[Image.Image]:
    is_pdf = (
        content_type == 'application/pdf'
        or (filename or '').lower().endswith('.pdf')
    )
    if is_pdf:
        return [_cap_image_size(img) for img in pdf_to_images(contents)]
    if (content_type or '').startswith('image/'):
        img = Image.open(io.BytesIO(contents)).convert('RGB')
        return [_cap_image_size(img)]
    raise HTTPException(
        status_code=400,
        detail='Upload must be an image or PDF.',
    )


def _build_ocr_page(pred, page_num: int) -> Tuple[OCRPage, str]:
    """Convert a Surya prediction into an OCRPage + raw layout text."""
    lines = []
    page_confidence_sum = 0.0
    page_confidence_count = 0

    for line in pred.text_lines:
        if not line.text.strip():
            continue
        lc = float(line.confidence)
        page_confidence_sum += lc
        page_confidence_count += 1
        lines.append(OCRLine(
            text=line.text,
            confidence=lc,
            bbox=line.bbox,
            polygon=[list(pt) for pt in line.polygon],
        ))

    page_confidence = (
        round(page_confidence_sum / page_confidence_count, 6)
        if page_confidence_count > 0
        else None
    )
    raw_text = build_layout_text(pred.text_lines)
    page = OCRPage(
        paged_idx=page_num,
        page_confidence=page_confidence,
        page_type='UNKNOWN',
        image_bbox=pred.image_bbox,
        text_lines=lines,
    )
    return page, raw_text


def _derive_document_type(page_types: List[str]) -> str:
    """
    Infer the overall document type from already-classified page types.
    Most frequent non-UNKNOWN label wins; falls back to UNKNOWN.
    Saves one Gemini API call per upload.
    """
    known = [pt for pt in page_types if pt and pt != 'UNKNOWN']
    if not known:
        return 'UNKNOWN'
    return max(set(known), key=known.count)


async def run_surya_ocr_with_classification(
    classifier: DocumentTypeClassifier,
    images: List[Image.Image],
) -> SuryaOcrPipelineResult:

    try:
        current_device = next(rec_predictor.model.parameters()).device
        print(f"[debug] Surya is running on: {current_device}")
    except AttributeError:
        print("[debug] Could not determine device directly, checking torch...")
        import torch
        print(f"[debug] MPS available: {torch.backends.mps.is_available()}")
    """
    Optimized Pipeline:
    - Batch OCR for speed
    - Language locked to English ['en'] for efficiency
    - Concurrent Classification
    """
    total = len(images)
    if total == 0:
        return SuryaOcrPipelineResult([], "UNKNOWN", None, [])

    # --- အဆင့် (၁) BATCH OCR (English Only) ---
    print(
        f'[ocr] surya starting batch recognition for {total} pages (Language: EN)...')

    all_preds = await run_in_threadpool(
        rec_predictor,
        images,
        task_names=None,
        det_predictor=det_predictor,
        detection_batch_size=_DETECTOR_BATCH_SIZE,
        recognition_batch_size=_RECOGNITION_BATCH_SIZE,
        math_mode=False,
    )
    pages_list: List[OCRPage] = []
    raw_text_pages: List[str] = []

    # --- အဆင့် (၂) BUILD PAGES & CLASSIFY ---
    async def _sanitize_and_classify(raw_text: str, page_num: int) -> Tuple[str, str]:
        # sanitize_page_with_log is pure string ops — no need for a thread
        sanitized = sanitize_page_with_log(page_num, raw_text)
        page_type = await run_in_threadpool(classifier.classify_page, sanitized, page_num)
        return sanitized, page_type

    classify_tasks: List[asyncio.Task] = []

    for idx, pred in enumerate(all_preds):
        page_num = idx + 1
        page, raw_text = _build_ocr_page(pred, page_num)
        pages_list.append(page)
        raw_text_pages.append(raw_text)

        # Classification task ကို background တွင် fire လုပ်ထားမည်
        classify_tasks.append(asyncio.create_task(
            _sanitize_and_classify(raw_text, page_num)
        ))

    print(f'[ocr] surya batch done, awaiting classification results...')
    classify_results = await asyncio.gather(*classify_tasks)

    sanitized_page_texts = [r[0] for r in classify_results]
    page_types = [r[1] for r in classify_results]

    for idx, page_type in enumerate(page_types):
        pages_list[idx].page_type = page_type
        pages_list[idx].raw_text = raw_text_pages[idx]

    # --- အဆင့် (၃) INVOICE & FINAL CLASSIFICATION ---
    invoice_page_indices = [i for i, pt in enumerate(
        page_types) if pt == 'INVOICE']
    if invoice_page_indices:
        print(
            f'[ocr] classifying {len(invoice_page_indices)} invoice companies...')
        company_types = await asyncio.gather(*[
            run_in_threadpool(classifier.classify_invoice_company,
                              sanitized_page_texts[i], i + 1)
            for i in invoice_page_indices
        ])
        for i, company_type in zip(invoice_page_indices, company_types):
            pages_list[i].sub_page_type = company_type

    document_type = _derive_document_type(page_types)

    # Confidence summary
    confidence_values = [
        line.confidence for page in pages_list for line in page.text_lines if page.text_lines
    ]
    overall_confidence = round(
        sum(confidence_values) / len(confidence_values), 6) if confidence_values else None

    return SuryaOcrPipelineResult(
        pages=pages_list,
        document_type=document_type,
        overall_confidence=overall_confidence,
        raw_text_pages=raw_text_pages,
    )
