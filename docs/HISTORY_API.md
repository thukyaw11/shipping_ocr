# History API

Base path: `/api/v1/history`

All endpoints require `Authorization: Bearer <token>`.

Optional header: `X-Timezone: Asia/Bangkok` — localizes all `created_at` / `edited_at` timestamps.

---

## Data Models

### Canvas object (list view)

```json
{
  "id": "664f1a2b3c4d5e6f7a8b9c0d",
  "name": "Shipment May 2026",
  "status": "active",
  "pdf_count": 3,
  "created_at": "2026-04-24T10:00:00+07:00",
  "edited_at": "2026-04-24T12:30:00+07:00"
}
```

| Field | Type | Notes |
|---|---|---|
| `id` | string | MongoDB ObjectId as string |
| `name` | string | Canvas display name |
| `status` | string | Always `"active"` for non-deleted records |
| `pdf_count` | int | Count of non-deleted PDFs in this canvas |
| `created_at` | ISO 8601 | Localized if `X-Timezone` header provided |
| `edited_at` | ISO 8601 | Updated on any mutation to the canvas or its PDFs |

---

### OCRDocument object (inside canvas detail)

```json
{
  "id": "665a1b2c3d4e5f6a7b8c9d0e",
  "canvas_id": "664f1a2b3c4d5e6f7a8b9c0d",
  "sort_order": 0,
  "filename": "airwaybill.pdf",
  "total_pages": 3,
  "overall_confidence": 0.9412,
  "document_type": "MAWB",
  "status": "completed",
  "type": "pdf",
  "url": "https://r2.example.com/raw-files/...",
  "preview": "https://r2.example.com/raw-files/....jpg",
  "created_at": "2026-04-24T10:00:00+07:00",
  "edited_at": "2026-04-24T10:00:00+07:00",
  "data": [ <OCRPage> ],
  "checklists": [ <checklist object | null> ],
  "connections": [ <PageConnection> ],
  "cross_validation_results": [ <ValidationResult> ]
}
```

| Field | Type | Notes |
|---|---|---|
| `id` | string | MongoDB ObjectId as string |
| `canvas_id` | string | Parent canvas ID |
| `sort_order` | int | Upload order within the canvas, 0-based |
| `filename` | string | Original uploaded filename |
| `total_pages` | int | Total page count |
| `overall_confidence` | float \| null | Average OCR confidence across all pages (0–1) |
| `document_type` | string \| null | Most frequent page type: `MAWB` \| `HAWB` \| `IATA` \| `INVOICE` \| `CARGO_MANIFEST` \| `UNKNOWN` |
| `status` | string | `"completed"` or `"failed"` |
| `type` | string | Always `"pdf"` |
| `url` | string | Cloudflare R2 URL of the raw file |
| `preview` | string \| null | R2 URL of a preview image (first page) |
| `data` | OCRPage[] | Per-page OCR results |
| `checklists` | array | Flattened from `data[].checklist` — index matches page index |
| `connections` | PageConnection[] \| null | Directed links between related pages |
| `cross_validation_results` | ValidationResult[] | Rule check results |

---

### OCRPage object

```json
{
  "paged_idx": 1,
  "page_confidence": 0.9531,
  "page_type": "MAWB",
  "sub_page_type": "SYMRISE",
  "image_bbox": [0.0, 0.0, 2480.0, 3508.0],
  "raw_text": "MASTER AIR WAYBILL ...",
  "text_lines": [ <OCRLine> ],
  "checklist": { ... }
}
```

| Field | Type | Notes |
|---|---|---|
| `paged_idx` | int | 1-based page number |
| `page_confidence` | float \| null | Average OCR confidence for this page (0–1) |
| `page_type` | string \| null | Classified type: `MAWB` \| `HAWB` \| `IATA` \| `INVOICE` \| `CARGO_MANIFEST` \| `UNKNOWN` |
| `sub_page_type` | string \| null | Vendor/company name for INVOICE pages (e.g. `"SYMRISE"`) |
| `image_bbox` | float[4] | Page bounding box `[x1, y1, x2, y2]` in pixels |
| `raw_text` | string \| null | Full concatenated OCR text of this page |
| `text_lines` | OCRLine[] | Individual text line detections |
| `checklist` | object \| null | Extracted structured fields — shape depends on `page_type` (see below) |

---

### OCRLine object

