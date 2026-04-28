# Import Entries API

Base path: `/api/v1/canvases/{canvas_id}/import-entries`

All endpoints require `Authorization: Bearer <token>`.

Import entries are versioned customs/import declaration documents attached to a canvas. Each upload creates a new version; the most recently uploaded non-deleted entry is the "active" one.

---

## Data Model

### ImportEntryDocument object

```json
{
  "id": "665a1b2c3d4e5f6a7b8c9d0e",
  "canvas_id": "664f1a2b3c4d5e6f7a8b9c0d",
  "version": 1,
  "filename": "import_declaration.pdf",
  "url": "https://r2.example.com/raw-files/import_declaration.pdf",
  "type": "pdf",
  "preview": "https://r2.example.com/raw-files/import_declaration_preview.jpg",
  "total_pages": 2,
  "overall_confidence": 0.9312,
  "status": "completed",
  "ocr_result_id": "665a1b2c3d4e5f6a7b8c9d0f",
  "created_at": "2026-04-24T10:00:00",
  "edited_at": "2026-04-24T10:00:00",
  "data": [ <OCRPage> ]
}
```

| Field | Type | Notes |
|---|---|---|
| `id` | string | MongoDB ObjectId as string |
| `canvas_id` | string | Parent canvas ID |
| `version` | int | Upload sequence number within the canvas, starts at 1, increments on each upload |
| `filename` | string | Original uploaded filename |
| `url` | string | Cloudflare R2 URL of the raw file |
| `type` | string | Always `"pdf"` |
| `preview` | string \| null | R2 URL of a preview image (first page) |
| `total_pages` | int | Total page count |
| `overall_confidence` | float \| null | Average OCR confidence across all pages (0–1) |
| `status` | string | `"completed"` or `"failed"` |
| `ocr_result_id` | string \| null | Links to a related shipping document (MAWB / HAWB / etc.) if associated |
| `created_at` | ISO 8601 datetime | UTC |
| `edited_at` | ISO 8601 datetime | UTC |
| `data` | OCRPage[] | Per-page OCR results — same shape as OCRPage in [HISTORY_API.md](./HISTORY_API.md) |

> `data[].checklist` shape for import entries follows its own extraction model — fields depend on the document content.

---

## Endpoints

### List all import entries

`GET /api/v1/canvases/{canvas_id}/import-entries`

Returns all non-deleted import entries for the canvas, newest first. No pagination — all entries returned at once.

**Path params:**

| Param | Type | Notes |
|---|---|---|
| `canvas_id` | string | MongoDB ObjectId |

**Response `200`:**

```json
{
  "success": true,
  "message": "Success",
  "data": [
    {
      "id": "665a1b2c3d4e5f6a7b8c9d0e",
      "canvas_id": "664f1a2b3c4d5e6f7a8b9c0d",
      "version": 2,
      "filename": "import_declaration_v2.pdf",
      "url": "https://r2.example.com/raw-files/import_declaration_v2.pdf",
      "type": "pdf",
      "preview": null,
      "total_pages": 2,
      "overall_confidence": 0.9312,
      "status": "completed",
      "ocr_result_id": null,
      "created_at": "2026-04-24T12:00:00",
      "edited_at": "2026-04-24T12:00:00",
      "data": []
    },
    {
      "id": "665a1b2c3d4e5f6a7b8c9d0d",
      "canvas_id": "664f1a2b3c4d5e6f7a8b9c0d",
      "version": 1,
      "filename": "import_declaration_v1.pdf",
      "url": "https://r2.example.com/raw-files/import_declaration_v1.pdf",
      "type": "pdf",
      "preview": null,
      "total_pages": 1,
      "overall_confidence": 0.8910,
      "status": "completed",
      "ocr_result_id": null,
      "created_at": "2026-04-24T10:00:00",
      "edited_at": "2026-04-24T10:00:00",
      "data": []
    }
  ]
}
```

**Errors:**

| Status | `detail` | Cause |
|---|---|---|
| `404` | `"Canvas not found."` | Wrong canvas ID or belongs to another user |

---

### Get active import entry

