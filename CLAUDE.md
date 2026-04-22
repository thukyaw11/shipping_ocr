# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

FastAPI backend for shipping document OCR and structured data extraction. Processes PDFs/images of air waybills (MAWB/HAWB), invoices, and cargo manifests using:
- **Surya** for open-source OCR (text detection + recognition)
- **Gemini API** (primary) or **Ollama** (fallback) for document classification and structured extraction
- **MongoDB** for result storage; **Cloudflare R2** for raw file storage
- **JWT + Google OAuth** for authentication

## Running the Service

```bash
# Install dependencies
pip install -r requirements.txt

# Start server
python main.py
# or
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

API docs available at `http://localhost:8000/docs` when running.

## Required Environment Variables

```env
MONGODB_URL=mongodb://localhost:27017
DATABASE_NAME=shipping_ocr
JWT_SECRET_KEY=...
GOOGLE_CLIENT_ID=...
GEMINI_API_KEY=...
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_ENDPOINT_URL=https://...r2.cloudflarestorage.com
R2_BUCKET_NAME=raw-files
CLASSIFICATION_PROVIDER=auto  # gemini | ollama | auto
```

Optional: `GEMINI_MODEL`, `DOC_TYPE_MODEL`, `ALLOW_ORIGINS`, `DEBUG_CLASSIFICATION`, `OCR_LOG_LEVEL`

## Architecture

### Request Flow (OCR Upload)

```
POST /api/v1/ocr/surya
  → Auth check (verify_jwt)
  → ocr_processing_service.process_ocr_upload()
      → Upload file to Cloudflare R2 (s3_service)
      → Load PDF/image as PIL Images (capped at 2000px longest side)
      → [Step 1] Surya batch OCR — all pages in one pass (English, configurable batch sizes)
      → [Step 2] Concurrent classification — asyncio.gather across all pages
          → sanitize_page_with_log() → truncate to 4000 chars
          → DocumentTypeClassifier.classify_page() via Gemini structured JSON
          → labels: MAWB | HAWB | IATA | INVOICE | CARGO_MANIFEST | UNKNOWN
      → [Step 3] Invoice sub-classification (if any page = INVOICE)
          → classify_invoice_company() → identifies vendor (SYMRISE, TAKASAGO, etc.)
          → stored as page.sub_page_type
      → document_type = most frequent non-UNKNOWN page_type (no extra API call)
      → [Step 4] Checklist extraction (Gemini only, all pages concurrent)
          → YAML prompt loaded per doc type from src/prompts/checklists/
          → gemini.generate_structured_json() with Pydantic model schema
          → page_type → model: MAWB/HAWB→MAWBCheckList, IATA→IATAChecklist,
                                INVOICE→InvoiceChecklist, CARGO_MANIFEST→ManifestChecklist
          → OCR text truncated to 14000 chars before sending
      → [Step 5] Cross-validation rule engine
          → multi-page merge: scalar=first-non-null, list=concatenated across pages
          → rules: match | sum_match | array_sum_match | list_match
          → result per rule: pass | fail | skipped
      → Resolve/create canvas (canvas_repo)
      → Store OCRDocument in MongoDB (ocr_result_repo)
      → Write scan log with billing fields (scan_log_repo)
  → Return ApiResponse[OCRDocument]
```

### Result Enrichment (at query time, not scan time)

When fetching a saved OCR result, `ocr_result_enricher.enrich_ocr_result()` runs:

```
enrich_confidence_fields()   → fills page_confidence/overall_confidence from text lines if missing
attach_checklists()          → flattens page.checklist into top-level checklists[]
attach_connections()         → runs page_connections engine (config-driven, never raises)
attach_cross_validation()    → re-runs cross-validation rules on stored pages
localize timestamps          → converts created_at/edited_at to user's timezone
```

**Page connections** are resolved by `page_connections.py` using `CONNECTION_RULES`:
- `list_overlap` — set intersection between two fields (e.g. MAWB.freight_numbers ∩ CARGO_MANIFEST.hawb_list[].hawb_no)
- `key_match` — scalar equality (e.g. HAWB.awb_number == IATA.awb_number)

Active rules: `MAWB→CARGO_MANIFEST`, `CARGO_MANIFEST→HAWB`, `HAWB→IATA`, `INVOICE→INVOICE`, `HAWB→INVOICE`

### Layer Responsibilities

```
endpoints/      HTTP only — auth check, call services/repos, return ApiResponse
    ↓
services/       Business logic — no direct db access, imports repos
    ↓
repositories/   All MongoDB access — the only layer that touches db.db[...]
```

**Rules:**
- Endpoints raise `HTTPException`, never touch `db` directly
- Services contain business logic, import repos (not `db`)
- Repos are the only files that call `db.db[collection]`

### Key Directories

- `src/api/v1/endpoints/` — HTTP endpoints: `ocr.py`, `results.py`, `auth_jwt.py`, `customers.py`
- `src/services/` — Business logic:
  - `ocr_processing_service.py` — full OCR pipeline orchestration (upload → OCR → checklist → persist)
  - `ocr_result_enricher.py` — enrichment helpers (confidence, checklists, connections, cross-validation)
  - `surya_ocr_pipeline.py`, `document_classification.py`, `checklist_extraction.py`, `cross_validation.py`
  - `s3_service.py` — Cloudflare R2 upload
  - `pricing.py` — per-page cost calculation
- `src/services/ai/` — Pluggable AI providers (`gemini_provider.py`, `ollama_provider.py`) via factory pattern
- `src/repositories/` — MongoDB access layer:
  - `canvas_repository.py` — canvas CRUD + soft delete
  - `ocr_result_repository.py` — OCR result CRUD, page field updates, soft delete
  - `scan_log_repository.py` — scan log writes
  - `highlight_repository.py` — highlight get/upsert
- `src/models/` — Pydantic schemas; `checklists/` has per-doc-type models (MAWB, HAWB, Invoice, Manifest)
- `src/prompts/checklists/` — YAML prompt templates for each document type (externalized from code)
- `src/core/` — Config, MongoDB connection, JWT auth, response wrapper, exception handlers
- `test/` — Manual test scripts (`run_surya.py`, `scan.py`) and `index.html` viewer

### AI Provider Strategy

`CLASSIFICATION_PROVIDER` env var controls classification:
- `auto` — try Gemini first, fallback to Ollama
- `gemini` — Gemini only
- `ollama` — Ollama only

Checklist extraction **always uses Gemini** (no Ollama fallback).

### MongoDB Collections

- `users` — email (unique), password_hash, google_sub (sparse unique)
- `canvases` — user_id, name, status, is_deleted, deleted_at, edited_at
- `ocr_results` — user_id, canvas_id, filename, document_type, data[], is_deleted, deleted_at, status, timestamps
- `highlights` — user_id + canvas_id + ocr_result_id (compound unique), highlights[]
- `scan_logs` — user_id, filename, status, billing fields, processing_time_ms
- `customers` — user_id, name, priority, location, address, emails, profile_url, hs_code_data[]

### Soft Deletes

Both canvases and OCR results support soft delete via `is_deleted: true` + `deleted_at`. All queries filter with `{"is_deleted": {"$ne": True}}` so missing field (old docs) is treated as not deleted.

### Response Format

All endpoints return `ApiResponse[T]` from `src/core/response_wrapper.py`:
```json
{ "success": true, "data": {...}, "message": "..." }
```

## Testing

No automated test suite. Manual testing via:
```bash
python test/run_surya.py   # Test Surya OCR pipeline
python test/scan.py        # Scanning utility
```
Use Swagger UI at `/docs` for endpoint testing.
