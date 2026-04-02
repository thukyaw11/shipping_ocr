from typing import Protocol


class TextGenerationProvider(Protocol):
    """Pluggable text completion for prompts (classification, extraction, etc.)."""

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Return model text for the given system + user messages."""
        ...
