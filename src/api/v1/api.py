from fastapi import APIRouter
from src.api.v1.endpoints import ocr, results, auth_jwt, customers, scan_logs, settings

api_router = APIRouter()

api_router.include_router(auth_jwt.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(ocr.router, prefix="/ocr", tags=["OCR Processing"])
api_router.include_router(results.router, prefix="/history", tags=["OCR History"])
api_router.include_router(customers.router, prefix="/customers", tags=["Customers"])
api_router.include_router(scan_logs.router, prefix="/scan-logs", tags=["Scan Logs"])
api_router.include_router(settings.router, prefix="/settings", tags=["Settings"])