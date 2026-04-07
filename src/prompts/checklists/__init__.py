from pathlib import Path
from typing import Optional, Tuple

import yaml

_PKG_DIR = Path(__file__).resolve().parent

_KIND_BY_PAGE_TYPE: dict[str, str] = {
    'MAWB': 'mawb',
    'HAWB': 'mawb',
    'IATA': 'mawb',
    'INVOICE': 'invoice',
    'CARGO_MANIFEST': 'manifest',
}


def prompt_kind_for_page_type(page_type: Optional[str]) -> Optional[str]:
    if not page_type:
        return None
    return _KIND_BY_PAGE_TYPE.get(page_type.strip().upper())


def _read_yaml(name: str) -> dict:
    path = _PKG_DIR / f'{name}.yaml'
    if not path.is_file():
        return {}
    with open(path, encoding='utf-8') as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def build_checklist_prompts(kind: str) -> Tuple[str, str]:
    """
    Returns (system_prompt, user_prompt_template).
    user_prompt_template must support .format(page_type=..., ocr_text=...).
    """
    base = _read_yaml('base')
    specific = _read_yaml(kind)

    base_system = (base.get('system_prompt') or '').strip()
    type_system = (specific.get('system_prompt') or '').strip()
    if base_system and type_system:
        system = f'{base_system}\n\n{type_system}'
    else:
        system = type_system or base_system

    user_tmpl = (specific.get('user_prompt_template') or '').strip()
    if not user_tmpl:
        user_tmpl = (base.get('user_prompt_template') or '').strip()
    if not user_tmpl:
        user_tmpl = (
            'Page type label: {page_type}\n\nOCR text:\n{ocr_text}'
        )

    return system, user_tmpl


def format_checklist_user_prompt(
    user_prompt_template: str,
    page_type: str,
    ocr_text: str,
    sub_page_type: Optional[str] = None,
) -> str:
    # Avoid str.format: OCR text may contain { or }.
    prompt = (
        user_prompt_template.replace('{page_type}', page_type).replace(
            '{ocr_text}', ocr_text
        )
    )
    
    # Handle sub_page_type context
    if sub_page_type and sub_page_type != 'UNKNOWN':
        sub_context = f'Sub page type (company): {sub_page_type}'
    else:
        sub_context = ''
    
    prompt = prompt.replace('{sub_page_type_context}', sub_context)
    return prompt
