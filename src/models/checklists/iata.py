from typing import Optional

from pydantic import BaseModel

from src.models.checklists.config import checklist_model_config


class IATAChecklist(BaseModel):
    model_config = checklist_model_config

    awb_number: Optional[str] = None
    shipper_reference_number: Optional[str] = None
    date: Optional[str] = None
