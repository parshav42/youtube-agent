import os
from typing import Protocol

import requests


class TextGenerator(Protocol):
    def generate_dialogue(self, video_idea: str) -> str:
        """Create narration/dialogue for the requested video idea."""


class HuggingFaceTextGenerator:
    def __init__(self) -> None:
        self.token = os.getenv("HF_TOKEN", "").strip()
        if not self.token:
            raise RuntimeError("Missing HF_TOKEN environment variable.")

        self.base_url = os.getenv(
            "HF_BASE_URL", "https://router.huggingface.co/v1"
        ).rstrip("/")
        if not self.base_url:
            raise ValueError("HF_BASE_URL cannot be empty.")

        self.model = os.getenv("HF_MODEL", "Qwen/Qwen3-4B-Instruct-2507").strip()
        if not self.model:
            raise ValueError("HF_MODEL cannot be empty.")

    def generate_dialogue(self, video_idea: str) -> str:
        idea = video_idea.strip()
        if not idea:
            raise ValueError("Video idea cannot be empty.")

        prompt = (
            "Write a clear, engaging short-video narration with natural "
            "character dialogue when useful. Preserve the user's idea, "
            "make the context and sequence coherent, and return only the "
            "spoken script without headings or production notes.\n\n"
            f"User's idea:\n{idea}"
        )
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.token}"},
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                },
                timeout=180,
            )
            response.raise_for_status()
        except requests.RequestException:
            raise RuntimeError("Hugging Face inference request failed.") from None

        payload = response.json()
        choices = payload.get("choices") if isinstance(payload, dict) else None
        first_choice = choices[0] if isinstance(choices, list) and choices else None
        message = first_choice.get("message") if isinstance(first_choice, dict) else None
        dialogue = message.get("content") if isinstance(message, dict) else None
        if not isinstance(dialogue, str) or not dialogue.strip():
            raise RuntimeError("Hugging Face returned empty dialogue.")
        return dialogue.strip()
