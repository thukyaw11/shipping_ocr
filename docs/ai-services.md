# AI Services — Classification & Extraction

This document covers the provider architecture, how to call classification and extraction from code, and how to configure each provider.

---

## Architecture

```
TextGenerationProvider (Protocol)
    ├── GeminiTextProvider        ← google-genai SDK
    ├── OllamaTextProvider        ← ollama SDK (classification only)
    └── CloudflareAIProvider      ← openai SDK → CF Workers AI
```

Both tasks — **classification** and **checklist extraction** — run through the same `TextGenerationProvider` protocol. The provider is resolved once at startup via factory functions; all calling code is provider-agnostic.

---

## Protocol — `TextGenerationProvider`

**File:** [src/services/ai/base.py](../src/services/ai/base.py)

```python
class TextGenerationProvider(Protocol):
    def generate(self, system_prompt: str, user_prompt: str) -> str: ...

    def generate_structured_json(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[TModel],   # Pydantic BaseModel subclass
    ) -> TModel: ...
```

- `generate` — free-form text response; used as a fallback in classification when structured output fails.
- `generate_structured_json` — enforces a JSON schema matching `response_model`. Returns a validated Pydantic instance. Used for all extraction calls and preferred for classification.

> **Ollama** only implements `generate`. Classification detects this at runtime via `getattr` and falls back to text parsing. Extraction always requires `generate_structured_json` — Ollama cannot be used for extraction.

---

## Task 1 — Document Classification

**File:** [src/services/document_classification.py](../src/services/document_classification.py)

### Usage

```python
from src.services.ai.factory import get_classification_text_provider
from src.services.document_classification import DocumentTypeClassifier, sanitize_page_with_log

classifier = DocumentTypeClassifier(get_classification_text_provider())

# Classify a single page
ocr_text = sanitize_page_with_log(page_num=1, page_text=raw_text)  # trims to 4 000 chars
label = classifier.classify_page(ocr_text, page_num=1)
# → "MAWB" | "HAWB" | "IATA" | "INVOICE" | "CARGO_MANIFEST" | "UNKNOWN"

# For INVOICE pages: identify the vendor
company = classifier.classify_invoice_company(ocr_text, page_num=1)
# → "SYMRISE" | "TAKASAGO" | ... | "UNKNOWN"
# stored as page.sub_page_type
```

### How it calls the provider

```
System prompt  → CLASSIFICATION_SYSTEM_PROMPT
                 "Classify logistics/air-cargo OCR text into exactly one label…"

User prompt    → "Classify this page OCR text (page N):\n\n{sanitized_text}"

Response model → DocumentTypeClassificationOutput
                 { "document_type": "<label>" }
```

If the provider has `generate_structured_json`, it is used and the result is validated against the Pydantic schema. Otherwise `generate` is called and the raw text is scanned for the first matching label string.

### OCR text pre-processing

```python
sanitize_ocr_text(text, max_chars=4000)
# → collapses whitespace, truncates to 4 000 chars
```

---

## Task 2 — Checklist Extraction

**File:** [src/services/checklist_extraction.py](../src/services/checklist_extraction.py)

### Usage

```python
from src.services.ai.factory import get_extraction_provider
from src.services.checklist_extraction import extract_checklist_sync

provider = get_extraction_provider()

result: dict | None = extract_checklist_sync(
    provider=provider,
    page_type="MAWB",           # or HAWB | IATA | INVOICE | CARGO_MANIFEST | IMPORT_ENTRY
    ocr_text=raw_text,
    max_ocr_chars=14000,        # default, trims OCR before sending
    sub_page_type="SYMRISE",    # optional, only relevant for INVOICE pages
)
# → dict (model_dump) or None on failure / unsupported type
```

### Page type → Pydantic model mapping

| `page_type` | Pydantic model | YAML prompt |
|-------------|---------------|-------------|
| `MAWB` | `MAWBCheckList` | `mawb.yaml` |
| `HAWB` | `MAWBCheckList` | `hawb.yaml` |
| `IATA` | `IATAChecklist` | `iata.yaml` |
| `INVOICE` | `InvoiceChecklist` | `invoice.yaml` |
| `CARGO_MANIFEST` | `ManifestChecklist` | `manifest.yaml` |
| `IMPORT_ENTRY` | `ImportEntryChecklist` | `import_entry.yaml` |

### How it calls the provider

```
System prompt  → base.yaml system_prompt
                 + <type>.yaml system_prompt  (concatenated)

User prompt    → base.yaml user_prompt_template
                 filled with: page_type, ocr_text (≤14 000 chars), sub_page_type

Response model → Pydantic model for the page type
```

Returns `None` (never raises) — all provider errors are caught and logged as `checklist extraction failed`.

---

## Factory Functions

**File:** [src/services/ai/factory.py](../src/services/ai/factory.py)

### `get_classification_text_provider()`

Reads `CLASSIFICATION_PROVIDER` env var.

