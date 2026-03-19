# Frontend: OCR stream API and progress UI

Technical guide for consuming the OCR streaming endpoint and showing progress (percentage and state) in the UI.

---

## 1. Endpoint

| Method | URL | Body | Query |
|--------|-----|------|--------|
| `POST` | `{API_BASE}/ocr/stream` | `multipart/form-data` with `file` (image or PDF) | `provider=ollama` \| `provider=gemini` (optional, default `ollama`) |

**Example**

```http
POST /ocr/stream?provider=gemini
Content-Type: multipart/form-data; boundary=----WebKitFormBoundary...
```

```js
const formData = new FormData();
formData.append('file', file); // File from <input type="file"> or drag-and-drop

const url = `${API_BASE}/ocr/stream?provider=${provider}`;
const response = await fetch(url, { method: 'POST', body: formData });
```

- Do **not** use `EventSource` (GET only). Use `fetch()` and read `response.body` as a stream.
- Do **not** set a short timeout; OCR + LLM can take 1–2+ minutes per page.
- CORS: backend must allow your origin (e.g. `http://localhost:5173`).

---

## 2. Response: Server-Sent Events (SSE)

- **Content-Type:** `text/event-stream`
- **Body:** sequence of SSE events. Each event is one or more lines; the payload is on a line starting with `data: ` (the rest is JSON). Events are separated by a blank line (`\n\n`).

**Example raw chunk (one page)**

```
data: {"type":"start","total_pages":1,"filename":"bill.png","provider":"gemini","state":"Starting…"}

data: {"type":"progress","page":1,"total_pages":1,"message":"Processing bill.png…","page_percent":0,"state":"Extracting text…"}

data: {"type":"progress","page":1,"total_pages":1,"message":"Extracting data from bill.png…","page_percent":30,"state":"Extracting data…"}

data: {"type":"progress","page":1,"total_pages":1,"message":"Saving page 1…","page_percent":60,"state":"Saving…"}

data: {"type":"page","page":1,"total_pages":1,"saved_to":"outputs2/bill_gemini-2.5-flash_20260317_120000.json","data":{...},"page_percent":100,"state":"Done"}

data: {"type":"done","total_pages":1,"state":"Complete"}

```

---

## 3. Event types and payloads

Progress is **per-page**: use **`page_percent`** and **`state`**. The server sends **0 → 30 → 60 → 100** for each page:

| page_percent | Meaning |
|--------------|---------|
| 0  | Page started (OCR running) |
| 30 | OCR done, extraction (LLM) running |
| 60 | Extraction done, saving |
| 100| Page done (in `page` event) |

| type      | When        | Payload fields (relevant) | Use in UI |
|-----------|-------------|----------------------------|-----------|
| `start`   | Immediately | `total_pages`, `filename`, `provider`, `state` | Show overlay, state "Starting…" |
| `progress`| During page processing | `page`, `total_pages`, `message`, `page_percent` (0, 30, or 60), `state` | Update bar to `page_percent`; show `state` |
| `page`   | When a page is done | `page`, `total_pages`, `saved_to`, `data` (or `skipped`, `reason`), `page_percent` (100), `state` | Set page progress 100%; append `data` to results if not skipped |
| `error`  | When processing a page throws | `page`, `total_pages`, `page_percent`, `state`, `message` | Show error toast; keep stream open (you will still get `done`) |
| `done`   | After all pages | `total_pages`, `state` | state "Complete"; hide overlay after short delay |

**Important:** After `progress` the server may take **1–2 minutes** (OCR + LLM) with no `data:` events. It sends SSE **comment** lines (`: keepalive`) about every 10 seconds so the connection is not dropped. Ignore lines that start with `:` (SSE comments). Do **not** set a short fetch timeout; use a long one (e.g. 5–10 minutes) or no timeout.

**TypeScript-friendly shape (minimal)**

```ts
type OCRStreamEvent =
  | { type: 'start'; total_pages: number; filename: string; provider: string; state: string }
  | { type: 'progress'; page: number; total_pages: number; message?: string; page_percent: number; state: string }
  | { type: 'page'; page: number; total_pages: number; saved_to?: string; data?: YourExtractedType; skipped?: boolean; reason?: string; page_percent: number; state: string }
  | { type: 'error'; page: number; total_pages: number; page_percent: number; state: string; message: string }
  | { type: 'done'; total_pages: number; state: string };
```

---

## 4. Consuming the stream in JavaScript

1. Call `fetch()` as above; get `response.body` (a `ReadableStream`).
2. Use `response.body.getReader()` and a `TextDecoder` to read chunks.
3. Accumulate chunks into a string buffer; split by `\n\n` to get full event blocks.
4. For each block, take the line that starts with `data: `; the rest is one JSON object. Parse it and handle `type`, `percent`, and `state` (and `data` for `page`). Lines starting with `:` are SSE comments (e.g. keepalives); skip them (no `data:` line, so your parser will skip them).

