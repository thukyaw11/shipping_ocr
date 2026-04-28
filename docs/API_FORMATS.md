# API Request & Response Formats

## Authentication

All protected endpoints require a JWT bearer token.

```
Authorization: Bearer <jwt_token>
```

Optional header for timezone-aware timestamps:

```
X-Timezone: Asia/Bangkok
```

---

## Standard Response Envelope

Every endpoint returns this wrapper — success and failure alike.

```json
{
  "success": true,
  "message": "Success",
  "data": {}
}
```

| Field | Type | Notes |
|---|---|---|
| `success` | boolean | `true` on 2xx, `false` on errors |
| `message` | string | Human-readable status, e.g. `"Success"`, `"Page 1"` |
| `data` | any | The actual payload; `null` on some errors |

---

## Error Responses

### HTTP exceptions (400, 401, 403, 404)

FastAPI default format — **not** wrapped in `ApiResponse`:

```json
{
  "detail": "Canvas not found."
}
```

### Validation errors (422)

Wrapped in `ApiResponse`:

```json
{
  "success": false,
  "message": "body.name: field required; body.priority: value is not a valid enumeration member",
  "data": {
    "validation_errors": [
      {
        "location": ["body", "name"],
        "message": "Field required",
        "type": "missing"
      }
    ]
  }
}
```

---

## Pagination

### Offset formula (all endpoints)

```
skip = (page - 1) * page_size
```

### Pattern A — Envelope pagination

`data` is an object with `total`, `page`, `page_size`, and `items`.

Used by: `GET /api/v1/scan-logs`

**Query params:**

| Param | Type | Default | Max | Notes |
|---|---|---|---|---|
| `page` | int | `1` | — | 1-based |
| `page_size` | int | `20` | `100` | |
| `status` | string | — | — | Optional: `success` \| `failed` |

**Response:**

```json
{
  "success": true,
  "message": "Page 1",
  "data": {
    "total": 84,
    "page": 1,
    "page_size": 20,
    "items": [
      { "id": "...", "status": "success", "created_at": "2026-04-24T10:00:00+07:00" }
    ]
  }
}
```

---

### Pattern B — Simple array pagination

`data` is a plain array. **No `total` field** — detect end-of-list when `data.length < pageSize`.

#### `GET /api/v1/canvases`

| Param | Type | Default | Max | Notes |
|---|---|---|---|---|
| `page` | int | `1` | — | |
| `pageSize` | int | `20` | `100` | |
| `customer_name` | string | — | — | Filter by sub_page_type |

**Response:**

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
    }
  ]
}
```

#### `GET /api/v1/customers`

| Param | Type | Default | Max | Notes |
|---|---|---|---|---|
| `page` | int | `1` | — | |
| `limit` | int | `50` | `100` | |
| `priority` | string | — | — | `high` \| `medium` \| `low` |

#### `GET /api/v1/customers/{customer_id}/hs-codes`

| Param | Type | Default | Max | Notes |
|---|---|---|---|---|
| `page` | int | `1` | — | |
| `pageSize` | int | `20` | `100` | |
| `search` | string | — | — | Search by code or product name |

---

## Pagination Quick Reference

| Endpoint | Style | Page param | Size param | Has `total`? |
|---|---|---|---|---|
| `GET /scan-logs` | Envelope (`data.items`) | `page` | `page_size` | Yes |
| `GET /canvases` | Array (`data[]`) | `page` | `pageSize` | No |
| `GET /customers` | Array (`data[]`) | `page` | `limit` | No |
| `GET /customers/{id}/hs-codes` | Array (`data[]`) | `page` | `pageSize` | No |

> **Note:** The size param name is inconsistent across endpoints — `page_size`, `pageSize`, and `limit` are all used.

---

## Request Body Formats

### Rename canvas
```json
{ "name": "string" }
```

### Update page type
```json
{ "page_type": "string" }
```

### Update sub-page type
```json
{ "sub_page_type": "string" }
```

### Create customer
```json
{
  "name": "string",
  "priority": "high" | "medium" | "low",
  "location": "string",
  "address": "string",
  "emails": ["string"],
  "hs_code_data": []
}
```

### Update customer (all fields optional)
```json
{
  "name": "string",
  "priority": "high" | "medium" | "low",
  "location": "string",
  "address": "string",
  "emails": ["string"],
  "hs_code_data": []
}
```

### Replace highlights
```json
{
  "highlights": [
    {
      "id": "string (optional, generated if omitted)",
      "pageIndex": 0,
      "left": 10.5,
      "top": 20.0,
      "width": 30.0,
      "height": 5.0,
      "color": "#ff0000",
      "note": "string"
    }
  ]
}
```

### File uploads

Use `multipart/form-data` with field name `file`.

| Endpoint | Accepted types | Max size |
|---|---|---|
| `POST /customers/{id}/profile-pic` | `image/jpeg`, `image/png`, `image/webp` | 5 MB |
| `POST /customers/{id}/hs-codes/upload` | `.xlsx` only | — |

---

## OCR Upload

`POST /api/v1/ocr/surya` — `multipart/form-data`

The file is uploaded as part of the form. Response wraps a full `OCRDocument` in `data`.

---

## ID Format

All MongoDB `_id` fields are serialized as `id` (string) in responses. The raw `_id` is never returned.

```json
{ "id": "664f1a2b3c4d5e6f7a8b9c0d" }
```

## Timestamps

All `created_at` / `edited_at` / `updated_at` fields are ISO 8601 strings. If `X-Timezone` header is provided, timestamps are localized; otherwise UTC.

```
2026-04-24T10:00:00+07:00
```
