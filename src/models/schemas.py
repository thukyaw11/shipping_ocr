from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime


class OCRLine(BaseModel):
    text: str
    confidence: float
    bbox: List[float]
    polygon: List[List[float]]


class OCRPage(BaseModel):
    paged_idx: int
    page_confidence: Optional[float] = None
    page_type: Optional[str] = None
    image_bbox: List[float]
    text_lines: List[OCRLine]


class OCRDocument(BaseModel):
    user_id: Optional[str] = None
    filename: str
    total_pages: int
    overall_confidence: Optional[float] = None
    document_type: Optional[str] = None
    data: List[OCRPage]
    status: str = "completed"
    type: str = "pdf"
    url: str
    preview: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    edited_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True
