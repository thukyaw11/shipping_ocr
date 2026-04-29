from typing import List, Optional
from pydantic import BaseModel, Field
from src.models.checklists.config import checklist_model_config


class MoneyAmount(BaseModel):
    amount: float
    currency: str


class Consignee(BaseModel):
    name: str
    address: Optional[str] = None
    tax_id: Optional[str] = None


class ExchangeRateEntry(BaseModel):
    currency: str
    amount: float


class ExchangeRate(BaseModel):
    from_entry: ExchangeRateEntry = Field(..., alias="from")
    to: ExchangeRateEntry


class PackageItem(BaseModel):
    hs_code: str
    description: str
    invoice_number: str
    import_tax_percent: float
    fob: MoneyAmount
    import_duty: MoneyAmount
    vat: MoneyAmount
    foreign_price: MoneyAmount
    local_price: MoneyAmount
    weight_kg: float


class SummaryTotals(BaseModel):
    total_fob: MoneyAmount
    total_import_duty: MoneyAmount
    total_vat: MoneyAmount
    total_tax_to_pay: MoneyAmount


class ImportEntryChecklist(BaseModel):
    consignee: Consignee
    flight_number: str
    date: str
    exchange_rate: ExchangeRate
    gross_weight: float
    package_count: int
    package_list: List[PackageItem]
    summary_totals: SummaryTotals

    model_config = checklist_model_config
