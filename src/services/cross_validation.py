"""
Cross-validation rule engine.

Entry point
-----------
    from src.services.cross_validation import run_cross_validation

    results = run_cross_validation(ocr_document.data)   # data is List[OCRPage]

Each rule in SYSTEM_CONFIG["CROSS_VALIDATION_RULES"] produces one
ValidationResult with status "pass" | "fail" | "skipped".

"skipped" means the required data was absent in the uploaded documents;
it is not treated as an error.

Pagination handling
-------------------
A single logical document (e.g. a CARGO_MANIFEST) may span several physical
pages.  Before evaluating any rule, all pages of the same type are **merged**
into one virtual document:

  • Scalar fields  → first non-null value wins   (totals appear once per doc)
  • List fields    → concatenate across all pages (hawb_list, freight_numbers…)

The only exception is sum_match, which intentionally iterates individual pages
(each HAWB page carries its own weight to be summed).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.models.schemas import OCRPage, ValidationResult
from src.services.cross_validation_config import SYSTEM_CONFIG

logger = logging.getLogger('shipping_bill_ocr')


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _resolve_key(page: OCRPage, dotted_key: str) -> Optional[Any]:
    """Walk a dot-separated path through a serialised OCRPage dict."""
    obj: Any = page.model_dump(mode='json')
    for part in dotted_key.split('.'):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
        if obj is None:
            return None
    return obj


def _resolve_dict(data: dict, dotted_key: str) -> Optional[Any]:
    """Walk a dot-separated path through a plain dict (used on merged docs)."""
    obj: Any = data
    for part in dotted_key.split('.'):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
        if obj is None:
            return None
    return obj


def _numeric_compare(actual: float, expected: float, tolerance: float) -> bool:
    return abs(actual - expected) <= max(tolerance, 0.0)


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ── Pagination merge ──────────────────────────────────────────────────────────

def _merge_pages(pages: List[OCRPage]) -> dict:
    """
    Merge multiple OCRPages of the same document type into one virtual dict.

    Merge strategy
    ──────────────
    • Scalar fields  → first non-null value wins
                       (summary totals appear once; we grab them wherever they are)
    • List fields    → concatenated in page order
                       (paginated tables like hawb_list, freight_numbers grow
                        across pages and must be combined)

    The returned structure mirrors a serialised OCRPage so _resolve_dict can
    navigate it with the same dot-notation keys used in the rules.
    """
    merged_checklist: dict = {}
    for page in pages:
        cl = page.checklist or {}
        for key, value in cl.items():
            existing = merged_checklist.get(key)
            if existing is None:
                # First time we see this key — take the value regardless of type
                merged_checklist[key] = value
            elif isinstance(existing, list) and isinstance(value, list):
                # Extend an already-seen list with this page's continuation
                merged_checklist[key] = existing + value
            # Scalar already populated → keep the first non-null value
    return {'checklist': merged_checklist}


def _pages_by_type(pages: List[OCRPage]) -> Dict[str, List[OCRPage]]:
    """Return {PAGE_TYPE: [OCRPage, ...]} with upper-cased keys."""
    mapping: Dict[str, List[OCRPage]] = {}
    for page in pages:
        pt = (page.page_type or '').strip().upper()
        if pt:
            mapping.setdefault(pt, []).append(page)
    return mapping


def _merged_by_type(by_type: Dict[str, List[OCRPage]]) -> Dict[str, dict]:
    """Map each page_type to its merged virtual document."""
    return {pt: _merge_pages(page_list) for pt, page_list in by_type.items()}


# ── Rule evaluators ───────────────────────────────────────────────────────────

def _eval_match(
    rule: dict,
    by_type: Dict[str, List[OCRPage]],
    merged: Dict[str, dict],
) -> ValidationResult:
    """
    Direct comparison: merged(doc_a)[key_a] == merged(doc_b)[key_b].
    Works for both numeric (with tolerance) and string fields.
    Automatically benefits from page merging — e.g. total_weight that lives
    on the last page of a multi-page manifest is now visible.
    """
    doc_a, key_a = rule['doc_a'].upper(), rule['key_a']
    doc_b, key_b = rule['doc_b'].upper(), rule['key_b']
    tolerance = float(rule.get('tolerance') or 0.0)
    name = rule['name']

    if doc_a not in merged:
        return ValidationResult(rule_name=name, status='skipped',
                                message=f'No {doc_a} page found in document')
    if doc_b not in merged:
        return ValidationResult(rule_name=name, status='skipped',
                                message=f'No {doc_b} page found in document')

    val_a = _resolve_dict(merged[doc_a], key_a)
    val_b = _resolve_dict(merged[doc_b], key_b)

    if val_a is None:
        return ValidationResult(rule_name=name, status='skipped',
                                message=f'{doc_a}: "{key_a}" is missing or null')
    if val_b is None:
        return ValidationResult(rule_name=name, status='skipped',
                                message=f'{doc_b}: "{key_b}" is missing or null')

    num_a, num_b = _to_float(val_a), _to_float(val_b)
    if num_a is not None and num_b is not None:
        passed = _numeric_compare(num_a, num_b, tolerance)
        diff = abs(num_a - num_b)
        msg = '' if passed else (
            f'Difference {diff:.4f} exceeds tolerance {tolerance} '
            f'(actual={num_a}, expected={num_b})'
        )
        return ValidationResult(rule_name=name,
                                status='pass' if passed else 'fail',
                                expected=num_b, actual=num_a, message=msg)

    # String comparison
    passed = str(val_a).strip() == str(val_b).strip()
    msg = '' if passed else f"Expected '{val_b}', got '{val_a}'"
    return ValidationResult(rule_name=name,
                            status='pass' if passed else 'fail',
                            expected=val_b, actual=val_a, message=msg)


def _eval_sum_match(
    rule: dict,
    by_type: Dict[str, List[OCRPage]],
    merged: Dict[str, dict],
) -> ValidationResult:
    """
    Sum a numeric field across *every individual page* of doc_a, then compare
    to the merged value of doc_b.

    Intentionally uses individual pages for doc_a (each HAWB page contributes
    its own weight), but uses the merged view for doc_b (so a manifest total
    that only appears on the last page is still found).
    """
    doc_a, key_a = rule['doc_a'].upper(), rule['key_a']
    doc_b, key_b = rule['doc_b'].upper(), rule['key_b']
    tolerance = float(rule.get('tolerance') or 0.0)
    name = rule['name']

    pages_a = by_type.get(doc_a, [])
    if not pages_a:
        return ValidationResult(rule_name=name, status='skipped',
                                message=f'No {doc_a} pages found in document')
    if doc_b not in merged:
        return ValidationResult(rule_name=name, status='skipped',
                                message=f'No {doc_b} page found in document')

    total = 0.0
    missing = 0
    for page in pages_a:
        num = _to_float(_resolve_key(page, key_a))
        if num is None:
            missing += 1
        else:
            total += num

    if missing == len(pages_a):
        return ValidationResult(rule_name=name, status='skipped',
                                message=f'All {len(pages_a)} {doc_a} page(s) '
                                        f'are missing "{key_a}"')

    expected = _to_float(_resolve_dict(merged[doc_b], key_b))
    if expected is None:
        return ValidationResult(rule_name=name, status='skipped',
                                message=f'{doc_b}: "{key_b}" is missing or not numeric')

    passed = _numeric_compare(total, expected, tolerance)
    parts: List[str] = []
    if not passed:
        diff = abs(total - expected)
        parts.append(
            f'Sum of {len(pages_a) - missing} {doc_a} page(s) = {total:.4f}, '
            f'expected {expected:.4f} (diff {diff:.4f})'
        )
    if missing:
        parts.append(f'{missing} page(s) skipped (missing "{key_a}")')

    return ValidationResult(rule_name=name,
                            status='pass' if passed else 'fail',
                            expected=expected, actual=total,
                            message='  '.join(parts))


def _eval_array_sum_match(
    rule: dict,
    by_type: Dict[str, List[OCRPage]],
    merged: Dict[str, dict],
) -> ValidationResult:
    """
    Sum a field inside a nested list on doc_a, compare to a scalar on doc_b.

    Uses merged doc_a so that a paginated hawb_list (split across N pages) is
    fully combined before summing.  E.g. for a 2-page manifest:
      page 1: hawb_list = [item1, item2, item3]
      page 2: hawb_list = [item4, item5]
      merged: hawb_list = [item1…item5]  ← correct total used for sum
    """
    doc_a, key_a = rule['doc_a'].upper(), rule['key_a']
    array_key: str = rule['array_key']
    doc_b, key_b = rule['doc_b'].upper(), rule['key_b']
    tolerance = float(rule.get('tolerance') or 0.0)
    name = rule['name']

    if doc_a not in merged:
        return ValidationResult(rule_name=name, status='skipped',
                                message=f'No {doc_a} page found in document')
    if doc_b not in merged:
        return ValidationResult(rule_name=name, status='skipped',
                                message=f'No {doc_b} page found in document')

    array = _resolve_dict(merged[doc_a], key_a)
    if not isinstance(array, list):
        return ValidationResult(rule_name=name, status='skipped',
                                message=f'{doc_a}: "{key_a}" is not a list or is missing')
    if not array:
        return ValidationResult(rule_name=name, status='skipped',
                                message=f'{doc_a}: "{key_a}" is an empty list')

    total = 0.0
    missing = 0
    for item in array:
        if not isinstance(item, dict):
            missing += 1
            continue
        num = _to_float(item.get(array_key))
        if num is None:
            missing += 1
        else:
            total += num

    if missing == len(array):
        return ValidationResult(rule_name=name, status='skipped',
                                message=f'All {len(array)} item(s) in '
                                        f'"{key_a}" are missing "{array_key}"')

    expected = _to_float(_resolve_dict(merged[doc_b], key_b))
    if expected is None:
        return ValidationResult(rule_name=name, status='skipped',
                                message=f'{doc_b}: "{key_b}" is missing or not numeric')

    passed = _numeric_compare(total, expected, tolerance)
    parts: List[str] = []
    if not passed:
        diff = abs(total - expected)
        parts.append(
            f'Sum of {len(array) - missing} item(s)["{array_key}"] = {total:.4f}, '
            f'expected {expected:.4f} (diff {diff:.4f})'
        )
    if missing:
        parts.append(f'{missing} item(s) skipped (missing "{array_key}")')

    return ValidationResult(rule_name=name,
                            status='pass' if passed else 'fail',
                            expected=expected, actual=total,
                            message='  '.join(parts))


def _extract_list_from_dict(
    data: dict,
    key: str,
    array_key: Optional[str],
) -> Optional[List[str]]:
    """
    Resolve *key* on a plain dict and return normalised upper-cased strings.
    If elements are dicts, pluck *array_key* from each before stringifying.
    """
    raw = _resolve_dict(data, key)
    if not isinstance(raw, list):
        return None
    result: List[str] = []
    for item in raw:
        if isinstance(item, dict):
            if array_key is None:
                continue
            val = item.get(array_key)
        else:
            val = item
        if val is not None:
            result.append(str(val).strip().upper())
    return result


def _eval_list_match(
    rule: dict,
    by_type: Dict[str, List[OCRPage]],
    merged: Dict[str, dict],
) -> ValidationResult:
    """
    Set equality: elements of list_a == elements of list_b (order-insensitive,
    case-insensitive).

    Uses merged docs so that a freight_numbers list split across two MAWB pages,
    or a hawb_list split across two manifest pages, is fully assembled first.
    The message reports exactly which items are only on one side.
    """
    doc_a, key_a = rule['doc_a'].upper(), rule['key_a']
    array_key_a: Optional[str] = rule.get('array_key_a')
    doc_b, key_b = rule['doc_b'].upper(), rule['key_b']
    array_key_b: Optional[str] = rule.get('array_key_b')
    name = rule['name']

    if doc_a not in merged:
        return ValidationResult(rule_name=name, status='skipped',
                                message=f'No {doc_a} page found in document')
    if doc_b not in merged:
        return ValidationResult(rule_name=name, status='skipped',
                                message=f'No {doc_b} page found in document')

    list_a = _extract_list_from_dict(merged[doc_a], key_a, array_key_a)
    list_b = _extract_list_from_dict(merged[doc_b], key_b, array_key_b)

    if list_a is None:
        return ValidationResult(rule_name=name, status='skipped',
                                message=f'{doc_a}: "{key_a}" is missing or not a list')
    if list_b is None:
        return ValidationResult(rule_name=name, status='skipped',
                                message=f'{doc_b}: "{key_b}" is missing or not a list')
    if not list_a and not list_b:
        return ValidationResult(rule_name=name, status='skipped',
                                message='Both lists are empty — nothing to compare')
    if not list_a:
        return ValidationResult(rule_name=name, status='skipped',
                                message=f'{doc_a}: "{key_a}" is empty')
    if not list_b:
        return ValidationResult(rule_name=name, status='skipped',
                                message=f'{doc_b}: "{key_b}" is empty')

    set_a, set_b = set(list_a), set(list_b)
    only_in_a = sorted(set_a - set_b)
    only_in_b = sorted(set_b - set_a)

    passed = not only_in_a and not only_in_b
    parts: List[str] = []
    if only_in_a:
        parts.append(f'In {doc_a} only: {only_in_a}')
    if only_in_b:
        parts.append(f'In {doc_b} only: {only_in_b}')

    return ValidationResult(
        rule_name=name,
        status='pass' if passed else 'fail',
        expected=sorted(set_b),
        actual=sorted(set_a),
        message='  '.join(parts),
    )


# ── Public API ────────────────────────────────────────────────────────────────

_EVALUATORS = {
    'match': _eval_match,
    'sum_match': _eval_sum_match,
    'array_sum_match': _eval_array_sum_match,
    'list_match': _eval_list_match,
}


def run_cross_validation(pages: List[OCRPage]) -> List[ValidationResult]:
    """
    Evaluate every rule in SYSTEM_CONFIG["CROSS_VALIDATION_RULES"] against
    the given list of OCRPage objects.

    Returns one ValidationResult per rule, in the same order as the config.
    Never raises; unknown rule types and runtime errors produce a "skipped"
    result so that the rest of the pipeline continues unaffected.
    """
    rules: List[dict] = SYSTEM_CONFIG.get('CROSS_VALIDATION_RULES', [])

    by_type = _pages_by_type(pages)
    merged = _merged_by_type(by_type)

    page_counts = {pt: len(pgs) for pt, pgs in by_type.items()}
    multi_page = [f'{pt}×{n}' for pt, n in page_counts.items() if n > 1]
    if multi_page:
        logger.debug('cross_validation multi-page docs merged: %s', multi_page)

    results: List[ValidationResult] = []
    for rule in rules:
        rule_name = rule.get('name', 'unnamed')
        rule_type = rule.get('type', '')
        category = rule.get('category') or None
        evaluator = _EVALUATORS.get(rule_type)

        if evaluator is None:
            result = ValidationResult(
                category=category, rule_name=rule_name, status='skipped',
                message=f'Unknown rule type: "{rule_type}"',
            )
        else:
            try:
                result = evaluator(rule, by_type, merged)
                result = result.model_copy(update={'category': category})
            except Exception:
                logger.exception('Cross-validation error in rule "%s"', rule_name)
                result = ValidationResult(
                    category=category, rule_name=rule_name, status='skipped',
                    message='Internal error while evaluating rule',
                )

        logger.debug(
            'cross_validation rule="%s" category="%s" status=%s actual=%s expected=%s msg=%s',
            result.rule_name, result.category, result.status,
            result.actual, result.expected, result.message,
        )
        results.append(result)

    return results
