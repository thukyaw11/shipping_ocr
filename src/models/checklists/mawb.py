from typing import Optional

from pydantic import BaseModel

from src.models.checklists.config import checklist_model_config


class MAWBCheckList(BaseModel):
    model_config = checklist_model_config

    awb_number: Optional[str] = None
    shipper_name: Optional[str] = None
    consignee_name: Optional[str] = None
    total_weight: Optional[float] = None
