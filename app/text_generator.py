import os
from typing import Protocol

import requests


class TextGenerator(Protocol):
    def generate_dialogue(self, video_idea: str) -> str:
        """Create narration/dialogue for the requested video idea."""


class OllamaTextGenerator:
    def __init__(self) -> None:
        self.base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        self.model = os.getenv("OLLAMA_MODEL", "qwen3:1.7b").strip()
        if not self.model:
            raise ValueError("OLLAMA_MODEL cannot be empty.")

    def generate_dialogue(self, video_idea: str) -> str:
        idea = video_idea.strip()
        if not idea:
            raise ValueError("Video idea cannot be empty.")

        response = requests.post(
            f"{self.base_url}/api/generate",
            json={
                "model": self.model,
                "stream": False,
                "prompt": (
                    "Write a clear, engaging short-video narration with natural "
                    "character dialogue when useful. Preserve the user's idea, "
                    "make the context and sequence coherent, and return only the "
                    "spoken script without headings or production notes.\n\n"
                    f"User's idea:\n{idea}"
                ),
            },
            timeout=180,
        )
        response.raise_for_status()
        dialogue = response.json().get("response")
        if not isinstance(dialogue, str) or not dialogue.strip():
            raise RuntimeError("Ollama returned empty dialogue.")
        return dialogue.strip()
