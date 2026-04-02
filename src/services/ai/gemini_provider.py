import logging
import time
from typing import TypeVar

from pydantic import BaseModel

from src.core.config import Config

logger = logging.getLogger("shipping_bill_ocr")

TModel = TypeVar("TModel", bound=BaseModel)

try:
    from google import genai

    _GEMINI_IMPORT_OK = True
except ImportError:
    genai = None  # type: ignore[assignment]
    _GEMINI_IMPORT_OK = False


def gemini_sdk_available() -> bool:
    return _GEMINI_IMPORT_OK


class GeminiTextProvider:
    def __init__(self, api_key: str, model: str) -> None:
        if not _GEMINI_IMPORT_OK:
            raise RuntimeError("google-genai is not installed")
        self._api_key = api_key
        self._model = model

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        started = time.monotonic()
        logger.info(
            "Gemini generate start model=%s prompt_chars=%s",
            self._model,
            len(user_prompt or ""),
        )
        client = genai.Client(api_key=self._api_key)
        resp = client.models.generate_content(
            model=self._model,
            contents=f"{system_prompt}\n\n{user_prompt}",
        )
        text = str(getattr(resp, "text", "") or "")
        logger.info(
            "Gemini generate done took_ms=%.1f",
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
            "Gemini structured JSON start model=%s prompt_chars=%s schema=%s",
            self._model,
            len(user_prompt or ""),
            getattr(response_model, "__name__", "model"),
        )
        client = genai.Client(api_key=self._api_key)
        resp = client.models.generate_content(
            model=self._model,
            contents=f"{system_prompt}\n\n{user_prompt}",
            config={
                "response_mime_type": "application/json",
                "response_json_schema": response_model.model_json_schema(),
            },
        )
        raw = getattr(resp, "text", "") or ""
        parsed = response_model.model_validate_json(raw)
        logger.info(
            "Gemini structured JSON done took_ms=%.1f",
            (time.monotonic() - started) * 1000.0,
        )
        return parsed


def build_default_gemini_provider() -> GeminiTextProvider | None:
    key = Config.GEMINI_API_KEY
    if not key or not gemini_sdk_available():
        return None
    model = Config.GEMINI_MODEL
    return GeminiTextProvider(api_key=key, model=model)
