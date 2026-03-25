import json
import os
import re
from datetime import datetime
from typing import Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict
from PIL import Image
from surya.detection import DetectionPredictor
from surya.recognition import RecognitionPredictor
from surya.foundation import FoundationPredictor
import ollama

from src.utils.spinner import LoadingSpinner

# ── Pydantic models ──────────────────────────────────────────────────────────

_cfg = ConfigDict(extra="forbid")

class DocumentInfo(BaseModel):
    model_config = _cfg
    awb_number: Optional[str] = None
    airline_prefix: Optional[str] = None
    serial_number: Optional[str] = None
    document_status: Optional[str] = None
    copy_name: Optional[str] = None

class EntityInfo(BaseModel):
    model_config = _cfg
    name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    fax: Optional[str] = None
    account_number: Optional[str] = None

class IssuingAgent(BaseModel):
    model_config = _cfg
    name: Optional[str] = None
    city: Optional[str] = None
    iata_code: Optional[str] = None

class Parties(BaseModel):
    model_config = _cfg
    shipper: EntityInfo
    consignee: EntityInfo
    issuing_agent: IssuingAgent

class RoutingStep(BaseModel):
    model_config = _cfg
    to: Optional[str] = None
    by_carrier: Optional[str] = None
    flight_number: Optional[str] = None
    date: Optional[str] = None

class RoutingAndDestination(BaseModel):
    model_config = _cfg
    departure_airport: Optional[str] = None
    destination_airport: Optional[str] = None
    routing: List[RoutingStep]

class Declaration(BaseModel):
    model_config = _cfg
    currency: Optional[str] = None
    charge_code: Optional[str] = None
    weight_valuation_charge: Optional[str] = None
    other_charges: Optional[str] = None
    declared_value_for_carriage: Optional[str] = None
    declared_value_for_customs: Optional[str] = None

class CargoDetails(BaseModel):
    model_config = _cfg
    pieces: Optional[int] = None
    gross_weight: Optional[float] = None
    weight_unit: Optional[str] = None
    rate_class: Optional[str] = None
    chargeable_weight: Optional[float] = None
    rate_charge: Optional[Union[float, str]] = None
    total_weight_charge: Optional[Union[float, str]] = None
    nature_and_quantity_of_goods: Optional[str] = None
    total_volume_mc: Optional[float] = None
    dimensions: List[str]

class HandlingInformation(BaseModel):
    model_config = _cfg
    special_notes: Optional[str] = None
    instruction: Optional[str] = None
    eap: Optional[str] = None
    rcar: Optional[str] = None

class TotalPrepaidSummary(BaseModel):
    model_config = _cfg
    weight_charge: Optional[Union[float, str]] = None
    total_other_charges_due_agent: Optional[Union[float, str]] = None
    grand_total: Optional[Union[float, str]] = None

class AccountingAndCharges(BaseModel):
    model_config = _cfg
    freight_prepaid: List[str]
    other_charges_breakdown: Dict[str, Optional[float]]
    total_prepaid_summary: TotalPrepaidSummary

class Execution(BaseModel):
    model_config = _cfg
    shipper_signature_authority: Optional[str] = None
    execution_date: Optional[str] = None
    execution_place: Optional[str] = None
    carrier_signature_code: Optional[str] = None

class ExtractedInfo(BaseModel):
    model_config = _cfg
    document_info: DocumentInfo
    parties: Parties
    routing_and_destination: RoutingAndDestination
    declaration: Declaration
    cargo_details: CargoDetails
    handling_information: HandlingInformation
    accounting_and_charges: AccountingAndCharges
    execution: Execution

# ── Spinner ──────────────────────────────────────────────────────────────────

# ── Model setup (done once) ──────────────────────────────────────────────────

det_predictor = DetectionPredictor()
rec_predictor = RecognitionPredictor(FoundationPredictor())

# ── Spatial layout reconstruction ────────────────────────────────────────────

