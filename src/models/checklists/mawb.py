from typing import Optional, List

from pydantic import BaseModel

from src.models.checklists.config import checklist_model_config


class MAWBCheckList(BaseModel):
    model_config = checklist_model_config

    awb_number: Optional[str] = None
    airline_prefix: Optional[str] = None
    shipper_name: Optional[str] = None
    shipper_address: Optional[str] = None
    consignee_name: Optional[str] = None
    consignee_address: Optional[str] = None
    total_weight: Optional[float] = None
    total_prepaid: Optional[float] = None
    total_other_charges: Optional[float] = None
    currency: Optional[str] = None
    execution_date: Optional[str] = None
    flight_number: Optional[str] = None
    freight_numbers: List[str] = []
    
