# Python Backend for Vercel AI SDK Chat

The frontend uses `DefaultChatTransport` from Vercel AI SDK v6 (`ai` ≥ 6.0.168, `@ai-sdk/react` ≥ 3.0.170).

> **Important:** This version of the SDK uses a completely different stream protocol than earlier v6 docs may describe. The old `0:"text"` prefix format is no longer supported by `DefaultChatTransport`. The backend must use real **Server-Sent Events (SSE)** with typed JSON objects.

---

## Request

The frontend sends a `POST` with `Content-Type: application/json`.

### Body shape

```json
{
  "id": "chat-abc123",
  "messages": [
    {
      "id": "msg-1",
      "role": "user",
      "parts": [
        { "type": "text", "text": "What is in this document?" }
      ]
    }
  ],
  "canvas_id": "..."
}
```

> `messages` uses the `UIMessage` format — text lives inside `parts`, not a flat `content` field.
> Extract text with: `" ".join(p["text"] for p in msg["parts"] if p["type"] == "text")`

---

## Response

**Required headers:**

```
Content-Type: text/event-stream
Cache-Control: no-cache
```

**Stream format — real SSE with typed JSON objects:**

Each event is a line starting with `data: ` followed by a JSON object, then **two newlines** (`\n\n`):

```
data: {"type":"start","messageId":"msg-abc"}

data: {"type":"start-step"}

data: {"type":"text-start","id":"text-0"}

data: {"type":"text-delta","id":"text-0","delta":"Hello, "}

data: {"type":"text-delta","id":"text-0","delta":"I can help with that."}

data: {"type":"text-end","id":"text-0"}

data: {"type":"finish-step"}

data: {"type":"finish","finishReason":"stop"}

```

| Event type | Required fields | Purpose |
|------------|----------------|---------|
| `start` | `messageId` (optional) | Marks start of assistant message |
| `start-step` | — | Marks start of a generation step |
| `text-start` | `id` | Marks start of a text content part |
| `text-delta` | `id`, `delta` | One streamed text chunk |
| `text-end` | `id` | Marks end of a text content part |
| `finish-step` | — | Marks end of a generation step |
| `finish` | `finishReason` (optional) | Last event — signals stream complete |
| `error` | `errorText` | Send on error instead of finish |

> The `id` in `text-start`/`text-delta`/`text-end` is a **content part ID** (not a message ID). Use any consistent string like `"text-0"`.

---

## Minimal FastAPI example

```python
import json
import uuid
import asyncio
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI()


class Part(BaseModel):
    type: str
    text: str | None = None


class Message(BaseModel):
    id: str
    role: str
    parts: list[Part]


class ChatRequest(BaseModel):
    id: str
    messages: list[Message]
    canvas_id: str | None = None

    model_config = {"extra": "ignore"}


def extract_text(messages: list[Message]) -> list[dict]:
    """Convert UIMessage parts format → plain {role, content} for your LLM."""
    result = []
    for msg in messages:
        text = " ".join(p.text for p in msg.parts if p.type == "text" and p.text)
        result.append({"role": msg.role, "content": text})
    return result


def sse(obj: dict) -> str:
    """Format a dict as a single SSE event."""
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


async def stream_llm_response(prompt: str):
    """Replace this with your actual LLM call (OpenAI, Anthropic, local, etc.)"""
    tokens = ["Hello! ", "I can help ", "with your document."]
    for token in tokens:
        yield token
        await asyncio.sleep(0.05)


async def ui_message_stream(messages: list[Message]):
    message_id = str(uuid.uuid4())
    text_part_id = "text-0"

    yield sse({"type": "start", "messageId": message_id})
    yield sse({"type": "start-step"})
    yield sse({"type": "text-start", "id": text_part_id})

    async for token in stream_llm_response(messages[-1].parts[0].text or ""):
        yield sse({"type": "text-delta", "id": text_part_id, "delta": token})

    yield sse({"type": "text-end", "id": text_part_id})
    yield sse({"type": "finish-step"})
    yield sse({"type": "finish", "finishReason": "stop"})


@app.post("/v1/chat")
async def chat(request: ChatRequest):
    return StreamingResponse(
        ui_message_stream(request.messages),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
```

---

## With a real LLM (OpenAI example)

```python
from openai import AsyncOpenAI

client = AsyncOpenAI()

async def stream_llm_response(messages: list[dict]):
    stream = await client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        stream=True,
    )
    async for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta
```

Then in `ui_message_stream`, replace the call with:
```python
plain = extract_text(messages)
async for token in stream_llm_response(plain):
    yield sse({"type": "text-delta", "id": text_part_id, "delta": token})
```

---

## Error handling

If an error occurs mid-stream, send an `error` event instead of `finish`:

```python
yield sse({"type": "error", "errorText": "LLM call failed: rate limit exceeded"})
```

---

## CORS

If the frontend and backend run on different origins (e.g. Vite dev server vs. Python server):

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # your Vite dev URL
    allow_methods=["POST"],
    allow_headers=["Content-Type", "Authorization"],
)
```

> No special `expose_headers` needed — this protocol uses only standard `Content-Type` and no custom response headers.
