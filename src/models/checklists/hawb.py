from typing import Optional

from pydantic import BaseModel, Field

from src.models.checklists.config import checklist_model_config


class HawbEntry(BaseModel):
    model_config = checklist_model_config

    hawb_no: str
    pcs: int = Field(..., gt=0)
    weight_kg: float = Field(..., gt=0)
    destination: str
    goods_description: Optional[str] = None
