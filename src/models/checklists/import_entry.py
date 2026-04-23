from typing import List, Optional

from pydantic import BaseModel

from src.models.checklists.config import checklist_model_config


class ImportEntryChecklist(BaseModel):
    model_config = checklist_model_config

    entry_number: Optional[str] = None
    entry_date: Optional[str] = None
    importer_name: Optional[str] = None
    importer_tin: Optional[str] = None
    supplier_name: Optional[str] = None
    country_of_origin: Optional[str] = None
    country_of_export: Optional[str] = None
    port_of_entry: Optional[str] = None
    awb_number: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    total_cif_value: Optional[float] = None
    currency: Optional[str] = None
    total_duty: Optional[float] = None
    total_tax: Optional[float] = None
    total_payable: Optional[float] = None
    hs_codes: List[str] = []
    item_descriptions: List[str] = []
    remarks: Optional[str] = None
