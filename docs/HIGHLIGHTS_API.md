# Highlights API Integration

This guide explains how to integrate project-scoped user highlights with the backend.

Base URL examples:
- Local: `http://localhost:8000`
- API prefix: `/api/v1`

Full base: `http://localhost:8000/api/v1`

---

## Auth Requirement

All highlight endpoints require JWT auth:

```http
Authorization: Bearer <access_token>
```

`access_token` comes from your auth endpoints (`/api/v1/auth/login`, `/api/v1/auth/register`, or `/api/v1/auth/google`).

---

## Data Model

Backend returns highlights in this shape:

```ts
type Highlight = {
  id: string
  projectId: string
  pageIndex: number
  left: number
  top: number
  width: number
  height: number
  color?: string
  note?: string
  createdAt: string
  updatedAt: string
}
```

Coordinates are percentages (0-100), so they are resolution-independent in the viewer.

---

## Endpoints

### 1) Get highlights for a project

`GET /api/v1/history/{project_id}/highlights`

Example:

```bash
curl -X GET "http://localhost:8000/api/v1/history/67fabc1234abcd1234abcd12/highlights" \
  -H "Authorization: Bearer <access_token>"
```

Success response:

```json
{
  "success": true,
  "message": "Success",
  "data": [
    {
      "id": "1e6d3155-80f8-45c1-8535-f2b94fcfcae9",
      "projectId": "67fabc1234abcd1234abcd12",
      "pageIndex": 0,
      "left": 13.2,
      "top": 24.6,
      "width": 18.4,
      "height": 3.2,
      "color": "#f59e0b",
      "note": "Check shipper name",
      "createdAt": "2026-03-31T07:31:20.123456",
      "updatedAt": "2026-03-31T07:31:20.123456"
    }
  ]
}
```

If project is not found or not owned by current user: `404`.

---

### 2) Replace full highlight set

`PUT /api/v1/history/{project_id}/highlights`

This endpoint replaces all highlights for that project/user pair with the provided array.

Request body:

```json
{
  "highlights": [
    {
      "id": "1e6d3155-80f8-45c1-8535-f2b94fcfcae9",
      "pageIndex": 0,
      "left": 13.2,
      "top": 24.6,
      "width": 18.4,
      "height": 3.2,
      "color": "#f59e0b",
      "note": "Check shipper name"
    },
    {
      "pageIndex": 1,
      "left": 55.1,
      "top": 16.0,
      "width": 22.0,
      "height": 4.8,
      "color": "#22c55e"
    }
  ]
}
```

Notes:
- `id` is optional. If missing, backend auto-generates one.
- `createdAt`/`updatedAt` are normalized by backend.
- Existing IDs keep their original `createdAt`.

Success response:

```json
{
  "success": true,
  "message": "Highlights replaced",
  "data": [
    {
      "id": "1e6d3155-80f8-45c1-8535-f2b94fcfcae9",
      "projectId": "67fabc1234abcd1234abcd12",
      "pageIndex": 0,
      "left": 13.2,
      "top": 24.6,
      "width": 18.4,
      "height": 3.2,
      "color": "#f59e0b",
      "note": "Check shipper name",
      "createdAt": "2026-03-31T07:31:20.123456",
      "updatedAt": "2026-03-31T08:10:11.901245"
    }
  ]
}
```

---

## Frontend Integration Pattern

1. On project open:
   - call `GET /history/{project_id}/highlights`
   - render all boxes.

2. On highlight create/update/delete:
   - update local array in state.
   - debounce-save by calling `PUT /history/{project_id}/highlights` with full array.

3. On save failure:
   - show toast/error.
   - optionally re-fetch from `GET` to resolve conflicts.

---

## Minimal JS helper

```ts
const API = "http://localhost:8000/api/v1";

export async function fetchHighlights(projectId: string, token: string) {
  const res = await fetch(`${API}/history/${projectId}/highlights`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`GET highlights failed: ${res.status}`);
  const json = await res.json();
  return json.data ?? [];
}

export async function saveHighlights(projectId: string, token: string, highlights: any[]) {
  const res = await fetch(`${API}/history/${projectId}/highlights`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ highlights }),
  });
  if (!res.ok) throw new Error(`PUT highlights failed: ${res.status}`);
  const json = await res.json();
  return json.data ?? [];
}
```

---

## Error Codes

- `401` invalid/missing JWT
- `404` project not found (or not owned by user)
- `400` invalid project id format
- `422` invalid payload (e.g. negative pageIndex or out-of-range coordinates)
