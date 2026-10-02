"""Sarvam chat provider implementing the centralized LLM generation interface."""

import os
import time
from typing import Any

from app.config import get_sarvam_settings
from app.providers.huggingface_llm import (
    HuggingFaceConfigurationError,
    HuggingFaceInferenceError,
    HuggingFaceLLMProvider,
    _status_code,
)


class SarvamLLMProvider(HuggingFaceLLMProvider):
    """Use Sarvam's OpenAI-compatible endpoint for text and structured output."""

    def __init__(
        self,
        model: str,
        token: str,
        base_url: str = "https://api.sarvam.ai/v1",
        timeout_seconds: float = 60,
        max_retries: int = 2,
        client: Any | None = None,
    ) -> None:
        if not model.strip():
            raise HuggingFaceConfigurationError("SARVAM_MODEL_NAME is required.")
        if not token.strip():
            raise HuggingFaceConfigurationError(
                "SARVAM_API_KEY is required. Set it in your environment or ignored .env file."
            )
        if not base_url.strip():
            raise HuggingFaceConfigurationError("SARVAM_BASE_URL is required.")
        if max_retries < 0 or max_retries > 5:
            raise ValueError("max_retries must be between 0 and 5.")
        self.model = model.strip()
        self.base_url = base_url.strip().rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        if client is None:
            try:
                from openai import OpenAI

                client = OpenAI(
                    api_key=token.strip(),
                    base_url=self.base_url,
                    timeout=timeout_seconds,
                    max_retries=0,
                )
            except Exception as error:
                raise HuggingFaceConfigurationError(
                    f"Unable to initialize Sarvam OpenAI client: {error}"
                ) from error
        self._client = client

    @classmethod
    def from_environment(cls, **kwargs: Any) -> "SarvamLLMProvider":
        """Build the provider using SARVAM_API_KEY and optional endpoint settings."""
        settings = get_sarvam_settings()
        if not settings["api_key"]:
            raise HuggingFaceConfigurationError(
                "SARVAM_API_KEY is not configured. Set it in the environment or ignored .env file."
            )
        try:
            timeout_seconds = float(os.getenv("SARVAM_TIMEOUT_SECONDS", "60"))
            max_retries = int(os.getenv("SARVAM_MAX_RETRIES", "2"))
        except ValueError as error:
            raise HuggingFaceConfigurationError(
                "SARVAM_TIMEOUT_SECONDS and SARVAM_MAX_RETRIES must be numeric."
            ) from error
        return cls(
            model=settings["model"],
            token=settings["api_key"],
            base_url=settings["base_url"],
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            **kwargs,
        )

    @property
    def provider_name(self) -> str:
        return f"Sarvam ({self.model})"

    def generate_text(
        self, system_prompt: str, user_prompt: str, *, max_tokens: int = 256
    ) -> str:
        """Generate and return a plain text completion."""
        text = self._request(
            system_prompt, user_prompt, json_mode=False, max_tokens=max_tokens
        )
        if not text.strip():
            raise HuggingFaceInferenceError("Sarvam returned an empty completion.")
        return text.strip()

    def _request(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        json_mode: bool,
        max_tokens: int,
        max_attempts: int | None = None,
    ) -> str:
        attempts = self.max_retries + 1 if max_attempts is None else max_attempts
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                arguments: dict[str, Any] = {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": 0.4,
                    "reasoning_effort": None,
                }
                if json_mode:
                    arguments["response_format"] = {"type": "json_object"}
                response = self._client.chat.completions.create(**arguments)
                content = response.choices[0].message.content
                if isinstance(content, list):
                    content = "".join(
                        item.get("text", "") if isinstance(item, dict) else str(item)
                        for item in content
                    )
                if not isinstance(content, str) or not content.strip():
                    raise HuggingFaceInferenceError("Sarvam returned no text content.")
                return content
            except Exception as error:
                last_error = error
                status_code = _status_code(error)
                if status_code in {400, 401, 403, 404, 422}:
                    break
                if attempt + 1 < attempts:
                    time.sleep(min(0.25 * (2**attempt), 1.0))

        status_code = _status_code(last_error)
        if status_code in {401, 403}:
            detail = "Check SARVAM_API_KEY permissions and account access."
        elif status_code == 402:
            detail = "The Sarvam account rejected payment or has insufficient credits."
        elif status_code == 404:
            detail = f"Check that SARVAM_MODEL_NAME={self.model!r} is available."
        elif status_code == 429:
            detail = "The Sarvam rate limit or account quota was reached; retry later."
        elif isinstance(last_error, TimeoutError) or (
            last_error is not None and "timeout" in last_error.__class__.__name__.lower()
        ):
            detail = "The Sarvam request timed out; check network access or increase the client timeout."
        else:
            detail = "Check network access, model availability, and Sarvam account quota."
        raise HuggingFaceInferenceError(
            f"Sarvam inference failed after {attempts} attempt(s). "
            f"{detail} Cause: {last_error}",
            status_code=status_code,
        ) from last_error


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Sarvam provider connectivity check.")
    parser.add_argument("--smoke", action="store_true", help="Send one short prompt to Sarvam.")
    arguments = parser.parse_args()
    if not arguments.smoke:
        parser.error("pass --smoke to make a live, non-destructive request")
    provider = SarvamLLMProvider.from_environment()
    print(provider.generate_text("Reply briefly.", "Say: Sarvam connectivity verified.", max_tokens=64))