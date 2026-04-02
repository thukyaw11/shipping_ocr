from datetime import date
from typing import Optional

from pydantic import BaseModel

from src.models.checklists.config import checklist_model_config


class InvoiceChecklist(BaseModel):
    model_config = checklist_model_config

    invoice_no: Optional[str] = None
    date: Optional[date] = None
    total_amount: Optional[float] = None
