from fastapi import APIRouter, UploadFile, File, HTTPException
from src.core.response_wrapper import ApiResponse
from src.models.schemas import OCRDocument, OCRPage, OCRLine
from src.core.database import db
from datetime import datetime
import io
from PIL import Image
from src.utils import pdf_to_images

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

@router.post("/surya", response_model=ApiResponse[OCRDocument])
async def ocr_file_surya(file: UploadFile = File(...)):
    contents = await file.read()
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
    for page_num, pred in enumerate(predictions, start=1):
        lines = []
        for line in pred.text_lines:
            if not line.text.strip():
                continue

            lines.append(OCRLine(
                text=line.text,
                confidence=float(line.confidence),
                bbox=line.bbox,
                polygon=[list(pt) for pt in line.polygon]
            ))

        pages_list.append(OCRPage(
            paged_idx=page_num,
            image_bbox=pred.image_bbox,
            text_lines=lines
        ))

    ocr_doc = OCRDocument(
        filename=file.filename or "unknown",
        total_pages=len(pages_list),
        data=pages_list,
        status="success",
        created_at=datetime.utcnow()
    )

    insert_result = await db.db["ocr_results"].insert_one(ocr_doc.model_dump())

    return ApiResponse.ok(data=ocr_doc)
