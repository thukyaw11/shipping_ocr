from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.models.schemas import OCRPage


class ImportEntryDocument(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    canvas_id: str
    user_id: str
    version: int
    filename: str
    url: str
    type: str = "pdf"
    preview: Optional[str] = None
    total_pages: int
    overall_confidence: Optional[float] = None
    data: List[OCRPage] = Field(default_factory=list)
    status: str = "completed"
    # optional link to a related shipping document (MAWB / HAWB / etc.)
    ocr_result_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    edited_at: datetime = Field(default_factory=datetime.utcnow)
    is_deleted: bool = False
    deleted_at: Optional[datetime] = None
