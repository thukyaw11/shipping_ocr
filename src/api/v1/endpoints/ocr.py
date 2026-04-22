from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from src.core.auth import verify_jwt
from src.core.response_wrapper import ApiResponse
from src.models.schemas import OCRDocument
from src.services.ai.factory import get_classification_text_provider
from src.services.document_classification import DocumentTypeClassifier
from src.services.ocr_processing_service import process_ocr_upload

router = APIRouter()

_document_classifier = DocumentTypeClassifier(get_classification_text_provider())


@router.post('/surya', response_model=ApiResponse[OCRDocument])
async def ocr_file_surya(
    file: UploadFile = File(...),
    canvas_id: str | None = Form(None),
    payload: dict = Depends(verify_jwt),
):
    user_id = payload.get('sub')
    if not user_id:
        raise HTTPException(status_code=401, detail='Invalid token: missing subject')

    contents = await file.read()
    ocr_doc = await process_ocr_upload(
        contents=contents,
        filename=file.filename or 'unknown',
        content_type=file.content_type or 'application/octet-stream',
        canvas_id=canvas_id,
        user_id=user_id,
        document_classifier=_document_classifier,
    )
    return ApiResponse.ok(data=ocr_doc)
