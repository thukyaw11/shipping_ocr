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
    image_bbox: List[float]
    text_lines: List[OCRLine]

class OCRDocument(BaseModel):
    filename: str
    total_pages: int
    data: List[OCRPage]
    status: str = "completed"
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True