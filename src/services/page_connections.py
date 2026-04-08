"""
Config-driven page connection engine.

Adding a new connection type
----------------------------
Add one dict to CONNECTION_RULES.  The engine never changes.

Rule types
----------
list_overlap   : set(from_doc[from_key]) ∩ set(to_doc[to_key]) ≠ ∅
                 Both sides may be flat lists or lists-of-dicts; use
                 from_array_key / to_array_key to pluck a scalar from objects.

key_match      : from_doc[from_key] == to_doc[to_key]  (scalar equality)

Document types (page_type values)
----------------------------------
  MAWB            → freight_numbers: List[str], awb_number: str
  HAWB            → awb_number: str
  IATA            → awb_number: str
  CARGO_MANIFEST  → hawb_list: List[{hawb_no, pcs, weight_kg, destination}]
  INVOICE         → invoice_no: str
"""

import logging
from pprint import pprint
from typing import Any, Dict, List, Optional, Set

from src.models.schemas import OCRPage, PageConnection

logger = logging.getLogger('shipping_bill_ocr')


# ── Connection rule config ────────────────────────────────────────────────────

CONNECTION_RULES: List[dict] = [

    # ── MAWB → HAWB ───────────────────────────────────────────────────────────
    # The MAWB lists the HAWB numbers it covers in freight_numbers.
    # Each HAWB page's own awb_number must appear in that list.
    # {
    #     "name": "MAWB → HAWB",
    #     "from_type": "MAWB",
    #     "from_key": "freight_numbers",  # List[str] on MAWBCheckList
    #     "from_array_key": None,         # already a flat list
    #     "to_type": "HAWB",
    #     "to_key": "awb_number",         # scalar on each HAWB page
    #     "to_array_key": None,
    #     "type": "list_overlap",
    # },

    # ── MAWB → CARGO_MANIFEST ─────────────────────────────────────────────────
    # A MAWB and a manifest belong to the same shipment when at least one HAWB
    # number on the MAWB also appears in the manifest's hawb_list table.
    {
        "name": "MAWB → CARGO_MANIFEST",
        "from_type": "MAWB",
        "from_key": "freight_numbers",  # List[str] on MAWBCheckList
        "from_array_key": None,
        "to_type": "CARGO_MANIFEST",
        "to_key": "hawb_list",          # List[HawbEntry]
        "to_array_key": "hawb_no",      # pluck HawbEntry.hawb_no
        "type": "list_overlap",
    },

    # ── CARGO_MANIFEST → HAWB ─────────────────────────────────────────────────
    # The manifest lists all house AWB entries in hawb_list.
    # Match each HAWB page's awb_number against those entries.
    {
        "name": "CARGO_MANIFEST → HAWB",
        "from_type": "CARGO_MANIFEST",
        "from_key": "hawb_list",        # List[HawbEntry]
        "from_array_key": "hawb_no",    # pluck HawbEntry.hawb_no
        "to_type": "HAWB",
        "to_key": "awb_number",         # scalar on each HAWB page
        "to_array_key": None,
        "type": "list_overlap",
    },


    # ── Add future rules here — no engine changes needed ──────────────────────
    # Example: link IATA pages to the manifest the same way as MAWB
    # {
    #     "name": "IATA → CARGO_MANIFEST",
    #     "from_type": "IATA",
    #     "from_key": "freight_numbers",
    #     "from_array_key": None,
    #     "to_type": "CARGO_MANIFEST",
    #     "to_key": "hawb_list",
    #     "to_array_key": "hawb_no",
    #     "type": "list_overlap",
    # },
    # Example: link MAWB to IATA by shared AWB number
    {
        "name": "HAWB → IATA",
        "from_type": "HAWB",
        "from_key": "awb_number",
        "from_array_key": None,
        "to_type": "IATA",
        "to_key": "awb_number",
        "to_array_key": None,
        "type": "key_match",
    },
    {
        "name": "INVOICE → INVOICE",
        "from_type": "INVOICE",
        "from_key": "invoice_no",
        "from_array_key": None,
        "to_type": "INVOICE",
        "to_key": "invoice_no",
        "to_array_key": None,
        "type": "key_match",
    },
    {
        "name": "HAWB → INVOICE",
        "from_type": "HAWB",
        "from_key": "awb_number",
        "from_array_key": None,
        "to_type": "INVOICE",
        "to_key": "awb_number",
        "to_array_key": None,
        "type": "key_match",
    },
]