| Value | Behaviour |
|-------|-----------|
| `auto` (default) | Gemini if `GEMINI_API_KEY` + SDK available; else Ollama |
| `gemini` | Gemini only; raises if unconfigured |
| `ollama` | Ollama only |
| `cloudflare` | CF Workers AI; raises if `CF_WORKER_URL` or `CF_ACCOUNT_ID`+`CF_API_TOKEN` not set |

### `get_extraction_provider()`

Reads `EXTRACTION_PROVIDER` env var.

| Value | Behaviour |
|-------|-----------|
| `gemini` (default) | Gemini only; raises if unconfigured |
| `cloudflare` | CF Workers AI; raises if credentials not set |

> Ollama is **not** supported for extraction because it does not implement `generate_structured_json`.

---

## Providers

### Gemini

**File:** [src/services/ai/gemini_provider.py](../src/services/ai/gemini_provider.py)

```env
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.5-flash                  # used for extraction
GEMINI_CLASSIFICATION_MODEL=gemini-2.5-flash-lite  # used for classification
```

Enforces JSON schema via `response_mime_type: application/json` + `response_json_schema` at the SDK level. Most reliable structured output.

### Ollama

**File:** [src/services/ai/ollama_provider.py](../src/services/ai/ollama_provider.py)

```env
OLLAMA_CLASSIFICATION_MODEL=qwen3.5:397b-cloud
```

Only implements `generate`. Used for classification in `auto` mode when Gemini is unavailable. Structured output is approximated by text scanning.

### Cloudflare Workers AI

**File:** [src/services/ai/cloudflare_provider.py](../src/services/ai/cloudflare_provider.py)

Two connection modes — **Worker mode** is preferred for local development.

#### Worker mode (via wrangler)

```env
CF_WORKER_URL=http://localhost:8787   # wrangler dev
CF_AI_MODEL=@cf/qwen/qwen3-30b-a3b-fp8
CLASSIFICATION_PROVIDER=cloudflare
EXTRACTION_PROVIDER=cloudflare
```

Start the gateway Worker locally:

```bash
cd worker
wrangler login   # once
wrangler dev     # starts on http://localhost:8787
```

For production, deploy the Worker first:

```bash
cd worker
wrangler deploy
# → https://ocr-ai-gateway.<subdomain>.workers.dev
```

Then set `CF_WORKER_URL=https://ocr-ai-gateway.<subdomain>.workers.dev`.

#### REST API mode (no wrangler)

```env
CF_ACCOUNT_ID=<cloudflare account id>
CF_API_TOKEN=<cloudflare api token>
CF_AI_MODEL=@cf/qwen/qwen3-30b-a3b-fp8
CLASSIFICATION_PROVIDER=cloudflare
EXTRACTION_PROVIDER=cloudflare
```

No Worker or wrangler needed. Calls `https://api.cloudflare.com/client/v4/accounts/{id}/ai/v1` directly.

#### Available Qwen models on CF Workers AI

| Model | Notes |
|-------|-------|
| `@cf/qwen/qwen3-30b-a3b-fp8` | Default — general purpose, MoE |
| `@hf/qwen/qwen2.5-coder-32b-instruct` | Code-focused |
| `@cf/qwen/qwq-32b` | Reasoning-focused |

> CF Workers AI JSON schema compliance is best-effort. If the model returns invalid JSON, `generate_structured_json` raises `ValidationError`, which `extract_checklist_sync` catches and returns `None`.

---

## Full Environment Variable Reference

| Variable | Default | Purpose |
|----------|---------|---------|
| `CLASSIFICATION_PROVIDER` | `auto` | `auto` \| `gemini` \| `ollama` \| `cloudflare` |
| `EXTRACTION_PROVIDER` | `gemini` | `gemini` \| `cloudflare` |
| `GEMINI_API_KEY` | — | Required for Gemini providers |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Extraction model |
| `GEMINI_CLASSIFICATION_MODEL` | `gemini-2.5-flash-lite` | Classification model |
| `OLLAMA_CLASSIFICATION_MODEL` | `qwen3.5:397b-cloud` | Ollama model name |
| `CF_WORKER_URL` | — | Worker mode base URL (takes priority over REST) |
| `CF_ACCOUNT_ID` | — | REST mode: Cloudflare account ID |
| `CF_API_TOKEN` | — | Cloudflare API token |
| `CF_AI_MODEL` | `@cf/qwen/qwen3-30b-a3b-fp8` | Model for CF Workers AI |
| `DEBUG_CLASSIFICATION` | `false` | Print sanitize timings to stdout |

---

## Provider Capability Matrix

| Provider | `generate` | `generate_structured_json` | Classification | Extraction |
|----------|-----------|---------------------------|---------------|------------|
| Gemini | Yes | Yes (native schema) | Yes | Yes |
| Ollama | Yes | No | Yes (text fallback) | No |
| Cloudflare AI | Yes | Yes (best-effort) | Yes | Yes |
