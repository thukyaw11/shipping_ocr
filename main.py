from src.models.schemas import OCRDocument, OCRPage, OCRLine
from src.core.database import db 
import asyncio
import io
import json
import logging
import os
import re
from datetime import datetime

logging.getLogger("surya").setLevel(logging.WARNING)
logging.getLogger("surya.recognition").setLevel(logging.WARNING)
logging.getLogger("surya.detection").setLevel(logging.WARNING)
logging.getLogger("transformers").setLevel(logging.ERROR)
from typing import Literal
from contextlib import asynccontextmanager
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from src.core.config import Config
from PIL import Image
from src.core.database import connect_to_mongo, close_mongo_connection
from src.api.v1.api import api_router

from src.services.ocr_service import (
    ExtractedInfo,
    build_layout_text,
    det_predictor,
    rec_predictor,
)
import ollama


# Optional Gemini; only used when provider="gemini"
try:
    from google import genai
    _GEMINI_AVAILABLE = True
except ImportError:
    _GEMINI_AVAILABLE = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_to_mongo()
    yield
    await close_mongo_connection()

app = FastAPI(title="Shipping Bill OCR", version="1.0.0", lifespan=lifespan)
app.include_router(api_router, prefix="/api/v1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=Config.ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MODEL_NAME = "llama3.2-vision"

# MODEL_NAME = "qwen3.5:27b"
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_API_KEY = Config.GEMINI_API_KEY
OUTPUT_DIR = "outputs2"
PDF_DPI = 300

# Qwen3 external API (e.g. Cloudflare Worker)
QWEN3_API_URL = Config.QWEN3_API_URL
QWEN3_API_TOKEN = Config.QWEN3_API_TOKEN

Provider = Literal["ollama", "gemini", "qwen3", "qwen3-sanitize"]

SYSTEM_PROMPT = (
    "You are a specialized logistics data extractor for Air Waybills (AWB). "
    "Extract all details accurately into the requested JSON format. "
    'If numeric fields contain "AS ARRANGED", use that string instead of a number. '
    'For "freight_prepaid", extract all account numbers into a clean array of strings. '
    "On an AWB form the Currency field (e.g. THB, SGD, USD) appears on the same row "
    "as or immediately below the routing (To/By Carrier) section — extract it into "
    "declaration.currency as a 3-letter ISO code."
)




def _run_ocr(image: Image.Image, page_label: str) -> str:
    print(f"[OCR] {page_label} — detecting and recognising text...")
    predictions = rec_predictor([image], det_predictor=det_predictor)
    text = build_layout_text(predictions[0].text_lines)
    print(f"[OCR] {page_label} — done.")
    return text


def _run_ollama(ocr_text: str, page_label: str) -> dict:
    print(
        f"[Ollama] {page_label} — sending to {MODEL_NAME}, this may take ~1-2 min...")
    response = ollama.chat(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Extract all detailed logistics data from this OCR text:\n\n{ocr_text}"},
        ],
        format=ExtractedInfo.model_json_schema(),
    )
    print(f"[Ollama] {page_label} — done.")
    return json.loads(response.message.content)


def _get_clean_schema_for_gemini():
    """Schema for Gemini: no additionalProperties, inline $refs."""
    schema = ExtractedInfo.model_json_schema()
    defs = schema.get("$defs", {})

    def strip(obj):
        if isinstance(obj, dict):
            if "$ref" in obj:
                ref_key = obj["$ref"].split("/")[-1]
                return strip(defs[ref_key])
            return {k: strip(v) for k, v in obj.items() if k not in ("additionalProperties", "title", "description", "$defs")}
        if isinstance(obj, list):
            return [strip(x) for x in obj]
        return obj

    return strip(schema)


def _run_gemini(ocr_text: str, page_label: str) -> dict:
    if not _GEMINI_AVAILABLE:
        raise RuntimeError(
            "google-genai is not installed. pip install google-genai")
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY environment variable is not set")
    print(f"[Gemini] {page_label} — sending to {GEMINI_MODEL}...")
    client = genai.Client(api_key=GEMINI_API_KEY)
    clean_schema = _get_clean_schema_for_gemini()
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=f"Extract all detailed logistics data from this OCR text:\n\n{ocr_text}",
        config={
            "response_mime_type": "application/json",
            "response_schema": clean_schema,
            "system_instruction": "You are a specialized logistics data extractor for Air Waybills (AWB). Extract data from OCR text into valid JSON following the provided schema. If numeric fields contain 'AS ARRANGED', use that string. For freight_prepaid, extract account numbers into a clean array of strings. Extract Currency (THB, SGD, USD) into declaration.currency.",
        },
    )
    print(f"[Gemini] {page_label} — done.")
    return json.loads(response.text)


