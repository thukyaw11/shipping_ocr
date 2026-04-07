import logging
from typing import Any, Optional, Type

from pydantic import BaseModel

from src.models.checklists import (
    InvoiceChecklist,
    MAWBCheckList,
    ManifestChecklist,
)
from src.prompts.checklists import (
    build_checklist_prompts,
    format_checklist_user_prompt,
    prompt_kind_for_page_type,
)
from src.services.ai.gemini_provider import GeminiTextProvider

logger = logging.getLogger('shipping_bill_ocr')

PAGE_TYPE_TO_CHECKLIST_MODEL: dict[str, Type[BaseModel]] = {
    'MAWB': MAWBCheckList,
    'HAWB': MAWBCheckList,
    'IATA': MAWBCheckList,
    'INVOICE': InvoiceChecklist,
    'CARGO_MANIFEST': ManifestChecklist,
}


def checklist_model_for_page_type(page_type: Optional[str]) -> Optional[Type[BaseModel]]:
    if not page_type:
        return None
    key = page_type.strip().upper()
    return PAGE_TYPE_TO_CHECKLIST_MODEL.get(key)


def extract_checklist_sync(
    gemini: GeminiTextProvider,
    page_type: str,
    ocr_text: str,
    max_ocr_chars: int = 14000,
    sub_page_type: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    model_cls = checklist_model_for_page_type(page_type)
    if model_cls is None:
        return None
    text = (ocr_text or '').strip()
    if not text:
        return None
    if len(text) > max_ocr_chars:
        text = text[:max_ocr_chars]
    pt = page_type.strip().upper()
    kind = prompt_kind_for_page_type(pt)
    if not kind:
        return None
    system_prompt, user_tmpl = build_checklist_prompts(kind)
    user_prompt = format_checklist_user_prompt(user_tmpl, pt, text, sub_page_type)
    try:
        parsed = gemini.generate_structured_json(
            system_prompt,
            user_prompt,
            model_cls,
        )
        return parsed.model_dump(mode='json')
    except Exception:
        logger.exception(
            'Checklist extraction failed page_type=%s',
            page_type,
        )
        return None
