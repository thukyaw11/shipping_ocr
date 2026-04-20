from pydantic import BaseModel, ConfigDict, Field, computed_field
from typing import Any, Dict, List, Literal, Optional
from datetime import datetime


class CanvasDocument(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: str
    name: str
    status: str = "active"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    edited_at: datetime = Field(default_factory=datetime.utcnow)
    is_deleted: bool = False
    deleted_at: Optional[datetime] = None


class ValidationResult(BaseModel):
    """Result of a single cross-validation rule evaluation."""
    category: Optional[str] = None
    rule_name: str
    status: Literal['pass', 'fail', 'skipped']
    expected: Optional[Any] = None
    actual: Optional[Any] = None
    message: str = ''


class OCRLine(BaseModel):
    text: str
    confidence: float
    bbox: List[float]
    polygon: List[List[float]]


class OCRPage(BaseModel):
    paged_idx: int
    page_confidence: Optional[float] = None
    page_type: Optional[str] = None
    sub_page_type: Optional[str] = None
    image_bbox: List[float]
    text_lines: List[OCRLine]
    checklist: Optional[Dict[str, Any]] = None
    raw_text: Optional[str] = None


class PageConnection(BaseModel):
    """Directed link between two pages (e.g. MAWB → HAWB)."""
    model_config = ConfigDict(populate_by_name=True)

    from_: int = Field(serialization_alias="from")
    to: int
    confidence: Optional[float] = None


class OCRDocument(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    canvas_id: Optional[str] = None
    sort_order: int = 0
    user_id: Optional[str] = None
    filename: str
    total_pages: int
    overall_confidence: Optional[float] = None
    document_type: Optional[str] = None
    data: List[OCRPage]
    connections: Optional[List[PageConnection]] = None
    status: str = "completed"
    type: str = "pdf"
    url: str
    preview: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    edited_at: datetime = Field(default_factory=datetime.utcnow)
    cross_validation_results: Optional[List[ValidationResult]] = None

    @computed_field
    @property
    def checklists(self) -> List[Optional[Dict[str, Any]]]:
        return [p.checklist for p in self.data]


class ScanLog(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: str
    filename: str
    file_size_bytes: Optional[int] = None
    content_type: Optional[str] = None
    canvas_id: Optional[str] = None
    ocr_result_id: Optional[str] = None
    total_pages: Optional[int] = None
    document_type: Optional[str] = None
    status: Literal["success", "failed"] = "success"
    error_message: Optional[str] = None
    processing_time_ms: Optional[int] = None
    # Billing fields — only populated on success
    price_per_page: Optional[float] = None
    pages_charged: Optional[int] = None
    total_cost: Optional[float] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