def _run_qwen3(ocr_text: str, page_label: str) -> dict:
    """Call external Qwen API (e.g. Cloudflare Worker) for structured extraction."""
    if not QWEN3_API_TOKEN:
        raise ValueError("QWEN3_API_TOKEN environment variable is not set")
    import requests
    print(f"[Qwen3] {page_label} — sending to {QWEN3_API_URL}…")
    prompt = (
        "Extract all detailed logistics data from the following OCR text of an Air Waybill (AWB). "
        "Return only a single valid JSON object (no markdown, no explanation) with these top-level keys: "
        "document_info, parties, routing_and_destination, declaration, cargo_details, handling_information, "
        "accounting_and_charges, execution. Use the same structure as standard AWB extraction. "
        "If a field contains 'AS ARRANGED', use that string. For freight_prepaid use an array of account number strings. "
        "Put currency (e.g. THB, SGD) in declaration.currency.\n"
        "IMPORTANT — for ALL charge/amount fields (weight_charge, other_charges, valuation_charge, tax, totals, etc.): "
        "extract ONLY the numeric value, never include the charge code prefix. "
        "Examples: 'RAC:100.00' → 100.00, 'XBC:11.7' → 11.7, 'CGC:6.00' → 6.00. "
        "If multiple charges appear in a single field (e.g. 'XBC:11.7, MYC:29.25'), sum them into one number (e.g. 40.95).\n\n"
        f"OCR text:\n{ocr_text}"
    )
    payload = {
        "messages": [
            # /no-think disables Qwen3 thinking mode at the prompt level (works even if
            # the proxy doesn't forward chat_template_kwargs)
            {"role": "system", "content": "You are a logistics data extractor. Reply with only valid JSON. /no-think"},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 4096,
        # Disable Qwen3 thinking mode so the model returns content directly
        # instead of putting everything in reasoning_content
        "chat_template_kwargs": {"enable_thinking": False},
    }
    resp = requests.post(
        QWEN3_API_URL,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {QWEN3_API_TOKEN}",
        },
        json=payload,
        timeout=180,
    )
    resp.raise_for_status()
    data = resp.json()
    print(
        f"[Qwen3] {page_label} — finish_reason: {data.get('choices', [{}])[0].get('finish_reason')}")
    # OpenAI-style: choices[0].message.content; or direct .content / .text
    raw = None
    if "choices" in data and len(data["choices"]) > 0:
        msg = data["choices"][0].get("message", {})
        # Qwen3 thinking models put the answer in content; fall back to reasoning_content
        raw = msg.get("content") or msg.get(
            "reasoning_content") or data["choices"][0].get("text")
    if raw is None:
        raw = data.get("content") or data.get(
            "response") or data.get("text") or resp.text
    if isinstance(raw, dict):
        return raw
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    print(f"[Qwen3] {page_label} — done.")
    return json.loads(text)


def _sanitize_ocr_text(ocr_text: str, doc_type: str = "awb") -> str:
    """Call the Cloudflare Worker /sanitize endpoint to clean noisy OCR text."""
    import requests
    sanitize_url = QWEN3_API_URL.rstrip("/") + "/sanitize"
    print(f"[Sanitize] Calling {sanitize_url} with doc_type={doc_type}…")
    resp = requests.post(
        sanitize_url,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {QWEN3_API_TOKEN}",
        },
        json={"doc_type": doc_type, "ocr_text": ocr_text},
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    # Accept either {"sanitized_text": ...} or {"text": ...} or {"result": ...}
    cleaned = (
        data.get("sanitized_text")
        or data.get("text")
        or data.get("result")
        or data.get("content")
    )
    if not cleaned:
        print("[Sanitize] WARNING: empty response, falling back to original text.")
        return ocr_text
    print(
        f"[Sanitize] Done. Original length={len(ocr_text)}, cleaned length={len(cleaned)}")
    return cleaned


def _run_qwen3_sanitize(ocr_text: str, page_label: str) -> dict:
    """Sanitize OCR text first, then run normal Qwen3 extraction on the clean result."""
    print(f"[Qwen3-Sanitize] {page_label} — step 1: sanitizing OCR text…")
    clean_text = _sanitize_ocr_text(ocr_text, doc_type="awb")
    print(
        f"[Qwen3-Sanitize] {page_label} — step 2: extracting JSON from cleaned text…")
    return _run_qwen3(clean_text, page_label)


