# Authentication Flow

Base path: `/api/v1/auth`

> Auth endpoints do **not** use the standard `ApiResponse` envelope. They return the token/user object directly.

---

## 1. Register

`POST /api/v1/auth/register`

### Request

```json
{
  "email": "user@example.com",
  "password": "mypassword123"
}
```

| Field | Type | Rules |
|---|---|---|
| `email` | string | 3–254 chars, valid email format, lowercased |
| `password` | string | 8–128 chars |

### Response `201`

```json
{
  "user": {
    "id": "664f1a2b3c4d5e6f7a8b9c0d",
    "email": "user@example.com",
    "created_at": "2026-04-24T10:00:00"
  },
  "access_token": "<jwt>",
  "token_type": "bearer"
}
```

### Errors

| Status | `detail` | Cause |
|---|---|---|
| `409` | `"Email already registered"` | Duplicate email |
| `422` | validation error | Invalid email format or password too short |

---

## 2. Login (JSON)

`POST /api/v1/auth/login`

### Request

```json
{
  "email": "user@example.com",
  "password": "mypassword123"
}
```

| Field | Type | Rules |
|---|---|---|
| `email` | string | valid email, lowercased |
| `password` | string | 1–128 chars |

### Response `200`

```json
{
  "access_token": "<jwt>",
  "token_type": "bearer"
}
```

### Errors

| Status | `detail` | Cause |
|---|---|---|
| `401` | `"Incorrect email or password"` | User not found or wrong password |
| `401` | `"This account uses Google sign-in"` | Account has no password (Google-only) |

---

## 3. Login (OAuth2 form) — Swagger UI

`POST /api/v1/auth/token`

Used by the Swagger `/docs` "Authorize" button. Sends `application/x-www-form-urlencoded`.

### Request

```
Content-Type: application/x-www-form-urlencoded

username=user%40example.com&password=mypassword123
```

| Field | Notes |
|---|---|
| `username` | The user's email address |
| `password` | The user's password |

### Response `200`

```json
{
  "access_token": "<jwt>",
  "token_type": "bearer"
}
```

### Errors

| Status | `detail` | Cause |
|---|---|---|
| `401` | `"Incorrect email or password"` | Wrong credentials |

---

## 4. Google Sign-In / Register

`POST /api/v1/auth/google`

Handles both first-time registration and returning users in a single call. Send the Google ID token obtained from the Google Sign-In SDK on the client.

### Request

```json
{
  "id_token": "<google_id_token>"
}
```

`credential` is accepted as an alias for `id_token` (both field names work):

```json
{
  "credential": "<google_id_token>"
}
```

| Field | Rules |
|---|---|
| `id_token` / `credential` | At least one must be provided, min 10 chars |

### Response `200`

Same shape as Register:

```json
{
  "user": {
    "id": "664f1a2b3c4d5e6f7a8b9c0d",
    "email": "user@gmail.com",
    "created_at": "2026-04-24T10:00:00"
  },
  "access_token": "<jwt>",
  "token_type": "bearer"
}
```

### Errors

| Status | `detail` | Cause |
|---|---|---|
| `401` | `"Invalid Google token"` | Token failed Google verification |
| `409` | `"Email is already linked to another Google account"` | Google sub conflict |

---

## Using the Token

Include the `access_token` as a Bearer token in the `Authorization` header on every protected request:

```
Authorization: Bearer <access_token>
```

### Token payload (JWT claims)

| Claim | Value |
|---|---|
| `sub` | MongoDB user `_id` (string) |
| `email` | User's email address |
| `iat` | Issued-at timestamp (Unix) |
| `exp` | Expiry timestamp (Unix) |

### Token expiry

Default: **24 hours** (1440 minutes). Configured via `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` env var.

Algorithm: **HS256**

---

## Token errors (protected endpoints)

| Status | `detail` | Cause |
|---|---|---|
| `401` | `"Invalid or expired token"` | Bad signature, malformed, or expired JWT |
| `401` | `"Invalid token: missing subject"` | JWT `sub` claim is empty |
| `403` | (auto, from HTTPBearer) | No `Authorization` header sent |
| `503` | `"JWT is not configured..."` | `JWT_SECRET_KEY` env var not set |

---

## Flow Diagram

```
Client                              Server
  │                                   │
  │── POST /auth/register ──────────► │  hash password, insert user
  │◄─ { user, access_token } ──────── │
  │                                   │
  │── POST /auth/login ─────────────► │  verify password
  │◄─ { access_token } ────────────── │
  │                                   │
  │── POST /auth/google ────────────► │  verify Google ID token
  │   { id_token: "..." }             │  find-or-create user
  │◄─ { user, access_token } ──────── │
  │                                   │
  │── GET /api/v1/... ──────────────► │
  │   Authorization: Bearer <token>   │  verify_jwt → extract sub
  │◄─ ApiResponse[...] ────────────── │
```
