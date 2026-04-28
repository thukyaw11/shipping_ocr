from fastapi import APIRouter
from src.api.v1.endpoints import internal

api_router = APIRouter()

api_router.include_router(internal.router, prefix="", tags=["Internal"])