# ── Engine ────────────────────────────────────────────────────────────────────

def _extract_scalars(
    checklist: Dict[str, Any],
    key: str,
    array_key: Optional[str],
) -> Set[str]:
    """
    Resolve *key* on a checklist dict and return a normalised set of strings.

    - Flat list  ["MYN-001", "MYN-002"]          → {"MYN-001", "MYN-002"}
    - Object list [{hawb_no: "MYN-001"}, ...]     → {"MYN-001"} (needs array_key)
    - Scalar     "MYN-001"                        → {"MYN-001"}
    """
    val = checklist.get(key)
    if val is None:
        return set()

    if isinstance(val, list):
        result: Set[str] = set()
        for item in val:
            if isinstance(item, dict):
                if array_key:
                    v = item.get(array_key)
                else:
                    continue        # object but no array_key → skip
            else:
                v = item
            if v is not None:
                s = str(v).strip().upper()
                if s:
                    result.add(s)
        return result

    # Scalar value
    s = str(val).strip().upper()
    return {s} if s else set()


def _pages_by_type(pages: List[OCRPage]) -> Dict[str, List[OCRPage]]:
    mapping: Dict[str, List[OCRPage]] = {}
    for page in pages:
        pt = (page.page_type or '').strip().upper()
        if pt and page.checklist is not None:
            mapping.setdefault(pt, []).append(page)
    return mapping


def _eval_rule(
    rule: dict,
    from_pages: List[OCRPage],
    to_pages: List[OCRPage],
) -> List[PageConnection]:
    """Evaluate one connection rule and return matching PageConnection objects."""
    rule_type = rule['type']
    from_key = rule['from_key']
    from_array_key = rule.get('from_array_key')
    to_key = rule['to_key']
    to_array_key = rule.get('to_array_key')

    connections: List[PageConnection] = []

    for from_page in from_pages:
        from_set = _extract_scalars(
            from_page.checklist, from_key, from_array_key)
        if not from_set:
            continue

        for to_page in to_pages:
            # Skip self-connections and duplicate reverse pairs for same-type rules
            if to_page.paged_idx == from_page.paged_idx:
                continue
            if rule['from_type'].upper() == rule['to_type'].upper() and \
                    to_page.paged_idx < from_page.paged_idx:
                continue

            to_set = _extract_scalars(to_page.checklist, to_key, to_array_key)
            if not to_set:
                continue

            matched = False
            if rule_type == 'list_overlap':
                matched = bool(from_set & to_set)
            elif rule_type == 'key_match':
                matched = from_set == to_set

            if matched:
                logger.debug(
                    'connection rule="%s" from=%d to=%d overlap=%s',
                    rule['name'],
                    from_page.paged_idx,
                    to_page.paged_idx,
                    from_set & to_set if rule_type == 'list_overlap' else from_set,
                )
                connections.append(PageConnection(
                    from_=from_page.paged_idx,
                    to=to_page.paged_idx,
                    confidence=to_page.page_confidence,
                ))

    return connections


def build_page_connections(pages: List[OCRPage]) -> List[PageConnection]:
    """
    Evaluate every rule in CONNECTION_RULES against the provided OCRPage list
    and return all matched connections.

    Never raises — rules that error are skipped with a log warning.
    """
    by_type = _pages_by_type(pages)
    logger.debug(
        'build_page_connections page_types=%s',
        {pt: len(pgs) for pt, pgs in by_type.items()},
    )

    all_connections: List[PageConnection] = []

    pprint(CONNECTION_RULES)

    for rule in CONNECTION_RULES:
        from_type = rule['from_type'].upper()
        to_type = rule['to_type'].upper()

        from_pages = by_type.get(from_type, [])
        to_pages = by_type.get(to_type, [])

        if not from_pages or not to_pages:
            logger.debug(
                'connection rule="%s" skipped (from=%s×%d to=%s×%d)',
                rule['name'], from_type, len(
                    from_pages), to_type, len(to_pages),
            )
            continue

        try:
            rule_connections = _eval_rule(rule, from_pages, to_pages)
        except Exception:
            logger.exception('connection rule "%s" failed', rule['name'])
            continue

        logger.debug(
            'connection rule="%s" found %d connection(s)',
            rule['name'], len(rule_connections),
        )
        all_connections.extend(rule_connections)

    return all_connections
