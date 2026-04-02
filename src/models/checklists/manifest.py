from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field

from src.models.checklists.config import checklist_model_config
from src.models.checklists.hawb import HawbEntry


class ManifestChecklist(BaseModel):
    model_config = checklist_model_config

    flight_no: Optional[str] = None
    flight_date: Optional[date] = None
    origin: Optional[str] = None
    destination: Optional[str] = None
    total_pcs: Optional[int] = Field(None, ge=0)
    total_weight: Optional[float] = None
    hawb_list: List[HawbEntry] = Field(default_factory=list)
