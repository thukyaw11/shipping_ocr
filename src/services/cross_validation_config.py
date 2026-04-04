"""
Cross-validation rule configuration.

Rule types
----------
match           : value_a == value_b  (single doc vs single doc)
sum_match       : sum(value_a across all pages of doc_a) == value_b  (many docs → one doc)
array_sum_match : sum(item[array_key] for item in array_a) == value_b  (array inside a doc)
list_match      : set(list_a) == set(list_b)  (two lists contain identical elements)
                  Either list may be flat (["a","b"]) or a list of objects; use
                  array_key_a / array_key_b to pluck a scalar from each object.

Key format
----------
Keys follow dot-notation relative to an OCRPage, e.g.:
  "checklist.total_weight"    →  page["checklist"]["total_weight"]
  "checklist.hawb_list"       →  page["checklist"]["hawb_list"]   (a list)
  "checklist.freight_numbers" →  page["checklist"]["freight_numbers"]  (a flat list)

Document types
--------------
  MAWB            → MAWBCheckList
                     (awb_number, shipper_name, consignee_name, total_weight,
                      freight_preaid: List[int], freight_numbers: List[str])
  HAWB            → MAWBCheckList   (same model as MAWB)
  IATA            → MAWBCheckList   (same model as MAWB)
  INVOICE         → InvoiceChecklist (invoice_no, date, total_amount)
  CARGO_MANIFEST  → ManifestChecklist
                     (flight_no, flight_date, origin, destination,
                      total_pcs, total_weight,
                      hawb_list: List[HawbEntry{hawb_no, pcs, weight_kg, destination}])
"""

from typing import Literal, Optional, TypedDict, Union


# ── Rule schemas ──────────────────────────────────────────────────────────────

class ListMatchRule(TypedDict):
    """
    Compare two lists as sets — every element in list_a must appear in list_b
    and vice versa (set equality).

    Either list may be:
      - A flat list of scalars: ["MYN-001", "MYN-002"]
      - A list of objects: use array_key_a / array_key_b to extract the scalar
        from each element before comparison.

    Values are normalised to stripped, upper-cased strings before comparison so
    minor OCR capitalisation differences do not cause false failures.
    """
    name: str
    category: str
    doc_a: str                      # page_type that owns list_a
    key_a: str                      # dot-path to the list on doc_a
    array_key_a: Optional[str]      # pluck this key from each element (if objects)
    doc_b: str                      # page_type that owns list_b
    key_b: str                      # dot-path to the list on doc_b
    array_key_b: Optional[str]      # pluck this key from each element (if objects)
    type: Literal["list_match"]


class MatchRule(TypedDict):
    """Direct field equality between two single documents."""
    name: str
    category: str
    doc_a: str          # page_type of the first document
    key_a: str          # dot-separated path inside the page
    doc_b: str          # page_type of the second document
    key_b: str
    type: Literal["match"]
    tolerance: Optional[float]   # absolute tolerance for numeric comparisons


class SumMatchRule(TypedDict):
    """
    Sum a field across *all* pages of doc_a and compare to a single value
    from doc_b.  Used when multiple HAWB pages must collectively equal the
    master MAWB / Manifest total.
    """
    name: str
    category: str
    doc_a: str          # page_type to collect and aggregate (may appear N times)
    key_a: str          # dot-path to the numeric field on each collected page
    doc_b: str          # page_type that holds the expected total (appears once)
    key_b: str
    type: Literal["sum_match"]
    tolerance: Optional[float]


class ArraySumMatchRule(TypedDict):
    """
    Sum a field inside an array that lives on a single page of doc_a and
    compare to another field (possibly on the same or a different page).
    """
    name: str
    category: str
    doc_a: str          # page_type that owns the array
    key_a: str          # dot-path to the array field
    array_key: str      # field name inside each array element to sum
    doc_b: str          # page_type that holds the expected total
    key_b: str
    type: Literal["array_sum_match"]
    tolerance: Optional[float]


CrossValidationRule = Union[MatchRule, SumMatchRule, ArraySumMatchRule, ListMatchRule]


# ── Category constants ────────────────────────────────────────────────────────

CAT_MAWB_MANIFEST  = "MAWB vs Manifest"
CAT_MAWB_HAWB      = "MAWB vs HAWB"
CAT_IATA_MANIFEST  = "IATA vs Manifest"
CAT_HAWB_MANIFEST  = "HAWB vs Manifest"
CAT_HAWB_MAWB      = "HAWB vs MAWB"
CAT_MANIFEST_INTERNAL = "Manifest: Internal"


