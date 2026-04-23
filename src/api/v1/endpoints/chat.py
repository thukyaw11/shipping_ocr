import json
import logging
import uuid
from typing import AsyncIterator, List, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.core.auth import verify_jwt
from src.core.config import Config
from src.repositories.import_entry_repository import import_entry_repo

logger = logging.getLogger("shipping_bill_ocr")

router = APIRouter()

# ---------------------------------------------------------------------------
# Request / Response models (Vercel AI SDK UIMessage format)
# ---------------------------------------------------------------------------


class Part(BaseModel):
    type: str
    text: Optional[str] = None


class UIMessage(BaseModel):
    id: str
    role: str
    parts: List[Part]


class ChatRequest(BaseModel):
    id: str
    messages: List[UIMessage]
    canvas_id: Optional[str] = None

    model_config = {"extra": "ignore"}


# ---------------------------------------------------------------------------
# Raw text extraction from import entries
# ---------------------------------------------------------------------------


def _page_raw_text(page: dict) -> str:
    """Extract text from a single OCR page — prefer raw_text, fall back to text_lines."""
    if page.get("raw_text"):
        return page["raw_text"]
    lines = page.get("text_lines") or []
    return "\n".join(line["text"] for line in lines if line.get("text"))


async def _build_document_context(canvas_id: str, user_id: str) -> str:
    """
    Fetch all import entries for the canvas and return a formatted
    context block containing every page's raw OCR text.
    """
    entries = await import_entry_repo.list_all(canvas_id, user_id)
    if not entries:
        return ""

    blocks: list[str] = []
    for entry in entries:
        filename = entry.get("filename", "unknown")
        pages = entry.get("data") or []
        page_texts = []
        for page in pages:
            paged_idx = page.get("paged_idx", "?")
            page_type = page.get("page_type") or "unknown"
            text = _page_raw_text(page).strip()
            if text:
                page_texts.append(
                    f"  [Page {paged_idx} — {page_type}]\n{text}"
                )
        if page_texts:
            blocks.append(
                f"### Document: {filename}\n" + "\n\n".join(page_texts)
            )

    if not blocks:
        return ""

    return (
        "The following raw OCR text has been extracted from the import entry "
        "documents in this workspace. Use this as the primary source of truth "
        "when answering questions.\n\n"
        + "\n\n---\n\n".join(blocks)
    )


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an expert assistant specializing in Thai customs and import entry processing (กรมศุลกากร). You have deep knowledge of:

**Import Duty & Tax Calculations:**
- CIF value (Cost + Insurance + Freight) at Thai port of entry
- Import Duty based on HS code rates from Thai Customs Tariff (MFN, preferential rates under AFTA, RCEP, FTA agreements)
- Value Added Tax (VAT): 7% applied on (CIF + Import Duty)
- Excise Tax (ภาษีสรรพสามิต) for controlled goods: alcohol, tobacco, vehicles, fuel, electronics, etc.
- Excise Tax surcharges: Health Promotion Fund (2% of excise), Thai PBS Fund (1.5% of excise), elderly fund (2% of excise)
- Interior Tax (ภาษีเทศบาล/มหาดไทย): 10% of Excise Tax
- Total payable = Import Duty + Excise Tax + Excise surcharges + Interior Tax + VAT
- WTO Customs Valuation Agreement: transaction value method (primary), deductive/computed value (fallback)
- Currency conversion to THB using Bank of Thailand (BoT) reference rate or Thai Customs declared rate

**Thai Customs-Specific Rules:**
- De minimis threshold: imports under THB 1,500 CIF may be exempt from duty (subject to type)
- Preferential tariff under ASEAN Free Trade Area (AFTA/ATIGA): Form D certificate of origin required
- RCEP preferential rates: Form RCEP or back-to-back certificate required
- Post-clearance audit (PCA) obligations and self-assessment declaration
- HS code classification using Thai Customs Tariff Schedule (based on HS 2022)
- Prohibited and restricted goods requiring licenses (FDA, TISI, DFT, etc.)
- Inward Processing Relief (IPR) and bonded warehouse regimes

**Import Entry Documents (Thai Customs):**
- Customs Import Declaration (ใบขนสินค้าขาเข้า / Khor Sor 99/1) — declarant, importer, supplier, HS code, description, quantity, unit, CIF, duty rate, taxes, invoice no
- Air Waybill (MAWB/HAWB) — shipper, consignee, airline, origin, gross weight, chargeable weight, declared value
- Commercial Invoice — seller, buyer, item description, unit price, total value, currency, Incoterms
- Packing List — package count, gross weight, net weight, dimensions
- Bill of Lading (B/L) — for sea freight: vessel, voyage, port of loading/discharge, container no
- Certificate of Origin (Form D, Form RCEP, Form E, etc.) — for preferential duty rates
- Import License / Permit — for controlled goods