```json
{
  "text": "MASTER AIR WAYBILL",
  "confidence": 0.9821,
  "bbox": [120.0, 45.0, 680.0, 72.0],
  "polygon": [[120.0, 45.0], [680.0, 45.0], [680.0, 72.0], [120.0, 72.0]]
}
```

| Field | Type | Notes |
|---|---|---|
| `text` | string | Recognized text content |
| `confidence` | float | OCR confidence score (0–1) |
| `bbox` | float[4] | Bounding box `[x1, y1, x2, y2]` in pixels |
| `polygon` | float[4][2] | Four corner points `[[x,y], ...]` |

---

### PageConnection object

Directed link between two pages that are related (e.g. MAWB → CARGO_MANIFEST).

```json
{
  "from": 1,
  "to": 3,
  "confidence": 0.95
}
```

| Field | Type | Notes |
|---|---|---|
| `from` | int | `paged_idx` of the source page |
| `to` | int | `paged_idx` of the linked page |
| `confidence` | float \| null | Match confidence |

Active connection rules: `MAWB→CARGO_MANIFEST`, `CARGO_MANIFEST→HAWB`, `HAWB→IATA`, `HAWB→INVOICE`, `INVOICE→INVOICE`

---

### ValidationResult object

```json
{
  "category": "MAWB vs Manifest",
  "rule_name": "MAWB vs Manifest: Total Weight",
  "status": "fail",
  "expected": 22,
  "actual": 65,
  "message": "Difference 43.0000 exceeds tolerance 0.5 (actual=65.0, expected=22.0)"
}
```

| Field | Type | Notes |
|---|---|---|
| `category` | string \| null | Grouping label — static string from rule config, not computed at runtime |
| `rule_name` | string | Human-readable rule name |
| `status` | string | `"pass"` \| `"fail"` \| `"skipped"` |
| `expected` | number \| string \| string[] \| null | Reference value from doc_b (scalar for numeric rules, sorted array for `list_match`) |
| `actual` | number \| string \| string[] \| null | Value found in doc_a |
| `message` | string | Empty string on pass; human-readable explanation on fail or skip |

`"skipped"` means required data was absent in the uploaded documents — not an error.

#### How `category` is assigned

`category` is a **static string hardcoded on each rule** in the server-side config (`cross_validation_config.py`). It is stamped onto the result after the evaluator runs — the evaluators themselves do not set it. It is purely a grouping/display hint.

#### Active categories and rules

| category | rule_name | What it checks |
|---|---|---|
| `"MAWB vs Manifest"` | `"MAWB vs Manifest: Total Weight"` | `MAWB.total_weight == CARGO_MANIFEST.total_weight` (tolerance 0.5) |
| `"MAWB vs Manifest"` | `"MAWB vs Manifest: Freight Numbers"` | `MAWB.freight_numbers[]` set-equals `CARGO_MANIFEST.hawb_list[].hawb_no` |
| `"MAWB vs HAWB"` | `"MAWB vs HAWB: Total Weight"` | `MAWB.total_weight == HAWB.total_weight` (tolerance 0.5) |
| `"IATA vs Manifest"` | `"IATA vs Manifest: Total Weight"` | `IATA.total_weight == CARGO_MANIFEST.total_weight` (tolerance 0.5) |
| `"HAWB vs Manifest"` | `"HAWB vs Manifest: Total Weight"` | sum of all `HAWB[].total_weight` == `CARGO_MANIFEST.total_weight` (tolerance 0.5) |
| `"HAWB vs MAWB"` | `"HAWB vs MAWB: Total Weight"` | sum of all `HAWB[].total_weight` == `MAWB.total_weight` (tolerance 0.5) |
| `"Manifest: Internal"` | `"Manifest: HAWB List Weight Sum vs Total Weight"` | sum of `CARGO_MANIFEST.hawb_list[].weight_kg` == `CARGO_MANIFEST.total_weight` (tolerance 0.5) |
| `"Manifest: Internal"` | `"Manifest: HAWB List Pieces Sum vs Total Pieces"` | sum of `CARGO_MANIFEST.hawb_list[].pcs` == `CARGO_MANIFEST.total_pcs` (exact) |

#### `expected` / `actual` types by rule type

| Rule type | `expected` | `actual` |
|---|---|---|
| `match` | number or string from doc_b | number or string from doc_a |
| `sum_match` | number from doc_b | summed number across all doc_a pages |
| `array_sum_match` | number from doc_b | summed number across array items |
| `list_match` | sorted string[] from doc_b | sorted string[] from doc_a |

---

