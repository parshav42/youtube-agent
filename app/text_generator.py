import os
from typing import Protocol

import requests


class TextGenerator(Protocol):
    def generate_dialogue(self, video_idea: str) -> str:
        """Create narration/dialogue for the requested video idea."""


def _dialogue_prompt(video_idea: str) -> str:
    return (
        "Write a clear, engaging short-video narration with natural "
        "character dialogue when useful. Preserve the user's idea, "
        "make the context and sequence coherent, and return only the "
        "spoken script without headings or production notes.\n\n"
        f"User's idea:\n{video_idea}"
    )


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

        self.model = os.getenv("HF_MODEL", "Qwen/Qwen3-14B").strip()
        if not self.model:
            raise ValueError("HF_MODEL cannot be empty.")

    def generate_dialogue(self, video_idea: str) -> str:
        idea = video_idea.strip()
        if not idea:
            raise ValueError("Video idea cannot be empty.")

        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.token}"},
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": _dialogue_prompt(idea)}],
                    "stream": False,
                },
                timeout=180,
            )
            response.raise_for_status()
        except requests.HTTPError as error:
            status_code = (
                error.response.status_code if error.response is not None else None
            )
            if status_code in {401, 403}:
                message = "HF_TOKEN is invalid or lacks permission for Hugging Face Inference Providers."
            elif status_code == 404:
                message = f"HF_MODEL '{self.model}' is unavailable from Hugging Face Inference Providers."
            elif status_code == 429:
                message = "Hugging Face Inference Providers rate limit exceeded; retry later."
            elif status_code is not None and status_code >= 500:
                message = f"Hugging Face Inference Providers failed with HTTP {status_code}."
            elif status_code is not None:
                message = f"Hugging Face Inference Providers rejected the request with HTTP {status_code}."
            else:
                message = "Hugging Face Inference Providers rejected the request."
            raise RuntimeError(message) from None
        except requests.RequestException:
            raise RuntimeError(
                "Could not connect to Hugging Face Inference Providers."
            ) from None

        try:
            payload = response.json()
        except ValueError:
            raise RuntimeError("Hugging Face returned an invalid JSON response.") from None
        choices = payload.get("choices") if isinstance(payload, dict) else None
        first_choice = choices[0] if isinstance(choices, list) and choices else None
        message = first_choice.get("message") if isinstance(first_choice, dict) else None
        dialogue = message.get("content") if isinstance(message, dict) else None
        if not isinstance(dialogue, str) or not dialogue.strip():
            raise RuntimeError("Hugging Face returned empty dialogue.")
        return dialogue.strip()


class OllamaTextGenerator:
    def __init__(self) -> None:
        self.base_url = os.getenv(
            "OLLAMA_BASE_URL", "http://localhost:11434"
        ).rstrip("/")
        self.model = os.getenv("OLLAMA_MODEL", "qwen3:1.7b").strip()
        if not self.model:
            raise ValueError("OLLAMA_MODEL cannot be empty.")

    def generate_dialogue(self, video_idea: str) -> str:
        idea = video_idea.strip()
        if not idea:
            raise ValueError("Video idea cannot be empty.")

        try:
            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "stream": False,
                    "prompt": _dialogue_prompt(idea),
                },
                timeout=180,
            )
            response.raise_for_status()
        except requests.RequestException:
            raise RuntimeError("Ollama inference request failed.") from None

        payload = response.json()
        dialogue = payload.get("response") if isinstance(payload, dict) else None
        if not isinstance(dialogue, str) or not dialogue.strip():
            raise RuntimeError("Ollama returned empty dialogue.")
        return dialogue.strip()


def create_text_generator() -> TextGenerator:
    provider = os.getenv("LLM_PROVIDER", "huggingface").strip().lower()
    if os.getenv("HF_TOKEN", "").strip():
        provider = "huggingface"
    if provider == "huggingface":
        return HuggingFaceTextGenerator()
    if provider == "ollama":
        return OllamaTextGenerator()
    raise ValueError("LLM_PROVIDER must be 'huggingface' or 'ollama'.")
