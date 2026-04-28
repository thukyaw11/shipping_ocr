# MongoDB Schema Reference

Database: `shipping_ocr` (configurable via `DATABASE_NAME` env var)

---

## Collections Overview

| Collection | Description |
|---|---|
| [users](#users) | Authenticated user accounts |
| [canvases](#canvases) | User workspaces grouping related documents |
| [ocr_results](#ocr_results) | OCR-processed shipping documents |
| [highlights](#highlights) | Per-document annotation highlights |
| [scan_logs](#scan_logs) | Billing and audit trail for each scan |
| [import_entries](#import_entries) | Versioned import declaration documents |
| [customers](#customers) | Customer profiles with HS code data |

---

## users

Stores registered user accounts. Supports both email/password and Google OAuth sign-in.

| Field | Type | Required | Constraints | Notes |
|---|---|---|---|---|
| `_id` | ObjectId | auto | — | MongoDB primary key |
| `email` | string | yes | unique index | Lowercase-normalized |
| `password_hash` | string | no | — | Absent for Google-only accounts |
| `google_sub` | string | no | unique, sparse index | Google OAuth subject identifier |
| `created_at` | datetime | yes | — | UTC |

**Indexes**

| Fields | Options |
|---|---|
| `email` | unique |
| `google_sub` | unique, sparse |

---

## canvases

A canvas is a named workspace that groups one or more OCR documents belonging to a user.

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `_id` | ObjectId | auto | — | MongoDB primary key |
| `user_id` | string | yes | — | Ref → `users._id` |
| `name` | string | yes | — | Display name |
| `status` | string | yes | `"active"` | |
| `created_at` | datetime | yes | `utcnow()` | |
| `edited_at` | datetime | yes | `utcnow()` | Updated on rename or any child write |
| `is_deleted` | bool | yes | `false` | Soft-delete flag |
| `deleted_at` | datetime | no | `null` | Set on soft delete |

**Indexes**

| Fields | Options | Purpose |
|---|---|---|
| `(user_id ASC, edited_at DESC)` | — | Fast paginated listing per user |

**Relations**

- `user_id` → `users._id`
- One canvas contains many `ocr_results` and `import_entries`

---

## ocr_results

Stores a single processed shipping document (PDF/image). Each document has one or more pages; each page holds OCR text lines, classification label, and checklist extraction.

### Top-level fields

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `_id` | ObjectId | auto | — | MongoDB primary key |
| `canvas_id` | string | no | `null` | Ref → `canvases._id` |
| `user_id` | string | no | `null` | Ref → `users._id` |
| `filename` | string | yes | — | Original upload filename |
| `url` | string | yes | — | Cloudflare R2 file URL |
| `preview` | string | no | `null` | Thumbnail URL |
| `type` | string | yes | `"pdf"` | File type |
| `total_pages` | int | yes | — | Page count |
| `overall_confidence` | float | no | `null` | Aggregate OCR confidence (0–1) |
| `document_type` | string | no | `null` | `MAWB` \| `HAWB` \| `IATA` \| `INVOICE` \| `CARGO_MANIFEST` \| `UNKNOWN` |
| `sort_order` | int | yes | `0` | Display order within canvas |
| `status` | string | yes | `"completed"` | Processing status |
| `data` | array\<OCRPage\> | yes | — | Per-page OCR data (see below) |
| `connections` | array\<PageConnection\> | no | `null` | Directed links between pages |
| `cross_validation_results` | array\<ValidationResult\> | no | `null` | Rule engine output |
| `created_at` | datetime | yes | `utcnow()` | |
| `edited_at` | datetime | yes | `utcnow()` | |
| `is_deleted` | bool | yes | `false` | Soft-delete flag |
| `deleted_at` | datetime | no | `null` | Set on soft delete |

### Embedded: OCRPage (`data[]`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `paged_idx` | int | yes | 0-based page index |
| `page_type` | string | no | `MAWB` \| `HAWB` \| `IATA` \| `INVOICE` \| `CARGO_MANIFEST` \| `UNKNOWN` |
| `sub_page_type` | string | no | Invoice vendor (e.g. `SYMRISE`, `TAKASAGO`) |
| `page_confidence` | float | no | Average line confidence (0–1) |
| `image_bbox` | array\<float\> | yes | `[x0, y0, x1, y1]` in pixels |
| `raw_text` | string | no | Raw concatenated OCR text |
| `text_lines` | array\<OCRLine\> | yes | See below |
| `checklist` | object | no | Structured extraction; schema depends on `page_type` |

### Embedded: OCRLine (`data[].text_lines[]`)

| Field | Type | Notes |
|---|---|---|
| `text` | string | Recognized text |
| `confidence` | float | Per-line confidence (0–1) |
| `bbox` | array\<float\> | `[x0, y0, x1, y1]` |
| `polygon` | array\<array\<float\>\> | Quadrilateral corner points |

### Embedded: PageConnection (`connections[]`)

| Field | Type | Notes |
|---|---|---|
| `from` | int | Source page index (`paged_idx`) |
| `to` | int | Target page index (`paged_idx`) |
| `confidence` | float | Match confidence (0–1) |

### Embedded: ValidationResult (`cross_validation_results[]`)

| Field | Type | Notes |
|---|---|---|
| `rule_name` | string | Rule identifier |
| `category` | string | Optional grouping label |
| `status` | string | `pass` \| `fail` \| `skipped` |
| `expected` | any | Expected value |
| `actual` | any | Observed value |
| `message` | string | Human-readable explanation |

**Indexes**

| Fields | Options | Purpose |
|---|---|---|
| `(canvas_id ASC, sort_order ASC)` | — | Ordered retrieval within canvas |
| `(user_id ASC, edited_at DESC)` | — | User history, newest first |

**Relations**

- `canvas_id` → `canvases._id`
- `user_id` → `users._id`
- Referenced by `highlights.ocr_result_id`
- Referenced by `import_entries.ocr_result_id`

---

## highlights

Stores user-created annotations for a specific OCR document. At most one document per `(user_id, canvas_id, ocr_result_id)` tuple (enforced by unique index).

| Field | Type | Required | Notes |
|---|---|---|---|
| `_id` | ObjectId | auto | MongoDB primary key |
| `user_id` | string | yes | Ref → `users._id` |
| `canvas_id` | string | yes | Ref → `canvases._id` |
| `ocr_result_id` | string | yes | Ref → `ocr_results._id` |
| `highlights` | array | yes | Arbitrary highlight objects |
| `created_at` | datetime | yes | Set on first upsert |
| `updated_at` | datetime | yes | Updated on every upsert |

**Indexes**

| Fields | Options | Purpose |
|---|---|---|
| `(user_id ASC, canvas_id ASC, ocr_result_id ASC)` | unique | Enforce one doc per PDF per canvas per user |

**Relations**

- `user_id` → `users._id`
- `canvas_id` → `canvases._id`
- `ocr_result_id` → `ocr_results._id`

---

## scan_logs

Immutable audit and billing record written after every upload attempt (success or failure). Never updated after creation.

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `_id` | ObjectId | auto | — | MongoDB primary key |
| `user_id` | string | yes | — | Ref → `users._id` |
| `filename` | string | yes | — | Upload filename |
| `file_size_bytes` | int | no | `null` | |
| `content_type` | string | no | `null` | MIME type |
| `canvas_id` | string | no | `null` | Ref → `canvases._id` |
| `ocr_result_id` | string | no | `null` | Ref → `ocr_results._id`; null on failure |
| `total_pages` | int | no | `null` | |
| `document_type` | string | no | `null` | Classified doc type |
| `status` | string | yes | `"success"` | `success` \| `failed` |
| `error_message` | string | no | `null` | Populated on failure |
| `processing_time_ms` | int | no | `null` | Wall-clock processing time |
| `price_per_page` | float | no | `null` | Billing: only on success |
| `pages_charged` | int | no | `null` | Billing: only on success |
| `total_cost` | float | no | `null` | Billing: only on success |
| `created_at` | datetime | yes | `utcnow()` | |

**Indexes**

| Fields | Options | Purpose |
|---|---|---|
| `(user_id ASC, created_at DESC)` | — | Fast user billing/history queries |

**Relations**

- `user_id` → `users._id`
- `canvas_id` → `canvases._id` (may be null on failure)
- `ocr_result_id` → `ocr_results._id` (may be null on failure)

---

## import_entries

Versioned import declaration documents attached to a canvas. Multiple versions can exist for a canvas; only the latest non-deleted version is active.

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `_id` | ObjectId | auto | — | MongoDB primary key |
| `canvas_id` | string | yes | — | Ref → `canvases._id` |
| `user_id` | string | yes | — | Ref → `users._id` |
| `version` | int | yes | — | Auto-incremented per canvas |
| `filename` | string | yes | — | Upload filename |
| `url` | string | yes | — | Cloudflare R2 file URL |
| `type` | string | yes | `"pdf"` | File type |
| `preview` | string | no | `null` | Thumbnail URL |
| `total_pages` | int | yes | — | |
| `overall_confidence` | float | no | `null` | |
| `data` | array\<OCRPage\> | yes | `[]` | Same schema as `ocr_results.data[]` |
| `status` | string | yes | `"completed"` | |
| `ocr_result_id` | string | no | `null` | Optional link to related shipping doc |
| `created_at` | datetime | yes | `utcnow()` | |
| `edited_at` | datetime | yes | `utcnow()` | |
| `is_deleted` | bool | yes | `false` | Soft-delete flag |
| `deleted_at` | datetime | no | `null` | Set on soft delete |

**Indexes**

| Fields | Options | Purpose |
|---|---|---|
| `(canvas_id ASC, created_at DESC)` | — | Latest-version lookup per canvas |
| `(user_id ASC, created_at DESC)` | — | User history |
| `ocr_result_id ASC` | sparse | Reverse-lookup from shipping doc |

**Relations**

- `canvas_id` → `canvases._id`
- `user_id` → `users._id`
- `ocr_result_id` → `ocr_results._id` (optional link to shipping document)
- Embeds `OCRPage` — same structure as `ocr_results.data[]`

---

## customers

Customer profiles owned per-user, with embedded HS (Harmonized System) code data.

| Field | Type | Required | Default | Constraints | Notes |
|---|---|---|---|---|---|
| `_id` | string (UUID) | auto | — | — | String UUID, not ObjectId |
| `user_id` | string | yes | — | — | Ref → `users._id` |
| `name` | string | yes | — | 1–255 chars | Company name |
| `priority` | string | yes | — | `high` \| `medium` \| `low` | |
| `location` | string | yes | `""` | 0–255 chars | |
| `address` | string | yes | `""` | 0–512 chars | |
| `emails` | array\<string\> | yes | `[]` | — | Contact emails |
| `profile_url` | string | no | `null` | — | Avatar/profile image URL |
| `hs_code_data` | array\<HSCodeData\> | yes | `[]` | — | See below |
| `created_at` | datetime | yes | `utcnow()` | — | |
| `updated_at` | datetime | yes | `utcnow()` | — | |

### Embedded: HSCodeData (`hs_code_data[]`)

| Field | Type | Max Length | Notes |
|---|---|---|---|
| `product` | string | 128 | Product name |
| `definition` | string | 2000 | HS code definition |
| `code` | string | 128 | HS code value |
| `duty` | string | 255 | Import duty rate |
| `license` | string | 255 | License requirement |
| `remark` | string | 2000 | Additional notes |

**Relations**

- `user_id` → `users._id`

---

## Entity-Relationship Summary

```
users
 ├─< canvases           (user_id)
 ├─< ocr_results        (user_id)
 ├─< highlights         (user_id)
 ├─< scan_logs          (user_id)
 ├─< import_entries     (user_id)
 └─< customers          (user_id)

canvases
 ├─< ocr_results        (canvas_id)
 ├─< import_entries     (canvas_id)
 └─< highlights         (canvas_id)

ocr_results
 ├─< highlights         (ocr_result_id)
 ├─< scan_logs          (ocr_result_id, nullable)
 └─< import_entries     (ocr_result_id, optional/sparse)
```

---

## Soft Delete Pattern

`canvases`, `ocr_results`, and `import_entries` use soft deletes:

- `is_deleted: true` + `deleted_at: <datetime>` are set instead of removing the document.
- All queries filter with `{ "is_deleted": { "$ne": true } }` — documents without the field are treated as not deleted (backwards-compatible with older records).
- `scan_logs`, `highlights`, `users`, and `customers` do not use soft delete.

---

## Document Type Labels

Used in `ocr_results.document_type` and `ocr_results.data[].page_type`:

| Label | Description |
|---|---|
| `MAWB` | Master Air Waybill |
| `HAWB` | House Air Waybill |
| `IATA` | IATA cargo document |
| `INVOICE` | Commercial invoice |
| `CARGO_MANIFEST` | Cargo manifest |
| `UNKNOWN` | Unclassified page |
