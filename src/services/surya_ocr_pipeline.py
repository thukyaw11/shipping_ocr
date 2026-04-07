import asyncio
import io
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


@dataclass
class SuryaOcrPipelineResult:
    pages: List[OCRPage]
    document_type: str
    overall_confidence: Optional[float]
    raw_text_pages: List[str]


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
        return pdf_to_images(contents)
    if (content_type or '').startswith('image/'):
        return [Image.open(io.BytesIO(contents)).convert('RGB')]
    raise HTTPException(
        status_code=400,
        detail='Upload must be an image or PDF.',
    )


def run_surya_recognition(images: List[Image.Image]):
    """
    Run recognition one page at a time. Batching many PDF pages into a single
    rec_predictor() call can trigger torch/MPS errors (e.g. bogus indices in
    the vision encoder) on large documents.
    """
    predictions = []
    for idx, img in enumerate(images, start=1):
        print(f'[ocr] surya page {idx}/{len(images)} recognition...')
        batch_preds = rec_predictor([img], det_predictor=det_predictor)
        predictions.extend(batch_preds)
    return predictions


def build_pages_from_predictions(predictions) -> Tuple[
    List[OCRPage],
    List[str],
    Optional[float],
]:
    pages_list: List[OCRPage] = []
    raw_text_pages: List[str] = []
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
                polygon=[list(pt) for pt in line.polygon],
            ))
        page_confidence = (
            round(page_confidence_sum / page_confidence_count, 6)
            if page_confidence_count > 0
            else None
        )
        pages_list.append(OCRPage(
            paged_idx=page_num,
            page_confidence=page_confidence,
            page_type='UNKNOWN',
            image_bbox=pred.image_bbox,
            text_lines=lines,
        ))

    overall_confidence = (
        round(confidence_sum / confidence_count, 6)
        if confidence_count > 0
        else None
    )
    return pages_list, raw_text_pages, overall_confidence


async def run_surya_ocr_with_classification(
    classifier: DocumentTypeClassifier,
    images: List[Image.Image],
) -> SuryaOcrPipelineResult:
    predictions = await run_in_threadpool(
        lambda: run_surya_recognition(images),
    )
    print(
        f'[ocr] surya predictions ready pages={len(predictions) if predictions else 0}',
    )

    pages_list, raw_text_pages, overall_confidence = build_pages_from_predictions(
        predictions,
    )
    print('[ocr] built page objects. computing confidence/type...')

    print(
        f'[ocr] sanitizing {len(raw_text_pages)} page(s) for type classification...',
    )
    sanitized_page_texts = await asyncio.gather(
        *[
            run_in_threadpool(sanitize_page_with_log, idx + 1, t)
            for idx, t in enumerate(raw_text_pages)
        ],
    )

    page_type_tasks = [
        run_in_threadpool(classifier.classify_page, txt, idx + 1)
        for idx, txt in enumerate(sanitized_page_texts)
    ]
    print(f'[ocr] classifying {len(page_type_tasks)} page(s) for page_type...')
    page_types = await asyncio.gather(*page_type_tasks)
    for idx, page_type in enumerate(page_types):
        pages_list[idx].page_type = page_type

    print('[ocr] page types classified')

    # Classify invoice company sub-type for INVOICE pages
    invoice_company_tasks = []
    invoice_page_indices = []
    for idx, page_type in enumerate(page_types):
        if page_type == 'INVOICE':
            invoice_page_indices.append(idx)
            invoice_company_tasks.append(
                run_in_threadpool(
                    classifier.classify_invoice_company,
                    sanitized_page_texts[idx],
                    idx + 1
                )
            )

    if invoice_company_tasks:
        print(f'[ocr] classifying {len(invoice_company_tasks)} invoice page(s) for company...')
        company_types = await asyncio.gather(*invoice_company_tasks)
        for invoice_idx, company_type in zip(invoice_page_indices, company_types):
            pages_list[invoice_idx].sub_page_type = company_type
        print('[ocr] invoice companies classified')

    doc_clean_text = '\n\n'.join(sanitized_page_texts)
    print('[ocr] classifying document_type...')
    started = time.monotonic()
    document_type = await run_in_threadpool(
        classifier.classify_document,
        doc_clean_text,
    )
    print(
        f'[ocr] document type classified={document_type} '
        f'took_ms={(time.monotonic() - started) * 1000.0:.1f}',
    )

    return SuryaOcrPipelineResult(
        pages=pages_list,
        document_type=document_type,
        overall_confidence=overall_confidence,
        raw_text_pages=raw_text_pages,
    )
