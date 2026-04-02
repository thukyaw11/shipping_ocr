import logging
import time

import ollama

from src.core.config import Config

logger = logging.getLogger("shipping_bill_ocr")


class OllamaTextProvider:
    def __init__(self, model: str) -> None:
        self._model = model

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        started = time.monotonic()
        logger.info(
            "Ollama generate start model=%s prompt_chars=%s",
            self._model,
            len(user_prompt or ""),
        )
        response = ollama.chat(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        raw = str(response.message.content or "")
        logger.info(
            "Ollama generate done took_ms=%.1f",
            (time.monotonic() - started) * 1000.0,
        )
        return raw


def build_default_ollama_provider() -> OllamaTextProvider:
    return OllamaTextProvider(model=Config.OLLAMA_CLASSIFICATION_MODEL)
