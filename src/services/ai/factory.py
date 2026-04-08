from src.core.config import Config
from src.services.ai.gemini_provider import (
    GeminiTextProvider,
    build_default_gemini_provider,
    gemini_sdk_available,
)
from src.services.ai.ollama_provider import OllamaTextProvider, build_default_ollama_provider
from src.services.ai.base import TextGenerationProvider


def get_classification_text_provider() -> TextGenerationProvider:
    """
    Resolve the LLM used for document/page type classification.
    Uses GEMINI_CLASSIFICATION_MODEL (default: gemini-2.0-flash-lite) when Gemini
    is available — a faster, cheaper model sufficient for label classification.

    CLASSIFICATION_PROVIDER:
      - auto: Gemini when API key + SDK exist; otherwise Ollama
      - gemini: Gemini only; raises if unavailable
      - ollama: Ollama only
    """
    mode = (Config.CLASSIFICATION_PROVIDER or "auto").strip().lower()

    def try_gemini() -> TextGenerationProvider | None:
        key = Config.GEMINI_API_KEY
        if not key or not gemini_sdk_available():
            return None
        return GeminiTextProvider(api_key=key, model=Config.GEMINI_CLASSIFICATION_MODEL)

    def ollama() -> OllamaTextProvider:
        return build_default_ollama_provider()

    if mode == "ollama":
        return ollama()

    if mode == "gemini":
        g = try_gemini()
        if g is not None:
            return g
        raise RuntimeError(
            "CLASSIFICATION_PROVIDER=gemini but Gemini is unavailable: "
            "set GEMINI_API_KEY and install google-genai.",
        )

    # auto: use Gemini when configured; Ollama only when Gemini was not requested
    g = try_gemini()
    if g is not None:
        return g
    if Config.GEMINI_API_KEY and not gemini_sdk_available():
        raise RuntimeError(
            "GEMINI_API_KEY is set but google-genai failed to import; "
            "fix the install or unset the key. Not falling back to Ollama.",
        )
    return ollama()
