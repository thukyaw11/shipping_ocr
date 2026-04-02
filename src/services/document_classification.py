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

_LABEL_ORDER: tuple[str, ...] = (
    'MAWB', 'HAWB', 'IATA', 'INVOICE', 'CARGO_MANIFEST', 'UNKNOWN',
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


class DocumentTypeClassificationOutput(BaseModel):
    """JSON schema for Gemini structured classification responses."""

    model_config = ConfigDict(extra='forbid')
    document_type: Literal['MAWB', 'HAWB', 'IATA', 'INVOICE', 'CARGO_MANIFEST', 'UNKNOWN'] = Field(
        description='The single best classification label for the logistics/air-cargo document.',
    )


def sanitize_ocr_text(ocr_text: str, max_chars: int = 12000) -> str:
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


def normalize_classification_label(value: str) -> str:
    u = (value or '').strip().upper()
    if u in ALLOWED_DOCUMENT_TYPES:
        return u
    return parse_label_from_model_output(u)


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
