import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.core.config import Config
from src.services.ai.base import TextGenerationProvider

logger = logging.getLogger("shipping_bill_ocr")

ALLOWED_DOCUMENT_TYPES: frozenset[str] = frozenset({
    'MAWB',
    'HAWB',
    'IATA',
    'INVOICE',
    'CARGO_MANIFEST',
    'UNKNOWN',
})

ALLOWED_INVOICE_COMPANIES: frozenset[str] = frozenset({
    'SYMRISE',
    'TAKASAGO',
    'GIVAUDAN',
    'IFF',
    'FLAVOR FORCE',
    'SILESIA',
    'SHERWIN',
    'ALLNEX',
    'KH ROBERT',
    'THAI SPECIALITY',
    'PERSPECES',
    'NOURYON',
    'COLOSSAL INTERNATIONAL',
})

_LABEL_ORDER: tuple[str, ...] = (
    'MAWB', 'HAWB', 'IATA', 'INVOICE', 'CARGO_MANIFEST', 'UNKNOWN',
)

_INVOICE_COMPANY_ORDER: tuple[str, ...] = (
    'SYMRISE', 'TAKASAGO', 'GIVAUDAN', 'IFF', 'FLAVOR FORCE', 'SILESIA',
    'SHERWIN', 'ALLNEX', 'KH ROBERT', 'THAI SPECIALITY', 'PERSPECES',
    'NOURYON', 'COLOSSAL INTERNATIONAL'
)

CLASSIFICATION_SYSTEM_PROMPT = (
    'Classify logistics/air-cargo OCR text into exactly one label: '
    'MAWB, HAWB, IATA, INVOICE, CARGO_MANIFEST, UNKNOWN. '
    'Use these rules: '
    'CARGO_MANIFEST usually has a table/list of multiple shipments or items, often with shipper and consignee columns/entries; '
    'MAWB/HAWB are air waybill documents with AWB numbers and airway bill fields; '
    'INVOICE is billing-focused with invoice totals/tax/amount due; '
    'IATA documents usually follow IATA-standard air cargo/air waybill formats and terminology. '
    'Return only one label with no explanation.'
)

INVOICE_COMPANY_CLASSIFICATION_SYSTEM_PROMPT = (
    'Identify which company this INVOICE belongs to. Look for company name, letterhead, logo text, '
    'or any identifying information in the OCR text. '
    'Return exactly one company name from this list: '
    'SYMRISE, TAKASAGO, GIVAUDAN, IFF, FLAVOR FORCE, SILESIA, SHERWIN, ALLNEX, '
    'KH ROBERT, THAI SPECIALITY, PERSPECES, NOURYON, COLOSSAL INTERNATIONAL. '
    'If no company can be identified, return UNKNOWN. '
    'Return only the company name with no explanation.'
)


class DocumentTypeClassificationOutput(BaseModel):
    """JSON schema for Gemini structured classification responses."""

    model_config = ConfigDict(extra='forbid')
    document_type: Literal['MAWB', 'HAWB', 'IATA', 'INVOICE', 'CARGO_MANIFEST', 'UNKNOWN'] = Field(
        description='The single best classification label for the logistics/air-cargo document.',
    )


class InvoiceCompanyClassificationOutput(BaseModel):
    """JSON schema for invoice company classification responses."""

    model_config = ConfigDict(extra='forbid')
    company: Literal['SYMRISE', 'TAKASAGO', 'GIVAUDAN', 'IFF', 'FLAVOR FORCE', 'SILESIA',
                      'SHERWIN', 'ALLNEX', 'KH ROBERT', 'THAI SPECIALITY', 'PERSPECES',
                      'NOURYON', 'COLOSSAL INTERNATIONAL', 'UNKNOWN'] = Field(
        description='The company name for this invoice.',
    )


def sanitize_ocr_text(ocr_text: str, max_chars: int = 4000) -> str:
    if not ocr_text.strip():
        return ocr_text
    normalized = ' '.join(ocr_text.split())
    return normalized[:max_chars]