`GET /api/v1/canvases/{canvas_id}/import-entries/active`

Returns the most recently uploaded non-deleted import entry (highest `created_at`). Returns `null` in `data` if none exists.

**Path params:**

| Param | Type | Notes |
|---|---|---|
| `canvas_id` | string | MongoDB ObjectId |

**Response `200` — entry found:**

```json
{
  "success": true,
  "message": "Success",
  "data": {
    "id": "665a1b2c3d4e5f6a7b8c9d0e",
    "canvas_id": "664f1a2b3c4d5e6f7a8b9c0d",
    "version": 2,
    "filename": "import_declaration_v2.pdf",
    "url": "https://r2.example.com/raw-files/import_declaration_v2.pdf",
    "type": "pdf",
    "preview": null,
    "total_pages": 2,
    "overall_confidence": 0.9312,
    "status": "completed",
    "ocr_result_id": null,
    "created_at": "2026-04-24T12:00:00",
    "edited_at": "2026-04-24T12:00:00",
    "data": [ ... ]
  }
}
```

**Response `200` — no entry:**

```json
{
  "success": true,
  "message": "No import entry found.",
  "data": null
}
```

**Errors:**

| Status | `detail` | Cause |
|---|---|---|
| `404` | `"Canvas not found."` | Wrong canvas ID or belongs to another user |

---

### Upload import entry

`POST /api/v1/canvases/{canvas_id}/import-entries/upload`

`multipart/form-data` — field name: `file`

Uploads a new import entry document for the canvas. OCR is processed and a new version is created. Each upload increments `version` regardless of soft-deleted entries.

**Path params:**

| Param | Type | Notes |
|---|---|---|
| `canvas_id` | string | MongoDB ObjectId |

**Request:** `multipart/form-data` with field `file` (PDF or image)

**Response `200`:**

```json
{
  "success": true,
  "message": "Success",
  "data": {
    "id": "665a1b2c3d4e5f6a7b8c9d0f",
    "canvas_id": "664f1a2b3c4d5e6f7a8b9c0d",
    "version": 3,
    "filename": "import_declaration_v3.pdf",
    "url": "https://r2.example.com/raw-files/import_declaration_v3.pdf",
    "type": "pdf",
    "preview": null,
    "total_pages": 2,
    "overall_confidence": 0.9501,
    "status": "completed",
    "ocr_result_id": null,
    "created_at": "2026-04-25T08:00:00",
    "edited_at": "2026-04-25T08:00:00",
    "data": [ ... ]
  }
}
```

**Errors:**

| Status | `detail` | Cause |
|---|---|---|
| `401` | `"Invalid token: missing subject"` | Token missing `sub` claim |

---

### Delete import entry

`DELETE /api/v1/canvases/{canvas_id}/import-entries/{entry_id}`

Soft-deletes an import entry. The entry is excluded from list/active responses but the version number is not reused.

**Path params:**

| Param | Type | Notes |
|---|---|---|
| `canvas_id` | string | MongoDB ObjectId |
| `entry_id` | string | MongoDB ObjectId of the import entry |

**Response `200`:**

```json
{
  "success": true,
  "message": "Import entry deleted.",
  "data": null
}
```

**Errors:**

| Status | `detail` | Cause |
|---|---|---|
| `404` | `"Canvas not found."` | Wrong canvas ID or belongs to another user |
| `404` | `"Import entry not found."` | Wrong entry ID, already deleted, or entry does not belong to this canvas |

---

## Versioning behaviour

- `version` is assigned at upload time as `total_count_including_deleted + 1`
- Deleting an entry does **not** reuse its version number
- The "active" entry is always the one with the latest `created_at` among non-deleted entries — not necessarily the one with the highest `version` if uploads happened out of order

## Notes for migration

- No pagination on the list endpoint — all non-deleted entries are returned at once
- `data` (OCRPage array) is included in all responses including the list — it is not a separate detail call
- `GET /active` returns HTTP `200` with `data: null` (not `404`) when no entry exists — check `data` for null before rendering
- Timestamps are always UTC — no `X-Timezone` header support on these endpoints