### Checklist object shapes (per `page_type`)

Each page's `checklist` field is a flat object. The keys vary by document type:

| `page_type` | Typical checklist fields |
|---|---|
| `MAWB` | `awb_number`, `shipper`, `consignee`, `origin`, `destination`, `freight_numbers[]`, `total_weight`, `total_pieces`, `charges` |
| `HAWB` | `awb_number`, `shipper`, `consignee`, `origin`, `destination`, `weight`, `pieces` |
| `IATA` | `awb_number`, `flight_number`, `departure_date`, `origin`, `destination` |
| `INVOICE` | `invoice_number`, `invoice_date`, `seller`, `buyer`, `total_amount`, `currency`, `line_items[]` |
| `CARGO_MANIFEST` | `manifest_number`, `flight_number`, `hawb_list[]` |
| `UNKNOWN` | `null` |

---

## Endpoints

### List canvases

`GET /api/v1/history`

Returns the authenticated user's canvases sorted by `edited_at` descending (most recently modified first).

**Query params:**

| Param | Type | Default | Max | Notes |
|---|---|---|---|---|
| `page` | int | `1` | — | 1-based |
| `pageSize` | int | `20` | `100` | |
| `customer_name` | string | — | — | Filter: only canvases containing a PDF with this `sub_page_type` |

**Headers:**

| Header | Notes |
|---|---|
| `Authorization` | `Bearer <token>` — required |
| `X-Timezone` | e.g. `Asia/Bangkok` — optional, localizes timestamps |

**Response `200`:**

```json
{
  "success": true,
  "message": "Page 1",
  "data": [
    {
      "id": "664f1a2b3c4d5e6f7a8b9c0d",
      "name": "Shipment May 2026",
      "status": "active",
      "pdf_count": 3,
      "created_at": "2026-04-24T10:00:00+07:00",
      "edited_at": "2026-04-24T12:30:00+07:00"
    },
    {
      "id": "664f1a2b3c4d5e6f7a8b9c0e",
      "name": "Shipment April 2026",
      "status": "active",
      "pdf_count": 1,
      "created_at": "2026-04-10T08:00:00+07:00",
      "edited_at": "2026-04-10T09:15:00+07:00"
    }
  ]
}
```

No `total` field — end of list when `data.length < pageSize`.

---

### Get canvas detail

`GET /api/v1/history/{canvas_id}`

Returns the canvas metadata and all of its non-deleted PDFs with full enriched OCR data.

**Path params:**

| Param | Type | Notes |
|---|---|---|
| `canvas_id` | string | MongoDB ObjectId |

**Headers:**

| Header | Notes |
|---|---|
| `Authorization` | `Bearer <token>` — required |
| `X-Timezone` | e.g. `Asia/Bangkok` — optional, localizes timestamps |

**Response `200`:**

```json
{
  "success": true,
  "message": "Success",
  "data": {
    "id": "664f1a2b3c4d5e6f7a8b9c0d",
    "name": "Shipment May 2026",
    "status": "active",
    "created_at": "2026-04-24T10:00:00+07:00",
    "edited_at": "2026-04-24T12:30:00+07:00",
    "pdfs": [
      {
        "id": "665a1b2c3d4e5f6a7b8c9d0e",
        "canvas_id": "664f1a2b3c4d5e6f7a8b9c0d",
        "sort_order": 0,
        "filename": "airwaybill.pdf",
        "total_pages": 3,
        "overall_confidence": 0.9412,
        "document_type": "MAWB",
        "status": "completed",
        "type": "pdf",
        "url": "https://r2.example.com/raw-files/airwaybill.pdf",
        "preview": "https://r2.example.com/raw-files/airwaybill_preview.jpg",
        "created_at": "2026-04-24T10:00:00+07:00",
        "edited_at": "2026-04-24T10:00:00+07:00",
        "data": [
          {
            "paged_idx": 1,
            "page_confidence": 0.9531,
            "page_type": "MAWB",
            "sub_page_type": null,
            "image_bbox": [0.0, 0.0, 2480.0, 3508.0],
            "raw_text": "MASTER AIR WAYBILL ...",
            "text_lines": [
              {
                "text": "MASTER AIR WAYBILL",
                "confidence": 0.9821,
                "bbox": [120.0, 45.0, 680.0, 72.0],
                "polygon": [[120.0, 45.0], [680.0, 45.0], [680.0, 72.0], [120.0, 72.0]]
              }
            ],
            "checklist": {
              "awb_number": "123-45678901",
              "shipper": "ABC Corp",
              "consignee": "XYZ Ltd",
              "origin": "BKK",
              "destination": "SIN",
              "total_weight": 1500.0,
              "total_pieces": 10
            }
          }
        ],
        "checklists": [
          {
            "awb_number": "123-45678901",
            "shipper": "ABC Corp",
            "total_weight": 1500.0
          }
        ],
        "connections": [
          { "from": 1, "to": 3, "confidence": 0.95 }
        ],
        "cross_validation_results": [
          {
            "category": "MAWB vs Manifest",
            "rule_name": "MAWB vs Manifest: Total Weight",
            "status": "pass",
            "expected": 1500.0,
            "actual": 1500.0,
            "message": ""
          },
          {
            "category": "MAWB vs Manifest",
            "rule_name": "MAWB vs Manifest: Freight Numbers",
            "status": "pass",
            "expected": ["HWB001", "HWB002"],
            "actual": ["HWB001", "HWB002"],
            "message": ""
          },
          {
            "category": "IATA vs Manifest",
            "rule_name": "IATA vs Manifest: Total Weight",
            "status": "skipped",
            "expected": null,
            "actual": null,
            "message": "IATA: \"checklist.total_weight\" is missing or null"
          }
        ]
      }
    ]
  }
}
```

