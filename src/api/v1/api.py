from fastapi import APIRouter
from src.api.v1.endpoints import ocr, results

api_router = APIRouter()

api_router.include_router(ocr.router, prefix="/ocr", tags=["OCR Processing"])
api_router.include_router(results.router, prefix="/history", tags=["OCR History"])