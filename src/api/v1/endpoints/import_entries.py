from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from src.core.auth import verify_jwt
from src.core.response_wrapper import ApiResponse
from src.models.import_entry_schemas import ImportEntryDocument
from src.repositories.canvas_repository import canvas_repo
from src.repositories.import_entry_repository import import_entry_repo
from src.services.ocr_processing_service import process_import_entry_upload

router = APIRouter()


@router.post(
    "/canvases/{canvas_id}/import-entries/upload",
    response_model=ApiResponse[Dict[str, Any]],
)
async def upload_import_entry(
    canvas_id: str,
    file: UploadFile = File(...),
    payload: dict = Depends(verify_jwt),
):
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token: missing subject")
    contents = await file.read()
    entry = await process_import_entry_upload(
        contents=contents,
        filename=file.filename or "unknown",
        content_type=file.content_type or "application/octet-stream",
        canvas_id=canvas_id,
        user_id=user_id,
    )
    return ApiResponse.ok(data=entry.model_dump())


def _serialize(doc: dict) -> dict:
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    return doc


@router.get(
    "/canvases/{canvas_id}/import-entries",
    response_model=ApiResponse[List[Dict[str, Any]]],
)
async def list_import_entries(
    canvas_id: str,
    payload: dict = Depends(verify_jwt),
):
    user_id = payload["sub"]
    canvas = await canvas_repo.get_by_id(canvas_id, user_id)
    if not canvas:
        raise HTTPException(status_code=404, detail="Canvas not found.")
    entries = await import_entry_repo.list_all(canvas_id, user_id)
    return ApiResponse.ok(data=[_serialize(e) for e in entries])


@router.get(
    "/canvases/{canvas_id}/import-entries/active",
    response_model=ApiResponse[Optional[Dict[str, Any]]],
)
async def get_active_import_entry(
    canvas_id: str,
    payload: dict = Depends(verify_jwt),
):
    user_id = payload["sub"]
    canvas = await canvas_repo.get_by_id(canvas_id, user_id)
    if not canvas:
        raise HTTPException(status_code=404, detail="Canvas not found.")
    entry = await import_entry_repo.get_active(canvas_id, user_id)
    if not entry:
        return ApiResponse.ok(data=None, message="No import entry found.")
    return ApiResponse.ok(data=_serialize(entry))


@router.delete(
    "/canvases/{canvas_id}/import-entries/{entry_id}",
    response_model=ApiResponse[None],
)
async def delete_import_entry(
    canvas_id: str,
    entry_id: str,
    payload: dict = Depends(verify_jwt),
):
    user_id = payload["sub"]
    canvas = await canvas_repo.get_by_id(canvas_id, user_id)
    if not canvas:
        raise HTTPException(status_code=404, detail="Canvas not found.")
    entry = await import_entry_repo.get_by_id(entry_id, user_id)
    if not entry or entry.get("canvas_id") != canvas_id:
        raise HTTPException(status_code=404, detail="Import entry not found.")
    await import_entry_repo.soft_delete(entry_id, user_id)
    return ApiResponse.ok(message="Import entry deleted.")