# Matches fragmented dimension rows like "31  |  X  |  31  |  X  |  55  |  CM"
# (surya splits each cell into its own bbox) and collapses them to "31 X 31 X 55 CM"
_DIM_RE = re.compile(
    r"(\d+)\s*\|\s*([Xx×Χ])\s*\|\s*(\d+)\s*\|\s*([Xx×Χ])\s*\|\s*(\d+)\s*\|\s*(CM|IN|cm|in)",
    re.IGNORECASE,
)

def _collapse_dimension(text: str) -> str:
    """Collapse a fragmented dimension row into a clean 'W X H X D CM' string."""
    m = _DIM_RE.search(text)
    if not m:
        return text
    w, _, h, _, d, unit = m.groups()
    collapsed = f"{w} X {h} X {d} {unit.upper()}"
    # Keep anything else on the row (e.g. piece count at the end)
    rest = text[m.end():].replace("|", "").strip()
    return f"{collapsed}  {rest}".strip() if rest else collapsed


def build_layout_text(text_lines, row_tolerance: int = 12) -> str:
    """
    Sort lines by their bounding box position and group lines that share the
    same visual row (similar y1).  Within each row, lines are ordered left →
    right by x1, then joined with '  |  ' so the LLM can see side-by-side
    label/value pairs as they appear on the form.
    """
    # Sort top → bottom, then left → right
    sorted_lines = sorted(text_lines, key=lambda l: (l.bbox[1], l.bbox[0]))

    rows: list[list] = []
    for line in sorted_lines:
        placed = False
        for row in rows:
            # If this line's top (y1) is within tolerance of the row's avg y1
            row_y = sum(l.bbox[1] for l in row) / len(row)
            if abs(line.bbox[1] - row_y) <= row_tolerance:
                row.append(line)
                placed = True
                break
        if not placed:
            rows.append([line])

    output_lines = []
    for row in rows:
        row.sort(key=lambda l: l.bbox[0])          # left → right within row
        joined = "  |  ".join(l.text for l in row)
        output_lines.append(_collapse_dimension(joined))

    return "\n".join(output_lines)


# ── Main pipeline ────────────────────────────────────────────────────────────

def process_file_to_json(file_path: str):
    os.makedirs("outputs2", exist_ok=True)

    if not os.path.exists(file_path):
        print(f"ERROR: File not found: {file_path}")
        return

    # 1. OCR
    spinner = LoadingSpinner(f"OCR on {os.path.basename(file_path)}")
    spinner.start()
    image = Image.open(file_path).convert("RGB")
    predictions = rec_predictor([image], det_predictor=det_predictor)
    spinner.stop()

    ocr_text = build_layout_text(predictions[0].text_lines)
    print("\n--- SURYA OCR TEXT ---")
    print(ocr_text)
    print("----------------------\n")

    if not ocr_text.strip():
        print("WARNING: No text detected.")
        return

    # 2. Ollama structured extraction
    # model_name = "qwen3:8b"
    model_name = "llama3.2-vision"
    spinner = LoadingSpinner("Ollama extracting structured data")
    spinner.start()
    response = ollama.chat(
        model=model_name,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a specialized logistics data extractor for Air Waybills (AWB). "
                    "Extract all details accurately into the requested JSON format. "
                    'If numeric fields contain "AS ARRANGED", use that string instead of a number. '
                    'For "freight_prepaid", extract all account numbers into a clean array of strings. '
                    "On an AWB form the Currency field (e.g. THB, SGD, USD) appears on the same row "
                    "as or immediately below the routing (To/By Carrier) section — extract it into "
                    "declaration.currency as a 3-letter ISO code."
                ),
            },
            {
                "role": "user",
                "content": f"Extract all detailed logistics data from this OCR text:\n\n{ocr_text}",
            },
        ],
        format=ExtractedInfo.model_json_schema(),
    )
    spinner.stop()

    # 3. Save JSON
    json_content = json.loads(response.message.content)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = os.path.basename(file_path).rsplit(".", 1)[0]
    safe_model = model_name.replace(":", "-")
    output_path = os.path.join("outputs2", f"{base_name}_{safe_model}_{timestamp}.json")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(json_content, f, indent=4, ensure_ascii=False)

    print(f"DONE: saved → {output_path}")
    return json_content
