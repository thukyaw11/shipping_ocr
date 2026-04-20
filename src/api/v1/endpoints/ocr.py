import asyncio
import io
import time
from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from src.core.auth import verify_jwt
from src.core.database import db
from src.core.response_wrapper import ApiResponse
from src.models.schemas import CanvasDocument, OCRDocument, ScanLog
from src.services.pricing import compute_cost, get_price_per_page
from src.services.ai.factory import get_classification_text_provider
from src.services.ai.gemini_provider import build_default_gemini_provider
from src.services.checklist_extraction import extract_checklist_sync
from src.services.cross_validation import run_cross_validation
from src.services.document_classification import DocumentTypeClassifier
from src.services.s3_service import s3_service
from src.services.surya_ocr_pipeline import (
    load_images_from_upload,
    run_surya_ocr_with_classification,
)

router = APIRouter()

_document_classifier = DocumentTypeClassifier(get_classification_text_provider())


async def _resolve_canvas(canvas_id: str | None, user_id: str, filename: str) -> tuple[str, int]:
    """Return (canvas_id_str, sort_order) — creates a new canvas when canvas_id is None."""
    if canvas_id is None:
        canvas = CanvasDocument(
            user_id=user_id,
            name=filename,
        )
        result = await db.db["canvases"].insert_one(canvas.model_dump())
        return str(result.inserted_id), 0

    try:
        oid = ObjectId(canvas_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid canvas_id format.")

    existing = await db.db["canvases"].find_one({"_id": oid, "user_id": user_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Canvas not found.")

    sort_order = await db.db["ocr_results"].count_documents({"canvas_id": canvas_id})
    return canvas_id, sort_order


@router.post('/surya', response_model=ApiResponse[OCRDocument])
async def ocr_file_surya(
    file: UploadFile = File(...),
    canvas_id: str | None = Form(None),
    payload: dict = Depends(verify_jwt),
):
    started_at = time.monotonic()
    print(f'[ocr] start file={file.filename} content_type={file.content_type}')
    contents = await file.read()

    user_id = payload.get('sub')
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail='Invalid token: missing subject',
        )

    filename = file.filename or 'unknown'
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
            content_type=file.content_type,
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
        await db.db['scan_logs'].insert_one(log.model_dump())

    try:
        file_buffer = io.BytesIO(contents)
        unique_filename = f'{datetime.utcnow().timestamp()}_{filename}'

        file_url = await run_in_threadpool(
            s3_service.upload_file,
            file_buffer,
            unique_filename,
            file.content_type,
        )
        print('[ocr] uploaded file_url ready')

        images = load_images_from_upload(contents, file.content_type, file.filename)

        pipeline_result = await run_surya_ocr_with_classification(
            _document_classifier,
            images,
        )

        gemini = build_default_gemini_provider()
        if gemini:
            print(
                f'[ocr] extracting checklists for '
                f'{len(pipeline_result.pages)} page(s) with Gemini...',
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
                    pipeline_result.pages,
                    pipeline_result.raw_text_pages,
                )
            ]
            checklists = await asyncio.gather(*checklist_tasks)
            for page, checklist in zip(pipeline_result.pages, checklists):
                if checklist is not None:
                    page.checklist = checklist
            print('[ocr] checklist extraction done')
        else:
            print('[ocr] skip checklists (Gemini not configured)')

        cv_results = run_cross_validation(pipeline_result.pages)

        resolved_canvas_id, sort_order = await _resolve_canvas(
            canvas_id, user_id, filename
        )

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
            created_at=datetime.utcnow(),
            url=file_url,
            type=file.content_type,
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

        result = await db.db['ocr_results'].insert_one(insert_payload)
        ocr_result_id = str(result.inserted_id)

        await db.db['canvases'].update_one(
            {"_id": ObjectId(resolved_canvas_id)},
            {"$set": {"edited_at": datetime.utcnow()}},
        )

        print(f'[ocr] insert done in {time.monotonic() - started_at:.2f}s')
        await _write_scan_log(
            status='success',
            resolved_canvas_id=resolved_canvas_id,
            ocr_result_id=ocr_result_id,
            total_pages=len(pipeline_result.pages),
            document_type=pipeline_result.document_type,
        )
        return ApiResponse.ok(data=ocr_doc)

    except HTTPException:
        raise
    except Exception as exc:
        await _write_scan_log(status='failed', error_message=str(exc))
        raise
