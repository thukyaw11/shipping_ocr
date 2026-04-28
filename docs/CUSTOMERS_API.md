# Customers API

Base path: `/api/v1/customers`

All endpoints require `Authorization: Bearer <token>`.

---

## Data Models

### Customer object

```json
{
  "id": "string",
  "name": "string",
  "priority": "high" | "medium" | "low",
  "location": "string",
  "address": "string",
  "emails": ["string"],
  "profile_url": "string | null",
  "hs_code_data": [ <HSCodeData> ],
  "created_at": "2026-04-24T10:00:00",
  "updated_at": "2026-04-24T10:00:00"
}
```

| Field | Type | Notes |
|---|---|---|
| `id` | string | UUID |
| `name` | string | 1–255 chars |
| `priority` | string | `"high"` \| `"medium"` \| `"low"` |
| `location` | string | Office or city, max 255 chars, defaults to `""` |
| `address` | string | Full address, max 512 chars, defaults to `""` |
| `emails` | string[] | Contact email list, defaults to `[]` |
| `profile_url` | string \| null | R2 URL set after profile pic upload |
| `hs_code_data` | HSCodeData[] | HS code entries, defaults to `[]` |
| `created_at` | ISO 8601 datetime | UTC |
| `updated_at` | ISO 8601 datetime | UTC |

### HSCodeData object

```json
{
  "product": "string",
  "definition": "string",
  "code": "string",
  "duty": "string",
  "license": "string",
  "remark": "string"
}
```

| Field | Type | Max length |
|---|---|---|
| `product` | string | 128 |
| `definition` | string | 2000 |
| `code` | string | 128 |
| `duty` | string | 255 |
| `license` | string | 255 |
| `remark` | string | 2000 |

---

## Endpoints

### List customers

`GET /api/v1/customers`

Returns a flat array of the authenticated user's customers, sorted by `created_at` descending.

**Query params:**

| Param | Type | Default | Max | Notes |
|---|---|---|---|---|
| `page` | int | `1` | — | 1-based |
| `limit` | int | `50` | `100` | Items per page |
| `priority` | string | — | — | Filter: `high` \| `medium` \| `low` |

**Response `200`:**

```json
{
  "success": true,
  "message": "Success",
  "data": [
    {
      "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "name": "SYMRISE",
      "priority": "high",
      "location": "Sathorn",
      "address": "123 Sathorn Rd, Bangkok",
      "emails": ["contact@symrise.com"],
      "profile_url": null,
      "hs_code_data": [],
      "created_at": "2026-04-24T10:00:00",
      "updated_at": "2026-04-24T10:00:00"
    }
  ]
}
```

No `total` field is returned. End of list is detected when `data.length < limit`.

---

### List customers grouped by priority

`GET /api/v1/customers/grouped`

Returns all customers organized into three priority buckets. No pagination — returns all records.

**Response `200`:**

```json
{
  "success": true,
  "message": "Customers grouped by priority",
  "data": {
    "high": [ <Customer>, ... ],
    "medium": [ <Customer>, ... ],
    "low": [ <Customer>, ... ]
  }
}
```

---

### Get priority sections

`GET /api/v1/customers/priority-sections`

Returns the display labels for priority levels (static, does not hit the database).

**Response `200`:**

```json
{
  "success": true,
  "message": "Success",
  "data": [
    { "key": "high",   "label": "High priority" },
    { "key": "medium", "label": "Medium priority" },
    { "key": "low",    "label": "Low priority" }
  ]
}
```

---

### Get customer by ID

`GET /api/v1/customers/{customer_id}`

**Path params:**

| Param | Type | Notes |
|---|---|---|
| `customer_id` | string | UUID |

**Response `200`:**

```json
{
  "success": true,
  "message": "Success",
  "data": {
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "name": "SYMRISE",
    "priority": "high",
    "location": "Sathorn",
    "address": "123 Sathorn Rd, Bangkok",
    "emails": ["contact@symrise.com"],
    "profile_url": null,
    "hs_code_data": [],
    "created_at": "2026-04-24T10:00:00",
    "updated_at": "2026-04-24T10:00:00"
  }
}
```

**Errors:**

| Status | `detail` | Cause |
|---|---|---|
| `404` | `"Customer not found"` | Wrong ID or belongs to another user |

---

### Create customer

`POST /api/v1/customers`

**Request body:**

```json
{
  "name": "SYMRISE",
  "priority": "high",
  "location": "Sathorn",
  "address": "123 Sathorn Rd, Bangkok",
  "emails": ["contact@symrise.com"],
  "hs_code_data": []
}
```

| Field | Required | Type | Rules |
|---|---|---|---|
| `name` | Yes | string | 1–255 chars |
| `priority` | Yes | string | `"high"` \| `"medium"` \| `"low"` |
| `location` | No | string | max 255, defaults to `""` |
| `address` | No | string | max 512, defaults to `""` |
| `emails` | No | string[] | defaults to `[]` |
| `profile_url` | No | string | defaults to `null` |
| `hs_code_data` | No | HSCodeData[] | defaults to `[]` |