**Errors:**

| Status | `detail` | Cause |
|---|---|---|
| `400` | `"Invalid canvas ID format."` | Malformed ObjectId |
| `404` | `"Canvas not found."` | Wrong ID or belongs to another user |

---

### Rename canvas

`PATCH /api/v1/history/{canvas_id}/name`

Updates the display name of a canvas.

**Path params:**

| Param | Type | Notes |
|---|---|---|
| `canvas_id` | string | MongoDB ObjectId |

**Headers:**

| Header | Notes |
|---|---|
| `Authorization` | `Bearer <token>` — required |

**Request body:**

```json
{ "name": "New Canvas Name" }
```

| Field | Type | Constraints |
|---|---|---|
| `name` | string | Required. 1–512 characters. Leading/trailing whitespace is stripped. |

**Response `200`:**

Returns the full updated canvas object.

```json
{
  "success": true,
  "message": "Canvas renamed",
  "data": {
    "id": "664f1a2b3c4d5e6f7a8b9c0d",
    "name": "New Canvas Name",
    "status": "active",
    "created_at": "2026-04-24T10:00:00+00:00",
    "edited_at": "2026-04-27T08:15:00+00:00"
  }
}
```

**Errors:**

| Status | `detail` | Cause |
|---|---|---|
| `400` | `"Invalid canvas ID format."` | Malformed ObjectId |
| `404` | `"Canvas not found."` | Wrong ID or belongs to another user |
| `422` | validation error | `name` missing, empty, or exceeds 512 chars |

---

### Delete canvas

`DELETE /api/v1/history/{canvas_id}`

Soft-deletes a canvas. The canvas and all its PDFs are hidden from all subsequent queries but remain in the database.

**Path params:**

| Param | Type | Notes |
|---|---|---|
| `canvas_id` | string | MongoDB ObjectId |

**Headers:**

| Header | Notes |
|---|---|
| `Authorization` | `Bearer <token>` — required |

**Response `200`:**

```json
{
  "success": true,
  "message": "Canvas deleted",
  "data": {}
}
```

**Errors:**

| Status | `detail` | Cause |
|---|---|---|
| `400` | `"Invalid canvas ID format."` | Malformed ObjectId |
| `404` | `"Canvas not found."` | Wrong ID or belongs to another user |

**Notes:**
- This is a **soft delete** — sets `is_deleted: true` and records `deleted_at` timestamp. No data is permanently removed.
- Deleted canvases no longer appear in `GET /history` or `GET /history/{canvas_id}`.
- PDFs inside the canvas are also excluded from all queries after deletion.

---

## Notes for migration

- PDFs inside `data.pdfs[]` are sorted by `sort_order` ascending (upload order), **not** by date.
- `checklists[i]` always corresponds to `data[i]` at the same index — both arrays have the same length.
- `connections` and `cross_validation_results` are computed at **query time**, not stored from the original scan.
- `overall_confidence` and `page_confidence` may be `null` on very old records — they are back-filled at query time from `text_lines[].confidence` if missing.
- Soft-deleted PDFs are excluded from `pdfs[]` and not counted in `pdf_count`.
