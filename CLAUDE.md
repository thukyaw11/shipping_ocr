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
  → Upload file to Cloudflare R2
  → Load PDF/image as PIL Images
  → Surya OCR (DetectionPredictor + RecognitionPredictor)
  → Reconstruct text layout per page
  → Classify document type via Gemini/Ollama
  → Extract structured checklist fields via Gemini
  → Run cross-validation rules
  → Store OCRDocument in MongoDB
  → Return ApiResponse[OCRDocument]
```

### Key Directories

- `src/api/v1/endpoints/` — HTTP endpoints: `ocr.py`, `results.py`, `auth_jwt.py`
- `src/services/` — Business logic: OCR pipeline, classification, extraction, cross-validation, S3, auth
- `src/services/ai/` — Pluggable AI providers (`gemini_provider.py`, `ollama_provider.py`) via factory pattern
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
- `ocr_results` — user_id, filename, document_type, pages[], status, timestamps
- `highlights` — user_id + project_id (compound unique), highlights[]

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