**Response `200`:**

```json
{
  "success": true,
  "message": "Customer created successfully",
  "data": {
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "name": "SYMRISE",
    "priority": "high",
    "location": "Sathorn",
    "address": "123 Sathorn Rd, Bangkok",
    "emails": ["contact@symrise.com"],
    "profile_url": null,
    "hs_code_data": [],
    "created_at": "2026-04-24T10:00:00",
    "updated_at": "2026-04-24T10:00:00"
  }
}
```

---

### Update customer

`PUT /api/v1/customers/{customer_id}`

All fields are optional — only send fields you want to change.

**Request body:**

```json
{
  "name": "SYMRISE THAILAND",
  "priority": "medium",
  "location": "Silom",
  "address": "456 Silom Rd, Bangkok",
  "emails": ["new@symrise.com"],
  "hs_code_data": []
}
```

**Response `200`:**

```json
{
  "success": true,
  "message": "Customer updated successfully",
  "data": { <updated Customer object> }
}
```

**Errors:**

| Status | `detail` | Cause |
|---|---|---|
| `404` | `"Customer not found"` | Wrong ID or belongs to another user |
| `422` | validation error | Unknown fields sent (extra fields are forbidden) |

---

### Delete customer

`DELETE /api/v1/customers/{customer_id}`

Permanently deletes the customer (hard delete).

**Response `200`:**

```json
{
  "success": true,
  "message": "Customer deleted successfully",
  "data": { "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890" }
}
```

**Errors:**

| Status | `detail` | Cause |
|---|---|---|
| `404` | `"Customer not found"` | Wrong ID or belongs to another user |

---

### Upload profile picture

`POST /api/v1/customers/{customer_id}/profile-pic`

`multipart/form-data` — field name: `file`

| Rule | Value |
|---|---|
| Accepted types | `image/jpeg`, `image/png`, `image/webp` |
| Max file size | 5 MB |

**Response `200`:**

```json
{
  "success": true,
  "message": "Profile picture updated successfully",
  "data": { <updated Customer object with profile_url set> }
}
```

**Errors:**

| Status | `detail` | Cause |
|---|---|---|
| `400` | `"Only JPEG, PNG, and WebP images are allowed"` | Wrong content type |
| `400` | `"File size exceeds 5 MB limit"` | File too large |
| `404` | `"Customer not found"` | Wrong ID |

---

## HS Code Endpoints

### Get HS codes (paginated)

`GET /api/v1/customers/{customer_id}/hs-codes`

| Param | Type | Default | Max | Notes |
|---|---|---|---|---|
| `page` | int | `1` | — | 1-based |
| `pageSize` | int | `20` | `100` | |
| `search` | string | — | — | Matches `code` or `product` (case-insensitive) |

**Response `200`:**

```json
{
  "success": true,
  "message": "Page 1",
  "data": [
    {
      "product": "Fragrance Compound",
      "definition": "สารประกอบน้ำหอม",
      "code": "3302.10",
      "duty": "5%",
      "license": "—",
      "remark": ""
    }
  ]
}
```

No `total` field. End of list when `data.length < pageSize`.

---

### Add single HS code

`POST /api/v1/customers/{customer_id}/hs-codes`

**Request body:**

```json
{
  "product": "Fragrance Compound",
  "definition": "สารประกอบน้ำหอม",
  "code": "3302.10",
  "duty": "5%",
  "license": "—",
  "remark": ""
}
```

**Response `200`:**

```json
{
  "success": true,
  "message": "HS code added successfully",
  "data": { <full Customer object with updated hs_code_data> }
}
```

---

### Bulk import HS codes from Excel

`POST /api/v1/customers/{customer_id}/hs-codes/upload`

`multipart/form-data` — field name: `file` — `.xlsx` only.

**Replaces** all existing HS codes for the customer.

**Excel format:**

- Row 1–2: ignored (skip header rows)
- Row 3: header row with these column names (case-insensitive):

| Column | Required | Notes |
|---|---|---|
| `product` | Yes | |
| `thai_definition` | Yes | Mapped to `definition` field |
| `h_s_code` | Yes | Mapped to `code` field |
| `duty` | Yes | |
| `license` | Yes | |
| `remark` | Yes | |
| `flight` | No | Optional column |

**Response `200`:**

```json
{
  "success": true,
  "message": "42 HS codes imported successfully",
  "data": { <full Customer object with replaced hs_code_data> }
}
```

**Errors:**

| Status | `detail` | Cause |
|---|---|---|
| `400` | `"Only .xlsx files are supported"` | Wrong file type |
| `400` | `"Missing required columns: ..."` | Header row missing required columns |
| `404` | `"Customer not found"` | Wrong ID |