**Calculation workflow:**
1. Confirm Incoterms on invoice; adjust to CIF at Thai port if needed (add freight + insurance if FOB/EXW)
2. Convert foreign currency to THB using BoT rate on the date of import declaration
3. Identify HS code (10-digit Thai tariff), check MFN rate and applicable FTA preferential rate
4. Import Duty = CIF (THB) × duty rate %
5. Check if Excise Tax applies for the HS code; compute Excise Tax (ad valorem or specific rate)
6. Interior Tax = Excise Tax × 10%
7. Excise surcharges = Excise Tax × (2% + 1.5% + 2%)
8. VAT base = CIF + Import Duty + Excise Tax + Interior Tax + Excise surcharges
9. VAT = VAT base × 7%
10. Total = Import Duty + Excise Tax + Interior Tax + Excise surcharges + VAT

**OCR raw text handling:**
When the user provides raw OCR text from a shipping document, extract and identify all relevant fields, correct OCR errors using context (e.g., 0 vs O, 1 vs I, Thai numerals ๑๒๓ vs Arabic), reconstruct missing values through cross-field validation, and perform any requested calculations.

Always show your working step by step. State all assumed rates and exchange rates explicitly. When values are ambiguous or missing, say so clearly and ask for clarification."""


# ---------------------------------------------------------------------------
# Gemini streaming
# ---------------------------------------------------------------------------


def _build_gemini_history(messages: List[UIMessage]) -> list[dict]:
    """
    Convert UIMessage list to Gemini contents format.
    All messages except the last are treated as history.
    """
    history = []
    for msg in messages:
        text = " ".join(
            p.text for p in msg.parts if p.type == "text" and p.text
        )
        if not text:
            continue
        role = "user" if msg.role == "user" else "model"
        history.append({"role": role, "parts": [{"text": text}]})
    return history


# ---------------------------------------------------------------------------
# SSE helpers (Vercel AI SDK v6 typed-event protocol)
# ---------------------------------------------------------------------------


def _sse(obj: dict) -> str:
    """Format a dict as a single SSE event."""
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------------
# Gemini streaming
# ---------------------------------------------------------------------------


async def _stream_gemini(
    messages: List[UIMessage],
    document_context: str = "",
) -> AsyncIterator[str]:
    message_id = str(uuid.uuid4())
    text_part_id = "text-0"

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        yield _sse({"type": "start", "messageId": message_id})
        yield _sse({"type": "start-step"})
        yield _sse({"type": "text-start", "id": text_part_id})
        yield _sse({"type": "text-delta", "id": text_part_id, "delta": "Google Gemini SDK is not installed."})
        yield _sse({"type": "text-end", "id": text_part_id})
        yield _sse({"type": "finish-step"})
        yield _sse({"type": "finish", "finishReason": "error"})
        return

    api_key = Config.GEMINI_API_KEY
    if not api_key:
        yield _sse({"type": "start", "messageId": message_id})
        yield _sse({"type": "start-step"})
        yield _sse({"type": "text-start", "id": text_part_id})
        yield _sse({"type": "text-delta", "id": text_part_id, "delta": "GEMINI_API_KEY is not configured."})
        yield _sse({"type": "text-end", "id": text_part_id})
        yield _sse({"type": "finish-step"})
        yield _sse({"type": "finish", "finishReason": "error"})
        return

    client = genai.Client(api_key=api_key)
    model = Config.GEMINI_MODEL

    contents = _build_gemini_history(messages)
    if not contents:
        yield _sse({"type": "error", "errorText": "No message content found."})
        return

    system_instruction = SYSTEM_PROMPT
    if document_context:
        system_instruction = SYSTEM_PROMPT + "\n\n" + document_context

    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=0.2,
    )

    last_user_text = next(
        (c["parts"][0]["text"] for c in reversed(contents) if c["role"] == "user"),
        "",
    )
    print(
        f"[chat] request model={model} turn={len(contents)} "
        f"last_user={last_user_text[:120]!r}",
        flush=True,
    )

    yield _sse({"type": "start", "messageId": message_id})
    yield _sse({"type": "start-step"})
    yield _sse({"type": "text-start", "id": text_part_id})

    full_response: list[str] = []

    try:
        async for chunk in await client.aio.models.generate_content_stream(
            model=model,
            contents=contents,
            config=config,
        ):
            text = getattr(chunk, "text", None) or ""
            if text:
                full_response.append(text)
                yield _sse({"type": "text-delta", "id": text_part_id, "delta": text})
    except Exception as exc:
        logger.exception("Gemini streaming error")
        yield _sse({"type": "text-end", "id": text_part_id})
        yield _sse({"type": "finish-step"})
        yield _sse({"type": "error", "errorText": str(exc)})
        return

    print(
        f"[chat] response words={len(''.join(full_response).split())} "
        f"preview={(''.join(full_response))[:200]!r}",
        flush=True,
    )

    yield _sse({"type": "text-end", "id": text_part_id})
    yield _sse({"type": "finish-step"})
    yield _sse({"type": "finish", "finishReason": "stop"})


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("/chat")
async def chat(
    request: ChatRequest,
    payload: dict = Depends(verify_jwt),
):
    user_id = payload.get("sub", "")
    document_context = ""
    if request.canvas_id and user_id:
        document_context = await _build_document_context(
            request.canvas_id, user_id
        )
        print(
            f"[chat] loaded context canvas_id={request.canvas_id} "
            f"chars={len(document_context)}",
            flush=True,
        )

    return StreamingResponse(
        _stream_gemini(request.messages, document_context),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
