from pydantic import BaseModel, ConfigDict, Field, computed_field
from typing import Any, Dict, List, Literal, Optional
from datetime import datetime


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
    image_bbox: List[float]
    text_lines: List[OCRLine]
    checklist: Optional[Dict[str, Any]] = None


class PageConnection(BaseModel):
    """Directed link between two pages (e.g. MAWB → HAWB)."""
    model_config = ConfigDict(populate_by_name=True)

    from_: int = Field(serialization_alias="from")
    to: int
    confidence: Optional[float] = None


class OCRDocument(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

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
