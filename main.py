import io
import json
import os
from datetime import datetime

import pypdfium2
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from PIL import Image

from test_ocr import (
    ExtractedInfo,
    build_layout_text,
    det_predictor,
    rec_predictor,
)
import ollama

app = FastAPI(title="Shipping Bill OCR", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MODEL_NAME = "llama3.2-vision"
OUTPUT_DIR = "outputs2"
PDF_DPI = 300

SYSTEM_PROMPT = (
    "You are a specialized logistics data extractor for Air Waybills (AWB). "
    "Extract all details accurately into the requested JSON format. "
    'If numeric fields contain "AS ARRANGED", use that string instead of a number. '
    'For "freight_prepaid", extract all account numbers into a clean array of strings. '
    "On an AWB form the Currency field (e.g. THB, SGD, USD) appears on the same row "
    "as or immediately below the routing (To/By Carrier) section — extract it into "
    "declaration.currency as a 3-letter ISO code."
)


def pdf_to_images(data: bytes) -> list[Image.Image]:
    """Convert every page of a PDF to a PIL Image at PDF_DPI resolution."""
    doc = pypdfium2.PdfDocument(data)
    scale = PDF_DPI / 72  # pdfium renders at 72 dpi by default
    images = []
    for i, page in enumerate(doc):
        bitmap = page.render(scale=scale, rotation=0)
        images.append(bitmap.to_pil())
        print(f"[PDF] Converted page {i + 1}/{len(doc)}")
    return images


def _run_ocr(image: Image.Image, page_label: str) -> str:
    print(f"[OCR] {page_label} — detecting and recognising text...")
    predictions = rec_predictor([image], det_predictor=det_predictor)
    text = build_layout_text(predictions[0].text_lines)
    print(f"[OCR] {page_label} — done.")
    return text


def _run_ollama(ocr_text: str, page_label: str) -> dict:
    print(f"[Ollama] {page_label} — sending to {MODEL_NAME}, this may take ~1-2 min...")
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


def _process_page(image: Image.Image, base_name: str, page_label: str) -> dict:
    """OCR + Ollama extraction for a single page. Runs in a thread."""
    ocr_text = _run_ocr(image, page_label)
    if not ocr_text.strip():
        print(f"[WARN] {page_label} — no text detected, skipping.")
        return None

    json_content = _run_ollama(ocr_text, page_label)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_model = MODEL_NAME.replace(":", "-")
    output_path = os.path.join(OUTPUT_DIR, f"{base_name}_{safe_model}_{timestamp}.json")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(json_content, f, indent=4, ensure_ascii=False)

    return {"saved_to": output_path, "data": json_content}


@app.post("/ocr", response_class=JSONResponse)
async def ocr_file(file: UploadFile = File(...)):
    """
    Upload a shipping bill image (PNG/JPG) or PDF.
    - Image  → single result
    - PDF    → one result per page, processed sequentially

    Each page result is saved to outputs2/{filename}_{model}_{timestamp}.json.
    """
    contents = await file.read()
    base_name = os.path.splitext(file.filename)[0]
    is_pdf = file.content_type == "application/pdf" or file.filename.lower().endswith(".pdf")

    if is_pdf:
        print(f"[PDF] Converting {file.filename} pages to images at {PDF_DPI} dpi...")
        images = await run_in_threadpool(pdf_to_images, contents)
        print(f"[PDF] {len(images)} page(s) ready.")
    elif file.content_type.startswith("image/"):
        images = [Image.open(io.BytesIO(contents)).convert("RGB")]
    else:
        raise HTTPException(status_code=400, detail="Upload must be an image or PDF.")

    results = []
    for i, image in enumerate(images):
        page_label = f"page {i + 1}/{len(images)}" if len(images) > 1 else file.filename
        page_base = f"{base_name}_p{i + 1}" if len(images) > 1 else base_name

        result = await run_in_threadpool(_process_page, image, page_base, page_label)
        if result:
            results.append({"page": i + 1, **result})

    if not results:
        raise HTTPException(status_code=422, detail="No text detected in any page.")

    # Single image → return result directly; PDF → return list under "pages"
    if len(images) == 1:
        return JSONResponse(content=results[0])
    return JSONResponse(content={"total_pages": len(images), "pages": results})


def _sse_message(obj: dict) -> str:
    """Format a dict as one SSE event (data line + double newline)."""
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


async def _stream_ocr_generator(filename: str, base_name: str, images: list):
    """Async generator: yield SSE events as each page finishes."""
    total = len(images)
    yield _sse_message({"type": "start", "total_pages": total, "filename": filename})

    for i, image in enumerate(images):
        page_num = i + 1
        page_label = f"page {page_num}/{total}" if total > 1 else filename
        page_base = f"{base_name}_p{page_num}" if total > 1 else base_name

        yield _sse_message({"type": "progress", "page": page_num, "total_pages": total, "message": f"Processing {page_label}..."})

        result = await run_in_threadpool(_process_page, image, page_base, page_label)

        if result:
            yield _sse_message({"type": "page", "page": page_num, "saved_to": result["saved_to"], "data": result["data"]})
        else:
            yield _sse_message({"type": "page", "page": page_num, "skipped": True, "reason": "no_text"})

    yield _sse_message({"type": "done", "total_pages": total})


@app.post("/ocr/stream")
async def ocr_file_stream(file: UploadFile = File(...)):
    """
    Upload image or PDF; response is a stream of Server-Sent Events.
    Frontend receives one event per finished page instead of waiting for all.

    Events:
      - start:  { "type": "start", "total_pages": N, "filename": "..." }
      - progress: { "type": "progress", "page": 1, "total_pages": N, "message": "..." }
      - page:   { "type": "page", "page": 1, "saved_to": "...", "data": { ... } }  (or "skipped": true)
      - done:   { "type": "done", "total_pages": N }

    Use fetch() and read response.body as a stream; parse lines starting with "data: ".
    """
    if not file.content_type and not file.filename:
        raise HTTPException(status_code=400, detail="Upload must be an image or PDF.")

    contents = await file.read()
    base_name = os.path.splitext(file.filename)[0]
    is_pdf = file.content_type == "application/pdf" or (file.filename or "").lower().endswith(".pdf")

    if is_pdf:
        images = await run_in_threadpool(pdf_to_images, contents)
    elif (file.content_type or "").startswith("image/"):
        images = [Image.open(io.BytesIO(contents)).convert("RGB")]
    else:
        raise HTTPException(status_code=400, detail="Upload must be an image or PDF.")

    return StreamingResponse(
        _stream_ocr_generator(file.filename or "upload", base_name, images),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
