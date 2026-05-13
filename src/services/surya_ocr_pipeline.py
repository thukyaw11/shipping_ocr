import io
import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from PIL import Image

from src.models.schemas import OCRLine, OCRPage
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
    document_type: Optional[str]
    overall_confidence: Optional[float]
    raw_text_pages: List[str]


def _cap_image_size(img: Image.Image, max_dim: int = _MAX_IMAGE_DIM) -> Image.Image:
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
        page_type=None,
        image_bbox=pred.image_bbox,
        text_lines=lines,
    )
    return page, raw_text


async def run_surya_ocr_plain(
    images: List[Image.Image],
) -> SuryaOcrPipelineResult:
    total = len(images)
    if total == 0:
        return SuryaOcrPipelineResult([], None, None, [])

    print(f'[ocr] surya starting batch recognition for {total} pages...')

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

    for idx, pred in enumerate(all_preds):
        page, raw_text = _build_ocr_page(pred, idx + 1)
        page.raw_text = raw_text
        pages_list.append(page)
        raw_text_pages.append(raw_text)

    confidence_values = [
        line.confidence for page in pages_list for line in page.text_lines if page.text_lines
    ]
    overall_confidence = (
        round(sum(confidence_values) / len(confidence_values), 6)
        if confidence_values else None
    )

    return SuryaOcrPipelineResult(
        pages=pages_list,
        document_type=None,
        overall_confidence=overall_confidence,
        raw_text_pages=raw_text_pages,
    )