def _to_float(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    m = re.findall(r"-?\d+(?:\.\d+)?", s.replace(",", ""))
    if not m:
        return None
    try:
        return float(m[-1])
    except ValueError:
        return None


def _normalize_qwen_to_schema(raw: dict) -> dict:
    """Normalize qwen-style JSON into the same schema shape as Gemini output."""
    di = raw.get("document_info", {}) or {}
    parties = raw.get("parties", {}) or {}
    rd = raw.get("routing_and_destination", {}) or {}
    dec = raw.get("declaration", {}) or {}
    cargo = raw.get("cargo_details", {}) or {}
    handling = raw.get("handling_information", {}) or {}
    acc = raw.get("accounting_and_charges", {}) or {}
    exe = raw.get("execution", {}) or {}

    doc_num = di.get("document_number") or di.get("awb_number") or ""
    digits = "".join(re.findall(r"\d", str(doc_num)))
    airline_prefix = di.get("airline_prefix") or (
        digits[:3] if len(digits) >= 3 else None)
    serial_number = di.get("serial_number") or (
        digits[3:] if len(digits) > 3 else None)
    awb_number = di.get("awb_number") or (digits if digits else None)

    issuing = parties.get("issuing_agent") or parties.get("agent") or {}
    routing_items = rd.get("routing")
    if not isinstance(routing_items, list):
        routing_items = []
        fd = rd.get("flight_date")
        if fd:
            # e.g. "S00708 05MAR" -> carrier "SQ"/"S0" is ambiguous; keep as flight_number+date best effort
            parts = str(fd).split()
            routing_items = [{
                "to": rd.get("airport_of_destination") or rd.get("destination_airport"),
                "by_carrier": None,
                "flight_number": parts[0] if parts else None,
                "date": parts[1] if len(parts) > 1 else None,
            }]

    charges = acc.get("charges", {}) if isinstance(
        acc.get("charges"), dict) else {}
    prepaid_accounts = (
        acc.get("freight_prepaid")
        or acc.get("freight_prepaid_account_numbers")
        or []
    )
    if not isinstance(prepaid_accounts, list):
        prepaid_accounts = [str(prepaid_accounts)]

    normalized = {
        "document_info": {
            "awb_number": awb_number,
            "airline_prefix": airline_prefix,
            "serial_number": serial_number,
            "document_status": di.get("document_status") or di.get("status"),
            "copy_name": di.get("copy_name") or di.get("copies"),
        },
        "parties": {
            "shipper": {
                "name": (parties.get("shipper") or {}).get("name"),
                "address": (parties.get("shipper") or {}).get("address"),
                "phone": (parties.get("shipper") or {}).get("phone"),
                "fax": (parties.get("shipper") or {}).get("fax"),
                "account_number": (parties.get("shipper") or {}).get("account_number"),
            },
            "consignee": {
                "name": (parties.get("consignee") or {}).get("name"),
                "address": (parties.get("consignee") or {}).get("address"),
                "phone": (parties.get("consignee") or {}).get("phone"),
                "fax": (parties.get("consignee") or {}).get("fax"),
                "account_number": (parties.get("consignee") or {}).get("account_number"),
            },
            "issuing_agent": {
                "name": issuing.get("name"),
                "city": issuing.get("city"),
                "iata_code": issuing.get("iata_code"),
            },
        },
        "routing_and_destination": {
            "departure_airport": rd.get("departure_airport") or rd.get("airport_of_departure"),
            "destination_airport": rd.get("destination_airport") or rd.get("airport_of_destination"),
            "routing": routing_items if isinstance(routing_items, list) else [],
        },
        "declaration": {
            "currency": dec.get("currency") or rd.get("currency"),
            "charge_code": dec.get("charge_code"),
            "weight_valuation_charge": dec.get("weight_valuation_charge"),
            "other_charges": dec.get("other_charges"),
            "declared_value_for_carriage": dec.get("declared_value_for_carriage") or rd.get("declared_value_for_carriage"),
            "declared_value_for_customs": dec.get("declared_value_for_customs") or rd.get("declared_value_for_customs"),
        },
        "cargo_details": {
            "pieces": int(_to_float(cargo.get("pieces") or cargo.get("number_of_pieces")) or 0) or None,
            "gross_weight": _to_float(cargo.get("gross_weight") or cargo.get("gross_weight_kg")),
            "weight_unit": cargo.get("weight_unit"),
            "rate_class": cargo.get("rate_class"),
            "chargeable_weight": _to_float(cargo.get("chargeable_weight") or cargo.get("chargeable_weight_kg")),
            "rate_charge": _to_float(cargo.get("rate_charge") or cargo.get("rate")),
            "total_weight_charge": _to_float(cargo.get("total_weight_charge")),
            "nature_and_quantity_of_goods": cargo.get("nature_and_quantity_of_goods") or cargo.get("commodity"),
            "total_volume_mc": _to_float(cargo.get("total_volume_mc") or cargo.get("total_volume_m3")),
            "dimensions": cargo.get("dimensions") if isinstance(cargo.get("dimensions"), list) else [],
        },
        "handling_information": {
            "special_notes": handling.get("special_notes") or handling.get("handling_instructions"),
            "instruction": handling.get("instruction"),
            "eap": handling.get("eap"),
            "rcar": handling.get("rcar") or handling.get("special_instructions"),
        },
        "accounting_and_charges": {
            "freight_prepaid": [str(x) for x in prepaid_accounts if x is not None],
            "other_charges_breakdown": {
                "weight_charge": _to_float(charges.get("weight_charge") or acc.get("weight_charge")),
                "other_charges": _to_float(charges.get("other_charges") or acc.get("other_charges")),
                "valuation_charge": _to_float(charges.get("valuation_charge") or acc.get("valuation_charge")),
                "tax": _to_float(charges.get("tax") or acc.get("tax")),
                "total_other_charges_due_agent": _to_float(charges.get("total_other_charges_due_agent") or acc.get("total_other_charges_due_agent")),
                "total_other_charges_due_carrier": _to_float(charges.get("total_other_charges_due_carrier") or acc.get("total_other_charges_due_carrier")),
            },
            "total_prepaid_summary": {
                "weight_charge": _to_float(charges.get("weight_charge") or acc.get("weight_charge")),
                "total_other_charges_due_agent": _to_float(charges.get("total_other_charges_due_agent") or acc.get("total_other_charges_due_agent")),
                "grand_total": _to_float(acc.get("total_prepaid") or acc.get("grand_total")),
            },
        },
        "execution": {
            "shipper_signature_authority": exe.get("shipper_signature_authority") or exe.get("signature"),
            "execution_date": exe.get("execution_date") or exe.get("date"),
            "execution_place": exe.get("execution_place") or exe.get("place"),
            "carrier_signature_code": exe.get("carrier_signature_code") or exe.get("issuer_signature") or exe.get("carrier"),
        },
    }
    # Ensure strict, consistent output shape like Gemini route
    return ExtractedInfo.model_validate(normalized).model_dump()


def _run_extraction(ocr_text: str, page_label: str, provider: Provider) -> dict:
    if provider == "gemini":
        return _run_gemini(ocr_text, page_label)
    if provider == "qwen3":
        return _normalize_qwen_to_schema(_run_qwen3(ocr_text, page_label))
    if provider == "qwen3-sanitize":
        return _normalize_qwen_to_schema(_run_qwen3_sanitize(ocr_text, page_label))
    return _run_ollama(ocr_text, page_label)


def _process_page(image: Image.Image, base_name: str, page_label: str, provider: Provider) -> dict:
    """OCR + LLM extraction for a single page. Runs in a thread."""
    ocr_text = _run_ocr(image, page_label)
    if not ocr_text.strip():
        print(f"[WARN] {page_label} — no text detected, skipping.")
        return None

    json_content = _run_extraction(ocr_text, page_label, provider)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    model_label = (
        GEMINI_MODEL if provider == "gemini"
        else "qwen3-sanitize" if provider == "qwen3-sanitize"
        else "qwen3" if provider == "qwen3"
        else MODEL_NAME
    )
    safe_model = model_label.replace(":", "-")
    output_path = os.path.join(
        OUTPUT_DIR, f"{base_name}_{safe_model}_{timestamp}.json")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(json_content, f, indent=4, ensure_ascii=False)

    return {"saved_to": output_path, "data": json_content}


@app.post("/ocr", response_class=JSONResponse)
async def ocr_file(
    file: UploadFile = File(...),
    provider: Provider = Query(
        "ollama", description="LLM: 'ollama' | 'gemini' | 'qwen3' | 'qwen3-sanitize'"),
):
    """
    Upload a shipping bill image (PNG/JPG) or PDF.
    - Image  → single result
    - PDF    → one result per page, processed sequentially
    - provider: ollama (default), gemini (set GEMINI_API_KEY), qwen3 or qwen3-sanitize (set QWEN3_API_URL, QWEN3_API_TOKEN).
    Each page result is saved to outputs2/{filename}_{model}_{timestamp}.json.
    """
    if provider == "gemini":
        if not GEMINI_API_KEY:
            raise HTTPException(
                status_code=400, detail="GEMINI_API_KEY not set. Use provider=ollama or set the env var.")
        if not _GEMINI_AVAILABLE:
            raise HTTPException(
                status_code=503, detail="Gemini support not installed. pip install google-genai")
    if provider in ("qwen3", "qwen3-sanitize"):
        if not QWEN3_API_TOKEN:
            raise HTTPException(
                status_code=400, detail="QWEN3_API_TOKEN not set. Set env var or use another provider.")
    contents = await file.read()
    base_name = os.path.splitext(file.filename)[0]
    is_pdf = file.content_type == "application/pdf" or file.filename.lower().endswith(".pdf")

    if is_pdf:
        print(
            f"[PDF] Converting {file.filename} pages to images at {PDF_DPI} dpi...")
        images = await run_in_threadpool(pdf_to_images, contents)
        print(f"[PDF] {len(images)} page(s) ready.")
    elif file.content_type.startswith("image/"):
        images = [Image.open(io.BytesIO(contents)).convert("RGB")]
    else:
        raise HTTPException(
            status_code=400, detail="Upload must be an image or PDF.")

    results = []
    for i, image in enumerate(images):
        page_label = f"page {i + 1}/{len(images)}" if len(
            images) > 1 else file.filename
        page_base = f"{base_name}_p{i + 1}" if len(images) > 1 else base_name

        result = await run_in_threadpool(_process_page, image, page_base, page_label, provider)
        if result:
            results.append({"page": i + 1, **result})

    if not results:
        raise HTTPException(
            status_code=422, detail="No text detected in any page.")

    # Single image → return result directly; PDF → return list under "pages"
    if len(images) == 1:
        return JSONResponse(content=results[0])
    return JSONResponse(content={"total_pages": len(images), "pages": results})


def _sse_message(obj: dict) -> str:
    """Format a dict as one SSE event (data line + double newline)."""
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


async def _stream_ocr_generator(filename: str, base_name: str, images: list, provider: Provider):
    """Async generator: yield SSE events as each page finishes. Includes percent and state for UI."""
    total = len(images)
    yield _sse_message({
        "type": "start",
        "total_pages": total,
        "filename": filename,
        "provider": provider,
        "state": "Starting…",
    })

    for i, image in enumerate(images):
        page_num = i + 1
        page_label = f"page {page_num}/{total}" if total > 1 else filename
        page_base = f"{base_name}_p{page_num}" if total > 1 else base_name

        # 0% — starting this page
        yield _sse_message({
            "type": "progress",
            "page": page_num,
            "total_pages": total,
            "message": f"Processing {page_label}…",
            "page_percent": 0,
            "state": f"Processing page {page_num} of {total}…" if total > 1 else "Extracting text…",
        })

        # Run OCR in thread (keepalives every 10s)
        task = asyncio.create_task(
            run_in_threadpool(_run_ocr, image, page_label))
        ocr_text = None
        # 10% — OCR done
        yield _sse_message({
            "type": "progress",
            "page": page_num,
            "total_pages": total,
            "message": f"Extracting data from {page_label}…",
            "page_percent": 10,
            "state": f"Extracting data (page {page_num})…" if total > 1 else "Extracting data…",
        })

        # 20% — OCR done
        yield _sse_message({
            "type": "progress",
            "page": page_num,
            "total_pages": total,
            "message": f"Extracting data from {page_label}…",
            "page_percent": 20,
            "state": f"Extracting data (page {page_num})…" if total > 1 else "Extracting data…",
        })

        try:
            while not task.done():
                try:
                    ocr_text = await asyncio.wait_for(asyncio.shield(task), timeout=10.0)
                    break
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except Exception as e:
            yield _sse_message({
                "type": "error",
                "page": page_num,
                "total_pages": total,
                "page_percent": 0,
                "state": "Error",
                "message": str(e),
            })
            continue

        if not ocr_text or not ocr_text.strip():
            yield _sse_message({
                "type": "page",
                "page": page_num,
                "total_pages": total,
                "skipped": True,
                "reason": "no_text",
                "page_percent": 100,
                "state": f"Page {page_num} skipped (no text)" if total > 1 else "Skipped",
            })
            continue

        # 30% — OCR done
        yield _sse_message({
            "type": "progress",
            "page": page_num,
            "total_pages": total,
            "message": f"Extracting data from {page_label}…",
            "page_percent": 30,
            "state": f"Extracting data (page {page_num})…" if total > 1 else "Extracting data…",
        })

        # Run LLM extraction in thread (keepalives every 10s)
        task = asyncio.create_task(run_in_threadpool(
            _run_extraction, ocr_text, page_label, provider))
        json_content = None
        try:
            while not task.done():
                try:
                    json_content = await asyncio.wait_for(asyncio.shield(task), timeout=10.0)
                    break
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except Exception as e:
            yield _sse_message({
                "type": "error",
                "page": page_num,
                "total_pages": total,
                "page_percent": 30,
                "state": "Error",
                "message": str(e),
            })
            continue

        # 60% — LLM done, saving
        yield _sse_message({
            "type": "progress",
            "page": page_num,
            "total_pages": total,
            "message": f"Saving page {page_num}…",
            "page_percent": 60,
            "state": f"Saving page {page_num}…" if total > 1 else "Saving…",
        })

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        model_label = (
            GEMINI_MODEL if provider == "gemini"
            else "qwen3-sanitize" if provider == "qwen3-sanitize"
            else "qwen3" if provider == "qwen3"
            else MODEL_NAME
        )
        safe_model = model_label.replace(":", "-")
        output_path = os.path.join(
            OUTPUT_DIR, f"{page_base}_{safe_model}_{timestamp}.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(json_content, f, indent=4, ensure_ascii=False)

        # 100% — page done
        yield _sse_message({
            "type": "page",
            "page": page_num,
            "total_pages": total,
            "saved_to": output_path,
            "data": json_content,
            "page_percent": 100,
            "state": f"Page {page_num} of {total} done" if total > 1 else "Done",
        })

    yield _sse_message({
        "type": "done",
        "total_pages": total,
        "state": "Complete",
    })


@app.post("/ocr/stream")
async def ocr_file_stream(
    file: UploadFile = File(...),
    provider: Provider = Query(
        "ollama", description="LLM: 'ollama' | 'gemini' | 'qwen3' | 'qwen3-sanitize'"),
):
    """
    Upload image or PDF; response is a stream of Server-Sent Events.
    Frontend receives one event per finished page instead of waiting for all.
    provider: ollama (default), gemini (GEMINI_API_KEY), qwen3 or qwen3-sanitize (QWEN3_API_URL, QWEN3_API_TOKEN).

    Events include "state"; progress/page include "page_percent" (0 → 30 → 60 → 100 per page):
      - start:   state="Starting…"
      - progress: page_percent=0 (OCR) | 30 (OCR done, LLM) | 60 (saving), state=...
      - page:    page_percent=100, state="Page X done" | "Skipped", plus saved_to/data or skipped
      - done:    state="Complete"

    Use fetch() and read response.body as a stream; parse lines starting with "data: ".
    """
    if provider == "gemini":
        if not GEMINI_API_KEY:
            raise HTTPException(
                status_code=400, detail="GEMINI_API_KEY not set. Use provider=ollama or set the env var.")
        if not _GEMINI_AVAILABLE:
            raise HTTPException(
                status_code=503, detail="Gemini support not installed. pip install google-genai")
    if provider in ("qwen3", "qwen3-sanitize"):
        if not QWEN3_API_TOKEN:
            raise HTTPException(
                status_code=400, detail="QWEN3_API_TOKEN not set. Set env var or use another provider.")
    if not file.content_type and not file.filename:
        raise HTTPException(
            status_code=400, detail="Upload must be an image or PDF.")

    contents = await file.read()
    base_name = os.path.splitext(file.filename)[0]
    is_pdf = file.content_type == "application/pdf" or (
        file.filename or "").lower().endswith(".pdf")

    if is_pdf:
        images = await run_in_threadpool(pdf_to_images, contents)
    elif (file.content_type or "").startswith("image/"):
        images = [Image.open(io.BytesIO(contents)).convert("RGB")]
    else:
        raise HTTPException(
            status_code=400, detail="Upload must be an image or PDF.")

    return StreamingResponse(
        _stream_ocr_generator(file.filename or "upload",
                              base_name, images, provider),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )




if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
