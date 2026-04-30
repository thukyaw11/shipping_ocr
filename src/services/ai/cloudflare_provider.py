import logging
import time
from typing import TypeVar

from pydantic import BaseModel

logger = logging.getLogger("shipping_bill_ocr")

TModel = TypeVar("TModel", bound=BaseModel)

try:
    from openai import OpenAI as _OpenAI

    _OPENAI_IMPORT_OK = True
except ImportError:
    _OpenAI = None  # type: ignore[assignment]
    _OPENAI_IMPORT_OK = False


def cloudflare_sdk_available() -> bool:
    return _OPENAI_IMPORT_OK


class CloudflareAIProvider:
    def __init__(
        self,
        model: str,
        *,
        account_id: str | None = None,
        api_token: str | None = None,
        worker_url: str | None = None,
    ) -> None:
        """
        Two modes:
          - Worker mode (wrangler): set worker_url (e.g. http://localhost:8787)
          - REST API mode: set account_id + api_token
        """
        if not _OPENAI_IMPORT_OK:
            raise RuntimeError("openai package is not installed")
        self._model = model

        if worker_url:
            # wrangler dev or deployed worker — no account ID needed in URL
            self._client = _OpenAI(
                api_key=api_token or "unused",
                base_url=worker_url.rstrip("/"),
            )
        elif account_id and api_token:
            self._client = _OpenAI(
                api_key=api_token,
                base_url=f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
            )
        else:
            raise ValueError("Provide either worker_url or both account_id and api_token")

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        started = time.monotonic()
        logger.info(
            "Cloudflare AI generate start model=%s prompt_chars=%s",
            self._model,
            len(user_prompt or ""),
        )
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        text = resp.choices[0].message.content or ""
        logger.info(
            "Cloudflare AI generate done took_ms=%.1f",
            (time.monotonic() - started) * 1000.0,
        )
        return text

    def generate_structured_json(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[TModel],
    ) -> TModel:
        started = time.monotonic()
        logger.info(
            "Cloudflare AI structured JSON start model=%s prompt_chars=%s schema=%s",
            self._model,
            len(user_prompt or ""),
            getattr(response_model, "__name__", "model"),
        )
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": response_model.__name__,
                    "schema": response_model.model_json_schema(),
                },
            },
        )
        raw = resp.choices[0].message.content or "{}"
        parsed = response_model.model_validate_json(raw)
        logger.info(
            "Cloudflare AI structured JSON done took_ms=%.1f",
            (time.monotonic() - started) * 1000.0,
        )
        return parsed


def build_cloudflare_provider() -> CloudflareAIProvider | None:
    from src.core.config import Config

    if not cloudflare_sdk_available():
        return None

    # Worker mode (wrangler dev or deployed worker) takes priority
    if Config.CF_WORKER_URL:
        return CloudflareAIProvider(
            model=Config.CF_AI_MODEL,
            worker_url=Config.CF_WORKER_URL,
            api_token=Config.CF_API_TOKEN,
        )

    # REST API mode — needs account ID + token
    if Config.CF_ACCOUNT_ID and Config.CF_API_TOKEN:
        return CloudflareAIProvider(
            model=Config.CF_AI_MODEL,
            account_id=Config.CF_ACCOUNT_ID,
            api_token=Config.CF_API_TOKEN,
        )

    return None
