# Authentication API (frontend integration)

Base path: **`/api/v1`**. All JSON bodies use **`Content-Type: application/json`** unless noted.

Replace `{API_BASE}` with your environment (e.g. `http://localhost:8000`).

---

## Summary

| Action | Method | Path | Auth |
|--------|--------|------|------|
| Register | `POST` | `/api/v1/auth/register` | None |
| Login (JSON) | `POST` | `/api/v1/auth/login` | None |
| Login (OAuth2 form) | `POST` | `/api/v1/auth/token` | None |
| Google sign-in / register | `POST` | `/api/v1/auth/google` | None |
| OCR / history | `*` | `/api/v1/ocr/*`, `/api/v1/history/*` | **Bearer JWT** |

After login or register, store **`access_token`** and send it on protected requests:

```http
Authorization: Bearer <access_token>
```

---

## Google sign-in (ID token)

Use after the frontend obtains a **Google ID token** (Sign-In with Google, One Tap, or GIS). The backend verifies the token with Google and issues the same app JWT as email/password flows.

**Server env:** set **`GOOGLE_CLIENT_ID`** to the same OAuth 2.0 **Web client ID** used in the frontend (Google Cloud Console).

**`POST {API_BASE}/api/v1/auth/google`**

Request body (send **either** field; One Tap often uses `credential`):

```json
{
  "id_token": "<google_jwt>"
}
```

or

```json
{
  "credential": "<google_jwt>"
}
```

Success **`200 OK`** (same shape as register):

```json
{
  "user": {
    "id": "<mongodb_objectid_string>",
    "email": "user@gmail.com",
    "created_at": "2026-03-30T12:00:00"
  },
  "access_token": "<app_jwt>",
  "token_type": "bearer"
}
```

Behaviour:

- **New Google user:** creates a user with `email` and links `google_sub`; no password.
- **Returning Google user:** finds by `google_sub` and returns a token.
- **Existing email/password user, first Google login:** links Google to the same account if the email matches and `google_sub` was not set.

**`401`** — invalid token, unverified email, or misconfigured Google client.

**`409`** — rare conflict if the email is already tied to a different Google subject.

**`503`** — `GOOGLE_CLIENT_ID` not set, or `google-auth` not installed.

Email/password login for accounts that only use Google returns **`401`** with detail *This account uses Google sign-in*.

---

## Register

**`POST {API_BASE}/api/v1/auth/register`**

Request body:

```json
{
  "email": "user@example.com",
  "password": "minimum8chars"
}
```

Rules:

- `email`: trimmed, stored lowercase; basic format validation on the server.
- `password`: length **8–128** characters.

Success **`201 Created`**:

```json
{
  "user": {
    "id": "<mongodb_objectid_string>",
    "email": "user@example.com",
    "created_at": "2026-03-30T12:00:00"
  },
  "access_token": "<jwt>",
  "token_type": "bearer"
}
```

The client can treat this like a login: persist `access_token` and use it immediately for protected routes.

**`409 Conflict`** — email already registered:

```json
{ "detail": "Email already registered" }
```

**`422 Unprocessable Entity`** — validation (e.g. invalid email, password too short). FastAPI returns a `detail` array of field errors.

---

## Login (recommended for SPAs)

**`POST {API_BASE}/api/v1/auth/login`**

Request body:

```json
{
  "email": "user@example.com",
  "password": "secret"
}
```

Success **`200 OK`**:

```json
{
  "access_token": "<jwt>",
  "token_type": "bearer"
}
```

**`401 Unauthorized`**:

```json
{ "detail": "Incorrect email or password" }
```

---

## Login (OAuth2 password form)

**`POST {API_BASE}/api/v1/auth/token`**

Use when integrating with libraries that expect OAuth2 **application/x-www-form-urlencoded**:

- `username` — **must be the user’s email** (not a separate username).
- `password` — account password.

Example:

```http
POST /api/v1/auth/token
Content-Type: application/x-www-form-urlencoded

username=user%40example.com&password=secret
```

Success response shape matches login: `access_token`, `token_type`.

If the server is configured with optional dev credentials (`AUTH_DEV_USERNAME` / `AUTH_DEV_PASSWORD`), the same endpoint may accept those as `username` / `password` for non-production tooling only.

---

## JWT payload (for debugging / optional client use)

The access token is a **JWT** signed with **`HS256`**. Decoding the payload (without verifying) is optional; the API does not require the client to parse it.

Typical claims:

| Claim | Meaning |
|-------|---------|
| `sub` | User id (MongoDB `ObjectId` as string) |
| `email` | User email |
| `iat` | Issued-at (unix seconds) |
| `exp` | Expiry (unix seconds) |

Expiry is controlled server-side (`JWT_ACCESS_TOKEN_EXPIRE_MINUTES`, default 1440 minutes).

---

## User-scoped OCR history

OCR jobs are stored with **`user_id`** (JWT `sub`). **`GET /api/v1/history`** and **`GET /api/v1/history/{doc_id}`** only return documents for the **authenticated user**. Another user’s id returns **404** on the detail route.

Documents created before `user_id` was added have no owner filter match and will not appear in lists (re-upload or backfill if needed).

---

## Protected routes

These routes require a valid Bearer token:

- `POST /api/v1/ocr/surya` (and any other OCR routes on the same router)
- `GET /api/v1/history`
- `GET /api/v1/history/{doc_id}`

Example:

```http
POST /api/v1/ocr/surya
Authorization: Bearer <access_token>
Content-Type: multipart/form-data
```

**`401 Unauthorized`** — missing, invalid, or expired token:

```json
{ "detail": "Invalid or expired token" }
```

**`503 Service Unavailable`** — server missing `JWT_SECRET_KEY`:

```json
{ "detail": "JWT is not configured: set JWT_SECRET_KEY in the environment." }
```

---

## CORS

The API uses `CORSMiddleware` with origins from server config (`ALLOW_ORIGINS`). The frontend origin must be allowed, and preflight must include:

- `Authorization`
- `Content-Type`

(if you send custom headers such as `X-Timezone`, include those in the server allow list as well).

---

## Minimal frontend flow

1. **Register** or **login** → receive `access_token`.
2. Store the token (memory, `sessionStorage`, or secure storage—follow your security policy).
3. Attach **`Authorization: Bearer <access_token>`** to every **`/api/v1/ocr/*`** and **`/api/v1/history/*`** call.
4. On **401**, clear the token and send the user to login (or refresh, if you add refresh tokens later).

---

## Example: `fetch`

```javascript
const API_BASE = 'http://localhost:8000';

async function register(email, password) {
  const res = await fetch(`${API_BASE}/api/v1/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw await res.json();
  return res.json();
}

async function login(email, password) {
  const res = await fetch(`${API_BASE}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw await res.json();
  return res.json();
}

async function uploadOcr(file, accessToken) {
  const form = new FormData();
  form.append('file', file);
  const res = await fetch(`${API_BASE}/api/v1/ocr/surya`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${accessToken}` },
    body: form,
  });
  if (!res.ok) throw await res.json();
  return res.json();
}
```

---

## OpenAPI / Swagger

Interactive docs (when enabled): `{API_BASE}/docs` — schemas for **Authentication**, **OCR Processing**, and **OCR History** match this document.