**Example: read loop**

```js
const reader = response.body.getReader();
const decoder = new TextDecoder();
let buffer = '';

while (true) {
  const { value, done } = await reader.read();
  if (done) break;

  buffer += decoder.decode(value, { stream: true });
  const blocks = buffer.split('\n\n');
  buffer = blocks.pop() ?? '';

  for (const block of blocks) {
    const match = block.match(/^data:\s*(.+)/m);
    if (!match) continue;

    const event = JSON.parse(match[1]);

    // Per-page progress: update bar and status
    if (event.page_percent != null) {
      updateProgress(event.page_percent, event.state);
    }

    switch (event.type) {
      case 'start':
        onStart(event);
        break;
      case 'progress':
        onProgress(event);
        break;
      case 'page':
        if (!event.skipped) onPageDone(event.page, event.data);
        break;
      case 'done':
        onDone(event);
        break;
    }
  }
}
```

---

## 5. Progress UI

- **Per-page progress bar:** width = `page_percent`. The server sends **0 → 30 → 60 → 100** per page (0 = OCR, 30 = OCR done / LLM running, 60 = saving, 100 = page done). Example: `<div style={{ width: `${page_percent}%` }} />`. Reset to 0 when you receive `progress` for the next page.
- **State label:** display `state` (e.g. "Extracting data (page 2)…", "Saving page 1…", "Complete").
- **Overlay:** show while the request is in progress; hide when you receive `done` (optionally after a short delay).

Use `event.page_percent` and `event.state` from `progress` and `page` events. For an overall bar across all pages you can derive it as `(page - 1) / total_pages * 100 + page_percent / total_pages` if needed.

---

## 6. React: state and flow

- **State:** e.g. `loading` (boolean), `progress: { page_percent, state }`, `results: Array<{ page, data }>`, `error` (string | null).
- **On submit:** set `loading = true`, reset `progress` and `results`, then start the `fetch` + read loop. For each parsed event, call `setProgress({ page_percent: event.page_percent, state: event.state })` when `event.page_percent != null`; on `page` (non-skipped), append to results; on `done`, set `loading = false` (e.g. after 500–800 ms).
- **On fetch/stream error:** set `error` and `loading = false`. Optionally set a final progress state for "Error".

---

## 7. Error handling

| HTTP | Meaning | Suggested UI |
|------|---------|---------------|
| 400 | Bad request (e.g. wrong file type, or Gemini chosen but `GEMINI_API_KEY` not set) | Show `response.json().detail` or "Invalid request" |
| 422 | No text detected in any page | "No text detected in the file." |
| 503 | Gemini requested but not installed | "Server configuration error." |
| Network / parse error | Request failed or stream broken | "Request failed. Please try again." |

Read error body when possible: `const err = await response.json().catch(() => ({}));` and show `err.detail || response.statusText`.

---

## 8. Minimal React hook sketch

```ts
function useOCRStream() {
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState({ page_percent: 0, state: '' });
  const [results, setResults] = useState<Array<{ page: number; data: unknown }>>([]);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async (file: File, provider: 'ollama' | 'gemini' = 'ollama') => {
    setLoading(true);
    setProgress({ page_percent: 0, state: '' });
    setResults([]);
    setError(null);

    const formData = new FormData();
    formData.append('file', file);

    try {
      const res = await fetch(`${API_BASE}/ocr/stream?provider=${provider}`, {
        method: 'POST',
        body: formData,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || res.statusText);
      }

      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split('\n\n');
        buffer = blocks.pop() ?? '';

        for (const block of blocks) {
          const m = block.match(/^data:\s*(.+)/m);
          if (!m) continue;
          const event = JSON.parse(m[1]);
          if (event.page_percent != null) setProgress({ page_percent: event.page_percent, state: event.state ?? '' });
          if (event.type === 'page' && !event.skipped) setResults(prev => [...prev, { page: event.page, data: event.data }]);
          if (event.type === 'done') {
            setProgress(prev => ({ ...prev, state: 'Complete' }));
            setTimeout(() => setLoading(false), 600);
          }
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Request failed');
      setLoading(false);
    }
  }, []);

  return { loading, progress, results, error, run };
}
```

Use `progress.page_percent` and `progress.state` in your progress bar and status text.

---

## 9. Summary

| Task | How |
|------|-----|
| Call API | `POST /ocr/stream?provider=...` with `FormData` containing `file` |
| Read stream | `response.body.getReader()` + `TextDecoder`, split by `\n\n` |
| Parse events | For each block, match `data: (.+)`, `JSON.parse`, handle `type` |
| Show progress | Use `event.page_percent` (0 → 100 per page) and `event.state` on progress/page events |
| Show results | On `type === 'page'` and `!event.skipped`, append `event.data` |
| Finish | On `type === 'done'`, hide overlay after a short delay |
