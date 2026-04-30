# AI Use Cases — Gemini Integration

This document describes every place the application calls the Gemini API, what it sends, and what it gets back.

---

## Overview

The application uses **Google Gemini** as its primary AI provider for two distinct tasks:

| Task | When it runs | Output |
|------|-------------|--------|
| **Document Classification** | After Surya OCR, per page | Single label string |
| **Checklist Extraction** | After classification, per page | Structured JSON object |

Both tasks are executed concurrently across all pages using `asyncio.gather`.

---

## Provider Architecture

```
TextGenerationProvider (Protocol)
    └── GeminiTextProvider          ← primary (google-genai SDK)
    └── OllamaTextProvider          ← fallback (classification only)
```

`GeminiTextProvider` exposes two methods:

- `generate(system_prompt, user_prompt) → str` — free-form text response
- `generate_structured_json(system_prompt, user_prompt, response_model) → Pydantic model` — enforces a JSON schema via `response_mime_type: application/json` + `response_json_schema`

The model is configured via `GEMINI_MODEL` env var (defaults to a Gemini 1.5 / 2.0 variant). The API key comes from `GEMINI_API_KEY`.

---

## Task 1 — Document Classification

**File:** [src/services/document_classification.py](../src/services/document_classification.py)

### What it does

Reads OCR text from a single page and returns one of these labels:

```
MAWB | HAWB | IATA | INVOICE | CARGO_MANIFEST | UNKNOWN
```

### How it calls Gemini

```
System prompt  →  hard-coded CLASSIFICATION_SYSTEM_PROMPT
                  "Classify logistics/air-cargo OCR text into exactly one label…"

User prompt    →  "Classify this page OCR text (page N):\n\n{sanitized_text}"

Response model →  DocumentTypeClassificationOutput
                  { "document_type": "<label>" }
```

OCR text is **sanitized before sending**: whitespace is normalized and the string is capped at **4 000 characters** (`sanitize_ocr_text()`).

Gemini returns a strict JSON object. If parsing fails, the page is labeled `UNKNOWN` — no plain-text fallback.

### Invoice Sub-Classification

When a page is labeled `INVOICE`, a second Gemini call identifies the vendor:

```
System prompt  →  INVOICE_COMPANY_CLASSIFICATION_SYSTEM_PROMPT
                  "Identify which company this INVOICE belongs to…"

User prompt    →  "Identify the company for this invoice page (page N):\n\n{text}"

Response model →  InvoiceCompanyClassificationOutput
                  { "company": "<company_name>" }
```

Recognized vendors: `SYMRISE, TAKASAGO, GIVAUDAN, IFF, FLAVOR FORCE, SILESIA, SHERWIN, ALLNEX, KH ROBERT, THAI SPECIALITY, PERSPECES, NOURYON, COLOSSAL INTERNATIONAL`

The result is stored as `page.sub_page_type`.

### Provider selection

Controlled by `CLASSIFICATION_PROVIDER` env var:

| Value | Behavior |
|-------|----------|
| `gemini` | Gemini only |
| `ollama` | Ollama only |
| `auto` (default) | Try Gemini first, fall back to Ollama |

---

## Task 2 — Checklist Extraction

**File:** [src/services/checklist_extraction.py](../src/services/checklist_extraction.py)

**Always uses Gemini — no Ollama fallback.**

### What it does

Reads OCR text from a single classified page and returns a fully structured data object containing all relevant document fields.

### Page type → Pydantic model mapping

| Page Type | Pydantic Model | YAML Prompt |
|-----------|---------------|-------------|
| `MAWB` | `MAWBCheckList` | `mawb.yaml` |
| `HAWB` | `MAWBCheckList` | `hawb.yaml` |
| `IATA` | `IATAChecklist` | `iata.yaml` |
| `INVOICE` | `InvoiceChecklist` | `invoice.yaml` |
| `CARGO_MANIFEST` | `ManifestChecklist` | `manifest.yaml` |
| `IMPORT_ENTRY` | `ImportEntryChecklist` | `import_entry.yaml` |

### How it calls Gemini

```
System prompt  →  base.yaml system_prompt
                  +
                  <type>.yaml system_prompt
                  (concatenated, base first)

User prompt    →  base.yaml user_prompt_template
                  filled with: page_type, ocr_text, sub_page_type_context

Response model →  Pydantic model for the page type
                  (full JSON schema sent as response_json_schema)
```

OCR text is capped at **14 000 characters** before sending.

### Prompt system

Prompts live in [src/prompts/checklists/](../src/prompts/checklists/) as YAML files.

**base.yaml** — shared rules applied to every extraction:
```
You extract structured checklist fields from logistics document OCR text.
Use only information that appears in the OCR; use null for unknown or missing values.
Do not invent values. Parse numbers from the text when clearly present.
Tolerate OCR noise: broken columns, pipe characters between cells, and line wraps.
```

**Type-specific YAML** adds field-by-field extraction instructions. Example fields extracted per type:

- **MAWB / HAWB** — AWB number, airline prefix, shipper, consignee, addresses, weights, charges, currency, execution date, flight number, freight numbers (HAWB list)
- **IATA** — IATA-standard air cargo fields
- **INVOICE** — line items, totals, tax, invoice number/date, vendor
- **CARGO_MANIFEST** — shipment list, HAWB numbers, weights per consignment
- **IMPORT_ENTRY** — entry number, entry date, importer/supplier details, HS codes, CIF value, duty, tax, total payable

### Output

`extract_checklist_sync()` returns a `dict` (via `model.model_dump(mode='json')`), or `None` if the page type is unsupported or Gemini fails. This dict is stored as `page.checklist` in MongoDB.

---

## Full Pipeline Sequence

```
Upload PDF/image
        │
        ▼
[Surya OCR]  — batch, all pages, local model, no Gemini
        │
        ▼
[Step 1 — Classification]  — asyncio.gather, one Gemini call per page
        │   text capped at 4 000 chars
        │   → label: MAWB | HAWB | IATA | INVOICE | CARGO_MANIFEST | UNKNOWN
        │
        ▼
[Step 2 — Invoice Sub-classification]  — only for INVOICE pages
        │   → sub_page_type: vendor name
        │
        ▼
[Step 3 — Checklist Extraction]  — asyncio.gather, one Gemini call per page
        │   text capped at 14 000 chars
        │   → structured JSON fields stored as page.checklist
        │
        ▼
[Step 4 — Cross-validation]  — local rule engine, no Gemini
        │
        ▼
Store in MongoDB
```

---

## Gemini API Call Summary

| Step | Method | Char limit | Response format |
|------|--------|-----------|----------------|
| Classification | `generate_structured_json` | 4 000 | `{ document_type }` |
| Invoice vendor | `generate_structured_json` | 4 000 | `{ company }` |
| Checklist extraction | `generate_structured_json` | 14 000 | Full document schema |

All calls use the same `GeminiTextProvider` instance with `response_mime_type: application/json` enforced at the SDK level, so Gemini is constrained to return valid JSON matching the exact Pydantic schema.
