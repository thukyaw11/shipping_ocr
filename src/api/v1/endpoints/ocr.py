import asyncio
import io
import time
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from src.core.auth import verify_jwt
from src.core.database import db
from src.core.response_wrapper import ApiResponse
from src.models.schemas import OCRDocument
from src.services.ai.factory import get_classification_text_provider
from src.services.ai.gemini_provider import build_default_gemini_provider
from src.services.checklist_extraction import extract_checklist_sync
from src.services.document_classification import DocumentTypeClassifier
from src.services.s3_service import s3_service
from src.services.surya_ocr_pipeline import (
    load_images_from_upload,
    run_surya_ocr_with_classification,
)

router = APIRouter()

_document_classifier = DocumentTypeClassifier(get_classification_text_provider())


@router.post('/surya', response_model=ApiResponse[OCRDocument])
async def ocr_file_surya(
    file: UploadFile = File(...),
    payload: dict = Depends(verify_jwt),
):
    started_at = time.monotonic()
    print(f'[ocr] start file={file.filename} content_type={file.content_type}')
    contents = await file.read()

    file_buffer = io.BytesIO(contents)
    unique_filename = f'{datetime.utcnow().timestamp()}_{file.filename}'

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

    user_id = payload.get('sub')
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail='Invalid token: missing subject',
        )

    ocr_doc = OCRDocument(
        user_id=user_id,
        filename=file.filename or 'unknown',
        total_pages=len(pipeline_result.pages),
        overall_confidence=pipeline_result.overall_confidence,
        document_type=pipeline_result.document_type,
        data=pipeline_result.pages,
        status='success',
        created_at=datetime.utcnow(),
        url=file_url,
        type=file.content_type,
    )

    try:
        insert_payload = ocr_doc.model_dump(exclude_computed_fields=True)
    except TypeError:
        insert_payload = ocr_doc.model_dump()
        insert_payload.pop('checklists', None)
    await db.db['ocr_results'].insert_one(insert_payload)

    print(f'[ocr] insert done in {time.monotonic() - started_at:.2f}s')
    return ApiResponse.ok(data=ocr_doc)
