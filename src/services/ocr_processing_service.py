import asyncio
import io
import logging
import time
from datetime import datetime

from bson import ObjectId
from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool

from src.models.schemas import CanvasDocument, OCRDocument, ScanLog
from src.repositories.canvas_repository import canvas_repo
from src.repositories.ocr_result_repository import ocr_result_repo
from src.repositories.scan_log_repository import scan_log_repo
from src.services.ai.gemini_provider import build_default_gemini_provider
from src.services.checklist_extraction import extract_checklist_sync
from src.services.cross_validation import run_cross_validation
from src.services.document_classification import DocumentTypeClassifier
from src.services.pricing import compute_cost, get_price_per_page
from src.services.s3_service import s3_service
from src.services.surya_ocr_pipeline import (
    load_images_from_upload,
    run_surya_ocr_with_classification,
)

logger = logging.getLogger('shipping_bill_ocr')


async def _resolve_canvas(
    canvas_id: str | None, user_id: str, filename: str
) -> tuple[str, int]:
    """Return (canvas_id_str, sort_order). Creates a new canvas when canvas_id is None."""
    if canvas_id is None:
        canvas = CanvasDocument(user_id=user_id, name=filename)
        new_id = await canvas_repo.create(canvas.model_dump())
        return new_id, 0

    try:
        ObjectId(canvas_id)
    except Exception:
        raise HTTPException(
            status_code=400, detail="Invalid canvas_id format.")

    existing = await canvas_repo.get_by_id(canvas_id, user_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Canvas not found.")

    sort_order = await ocr_result_repo.count_all_in_canvas(canvas_id)
    return canvas_id, sort_order


async def process_ocr_upload(
    contents: bytes,
    filename: str,
    content_type: str,
    canvas_id: str | None,
    user_id: str,
    document_classifier: DocumentTypeClassifier,
) -> OCRDocument:
    started_at = time.monotonic()
    file_size = len(contents)

    async def _write_scan_log(
        status: str,
        resolved_canvas_id: str | None = None,
        ocr_result_id: str | None = None,
        total_pages: int | None = None,
        document_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        price_per_page: float | None = None
        pages_charged: int | None = None
        total_cost: float | None = None
        if status == 'success' and total_pages:
            price_per_page = await get_price_per_page()
            pages_charged = total_pages
            total_cost = compute_cost(pages_charged, price_per_page)
        log = ScanLog(
            user_id=user_id,
            filename=filename,
            file_size_bytes=file_size,
            content_type=content_type,
            canvas_id=resolved_canvas_id,
            ocr_result_id=ocr_result_id,
            total_pages=total_pages,
            document_type=document_type,
            status=status,
            error_message=error_message,
            processing_time_ms=elapsed_ms,
            price_per_page=price_per_page,
            pages_charged=pages_charged,
            total_cost=total_cost,
        )
        await scan_log_repo.create(log.model_dump())

    try:
        unique_filename = f'{datetime.utcnow().timestamp()}_{filename}'
        file_url = await run_in_threadpool(
            s3_service.upload_file,
            io.BytesIO(contents),
            unique_filename,
            content_type,
        )
        logger.debug('[ocr] file uploaded: %s', file_url)

        images = load_images_from_upload(contents, content_type, filename)
        pipeline_result = await run_surya_ocr_with_classification(
            document_classifier, images
        )

        gemini = build_default_gemini_provider()
        if gemini:
            logger.debug(
                '[ocr] extracting checklists for %d page(s)', len(
                    pipeline_result.pages)
            )
            checklist_tasks = [
                run_in_threadpool(
                    extract_checklist_sync,
                    gemini,
                    page.page_type or '',
                    raw_text,
                    sub_page_type=page.sub_page_type,
                )
                for page, raw_text in zip(
                    pipeline_result.pages, pipeline_result.raw_text_pages
                )
            ]
            checklists = await asyncio.gather(*checklist_tasks)
            for page, checklist in zip(pipeline_result.pages, checklists):
                if checklist is not None:
                    page.checklist = checklist
        else:
            logger.debug('[ocr] skip checklists (Gemini not configured)')

        cv_results = run_cross_validation(pipeline_result.pages)
        resolved_canvas_id, sort_order = await _resolve_canvas(canvas_id, user_id, filename)

        ocr_doc = OCRDocument(
            canvas_id=resolved_canvas_id,
            sort_order=sort_order,
            user_id=user_id,
            filename=filename,
            total_pages=len(pipeline_result.pages),
            overall_confidence=pipeline_result.overall_confidence,
            document_type=pipeline_result.document_type,
            data=pipeline_result.pages,
            status='success',
            created_at=datetime.now(datetime.timezone.utc),
            url=file_url,
            type=content_type,
            cross_validation_results=cv_results,
        )

        try:
            insert_payload = ocr_doc.model_dump(
                exclude_computed_fields=True,
                exclude={'cross_validation_results'},
            )
        except TypeError:
            insert_payload = ocr_doc.model_dump()
            insert_payload.pop('checklists', None)
            insert_payload.pop('cross_validation_results', None)

        ocr_result_id = await ocr_result_repo.create(insert_payload)
        await canvas_repo.touch(resolved_canvas_id)

        logger.debug('[ocr] done in %.2fs', time.monotonic() - started_at)
        await _write_scan_log(
            status='success',
            resolved_canvas_id=resolved_canvas_id,
            ocr_result_id=ocr_result_id,
            total_pages=len(pipeline_result.pages),
            document_type=pipeline_result.document_type,
        )
        return ocr_doc

    except HTTPException:
        raise
    except Exception as exc:
        await _write_scan_log(status='failed', error_message=str(exc))
        raise