def parse_label_from_model_output(raw: str) -> str:
    upper = (raw or '').upper()
    label_found = 'UNKNOWN'
    for label in _LABEL_ORDER:
        if label in upper:
            label_found = label
            break
    return label_found


def parse_company_from_model_output(raw: str) -> str:
    """Parse invoice company name from model output."""
    upper = (raw or '').upper()
    company_found = 'UNKNOWN'
    for company in _INVOICE_COMPANY_ORDER:
        if company in upper:
            company_found = company
            break
    return company_found


def normalize_classification_label(value: str) -> str:
    u = (value or '').strip().upper()
    if u in ALLOWED_DOCUMENT_TYPES:
        return u
    return parse_label_from_model_output(u)


def normalize_company_label(value: str) -> str:
    """Normalize and validate invoice company label."""
    u = (value or '').strip().upper()
    if u in ALLOWED_INVOICE_COMPANIES:
        return u
    if u == 'UNKNOWN':
        return 'UNKNOWN'
    return parse_company_from_model_output(u)


class DocumentTypeClassifier:
    """Maps OCR text to a single logistics document label using a TextGenerationProvider."""

    def __init__(self, provider: TextGenerationProvider) -> None:
        self._provider = provider

    def classify_document(self, clean_text: str) -> str:
        if not clean_text.strip():
            return 'UNKNOWN'
        prompt = f'Classify this whole document OCR text:\n\n{clean_text}'
        return self._classify_with_prompt(prompt)

    def classify_page(self, clean_text: str, page_num: int) -> str:
        if not clean_text.strip():
            return 'UNKNOWN'
        prompt = f'Classify this page OCR text (page {page_num}):\n\n{clean_text}'
        return self._classify_with_prompt(prompt)

    def classify_invoice_company(self, clean_text: str, page_num: int) -> str:
        """Classify which company an invoice belongs to."""
        if not clean_text.strip():
            return 'UNKNOWN'
        prompt = f'Identify the company for this invoice page (page {page_num}):\n\n{clean_text}'
        return self._classify_invoice_company_with_prompt(prompt)

    def _classify_with_prompt(self, prompt: str) -> str:
        structured = getattr(self._provider, 'generate_structured_json', None)
        if callable(structured):
            try:
                out = structured(
                    CLASSIFICATION_SYSTEM_PROMPT,
                    prompt,
                    DocumentTypeClassificationOutput,
                )
                return normalize_classification_label(out.document_type)
            except Exception:
                logger.exception(
                    'Structured classification failed; not falling back to plain text',
                )
                return 'UNKNOWN'
        try:
            raw = self._provider.generate(CLASSIFICATION_SYSTEM_PROMPT, prompt)
            return parse_label_from_model_output(raw)
        except Exception:
            logger.exception('Classification LLM call failed')
            return 'UNKNOWN'

    def _classify_invoice_company_with_prompt(self, prompt: str) -> str:
        """Classify invoice company with structured or fallback approach."""
        structured = getattr(self._provider, 'generate_structured_json', None)
        if callable(structured):
            try:
                out = structured(
                    INVOICE_COMPANY_CLASSIFICATION_SYSTEM_PROMPT,
                    prompt,
                    InvoiceCompanyClassificationOutput,
                )
                return normalize_company_label(out.company)
            except Exception:
                logger.exception(
                    'Structured invoice company classification failed; not falling back to plain text',
                )
                return 'UNKNOWN'
        try:
            raw = self._provider.generate(INVOICE_COMPANY_CLASSIFICATION_SYSTEM_PROMPT, prompt)
            return parse_company_from_model_output(raw)
        except Exception:
            logger.exception('Invoice company classification LLM call failed')
            return 'UNKNOWN'


def sanitize_page_with_log(page_num: int, page_text: str) -> str:
    if Config.DEBUG_CLASSIFICATION:
        print(f'[classify] sanitize page {page_num} start')
    cleaned = sanitize_ocr_text(page_text)
    if Config.DEBUG_CLASSIFICATION:
        print(
            f'[classify] sanitize page {page_num} done '
            f'({len(cleaned) if cleaned else 0} chars)',
        )
    return cleaned