# ── System configuration ──────────────────────────────────────────────────────

SYSTEM_CONFIG: dict = {
    "CROSS_VALIDATION_RULES": [

        # ── MAWB vs Manifest ──────────────────────────────────────────────────

        {
            "name": "MAWB vs Manifest: Total Weight",
            "category": CAT_MAWB_MANIFEST,
            "doc_a": "MAWB",
            "key_a": "checklist.total_weight",
            "doc_b": "CARGO_MANIFEST",
            "key_b": "checklist.total_weight",
            "type": "match",
            "tolerance": 0.5,
        },
        {
            "name": "MAWB vs Manifest: Freight Numbers",
            "category": CAT_MAWB_MANIFEST,
            "doc_a": "MAWB",
            "key_a": "checklist.freight_numbers",   # List[str]
            "array_key_a": None,                    # flat list of strings
            "doc_b": "CARGO_MANIFEST",
            "key_b": "checklist.hawb_list",         # List[HawbEntry]
            "array_key_b": "hawb_no",
            "type": "list_match",
        },

        # ── MAWB vs HAWB ──────────────────────────────────────────────────────

        {
            "name": "MAWB vs HAWB: Total Weight",
            "category": CAT_MAWB_HAWB,
            "doc_a": "MAWB",
            "key_a": "checklist.total_weight",
            "doc_b": "HAWB",
            "key_b": "checklist.total_weight",
            "type": "match",
            "tolerance": 0.5,
        },

        # ── IATA vs Manifest ──────────────────────────────────────────────────

        {
            "name": "IATA vs Manifest: Total Weight",
            "category": CAT_IATA_MANIFEST,
            "doc_a": "IATA",
            "key_a": "checklist.total_weight",
            "doc_b": "CARGO_MANIFEST",
            "key_b": "checklist.total_weight",
            "type": "match",
            "tolerance": 0.5,
        },

        # ── HAWB vs Manifest ──────────────────────────────────────────────────

        {
            "name": "HAWB vs Manifest: Total Weight",
            "category": CAT_HAWB_MANIFEST,
            "doc_a": "HAWB",
            "key_a": "checklist.total_weight",
            "doc_b": "CARGO_MANIFEST",
            "key_b": "checklist.total_weight",
            "type": "sum_match",
            "tolerance": 0.5,
        },

        # ── HAWB vs MAWB ──────────────────────────────────────────────────────

        {
            "name": "HAWB vs MAWB: Total Weight",
            "category": CAT_HAWB_MAWB,
            "doc_a": "HAWB",
            "key_a": "checklist.total_weight",
            "doc_b": "MAWB",
            "key_b": "checklist.total_weight",
            "type": "sum_match",
            "tolerance": 0.5,
        },

        # ── Manifest: Internal ────────────────────────────────────────────────

        {
            "name": "Manifest: HAWB List Weight Sum vs Total Weight",
            "category": CAT_MANIFEST_INTERNAL,
            "doc_a": "CARGO_MANIFEST",
            "key_a": "checklist.hawb_list",
            "array_key": "weight_kg",
            "doc_b": "CARGO_MANIFEST",
            "key_b": "checklist.total_weight",
            "type": "array_sum_match",
            "tolerance": 0.5,
        },
        {
            "name": "Manifest: HAWB List Pieces Sum vs Total Pieces",
            "category": CAT_MANIFEST_INTERNAL,
            "doc_a": "CARGO_MANIFEST",
            "key_a": "checklist.hawb_list",
            "array_key": "pcs",
            "doc_b": "CARGO_MANIFEST",
            "key_b": "checklist.total_pcs",
            "type": "array_sum_match",
            "tolerance": 0,
        },

        # ── PENDING / FUTURE RULES ────────────────────────────────────────────
        # The rules below require the InvoiceChecklist model to be extended
        # with an `items: List[InvoiceItem]` field before they can be activated.
        #
        # {
        #     "name": "Invoice: Items Sum vs Total Amount",
        #     "category": "Invoice: Internal",
        #     "doc_a": "INVOICE",
        #     "key_a": "checklist.items",
        #     "array_key": "price",
        #     "doc_b": "INVOICE",
        #     "key_b": "checklist.total_amount",
        #     "type": "array_sum_match",
        #     "tolerance": 0.01,
        # },
    ],
}
