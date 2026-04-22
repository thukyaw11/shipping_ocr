import logging

from src.models.schemas import OCRPage
from src.services.cross_validation import run_cross_validation
from src.services.page_connections import build_page_connections
from src.utils import to_local_time

logger = logging.getLogger('shipping_bill_ocr')


def enrich_confidence_fields(doc: dict) -> dict:
    pages = doc.get("data") or []
    total_sum = 0.0
    total_count = 0
    for page in pages:
        lines = page.get("text_lines") or []
        page_sum = 0.0
        page_count = 0
        for line in lines:
            conf = line.get("confidence")
            if isinstance(conf, (int, float)):
                page_sum += float(conf)
                page_count += 1
                total_sum += float(conf)
                total_count += 1
        if page.get("page_confidence") is None and page_count > 0:
            page["page_confidence"] = round(page_sum / page_count, 6)
    if doc.get("overall_confidence") is None and total_count > 0:
        doc["overall_confidence"] = round(total_sum / total_count, 6)
    return doc


def attach_checklists(doc: dict) -> dict:
    pages = doc.get('data') or []
    doc['checklists'] = [
        p.get('checklist') if isinstance(p, dict) else None for p in pages
    ]
    return doc


def attach_connections(doc: dict) -> dict:
    pages_raw = doc.get('data') or []
    try:
        pages = [OCRPage(**p) for p in pages_raw if isinstance(p, dict)]
        results = build_page_connections(pages)
        doc['connections'] = [r.model_dump(by_alias=True) for r in results] or None
    except Exception:
        logger.exception('Page connection building failed')
        doc['connections'] = None
    return doc


def attach_cross_validation(doc: dict) -> dict:
    pages_raw = doc.get('data') or []
    try:
        pages = [OCRPage(**p) for p in pages_raw if isinstance(p, dict)]
        results = run_cross_validation(pages)
        doc['cross_validation_results'] = [r.model_dump() for r in results]
    except Exception:
        logger.exception('Cross-validation failed while building project detail')
        doc['cross_validation_results'] = []
    return doc


def enrich_ocr_result(doc: dict, user_tz: str) -> dict:
    """Apply all enrichment steps and localise timestamps on a single ocr_result dict."""
    doc = enrich_confidence_fields(doc)
    attach_checklists(doc)
    attach_connections(doc)
    attach_cross_validation(doc)
    dt_created = to_local_time(doc.get("created_at"), user_tz)
    dt_edited = to_local_time(doc.get("edited_at"), user_tz)
    if dt_created:
        doc["created_at"] = dt_created.isoformat()
    if dt_edited:
        doc["edited_at"] = dt_edited.isoformat()
    return doc
