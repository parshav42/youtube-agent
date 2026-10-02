"""Groq OpenAI-compatible provider for text and validated JSON output."""

import os
import re
import time
from typing import Any

from app.config import (
    get_groq_api_key,
    get_groq_inter_call_delay_seconds,
    get_groq_max_tokens,
    get_groq_model,
)
from app.providers.huggingface_llm import (
    HuggingFaceConfigurationError,
    HuggingFaceInferenceError,
    HuggingFaceLLMProvider,
    _status_code,
)


class GroqProvider(HuggingFaceLLMProvider):
    """Call Groq's OpenAI-compatible chat completions endpoint."""

    def __init__(
        self,
        model: str | None = None,
        token: str | None = None,
        base_url: str = "https://api.groq.com/openai/v1",
        timeout_seconds: float = 60,
        max_retries: int = 2,
        client: Any | None = None,
    ) -> None:
        model = get_groq_model() if model is None else model
        token = get_groq_api_key() if token is None else token
        if not model.strip():
            raise HuggingFaceConfigurationError("GROQ_MODEL is required.")
        if not token.strip():
            raise HuggingFaceConfigurationError(
                "GROQ_API_KEY is required. Set it in your environment or ignored .env file."
            )
        if not base_url.strip():
            raise HuggingFaceConfigurationError("Groq base URL is required.")
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
                    f"Unable to initialize Groq OpenAI client: {error}"
                ) from error
        self._client = client

    @classmethod
    def from_environment(cls, **kwargs: Any) -> "GroqProvider":
        """Build the provider using GROQ_API_KEY and GROQ_MODEL."""
        api_key = get_groq_api_key()
        if not api_key:
            raise HuggingFaceConfigurationError(
                "GROQ_API_KEY is not configured. Set it in the environment or ignored .env file."
            )
        try:
            timeout_seconds = float(os.getenv("GROQ_TIMEOUT_SECONDS", "60"))
            max_retries = int(os.getenv("GROQ_MAX_RETRIES", "2"))
        except ValueError as error:
            raise HuggingFaceConfigurationError(
                "GROQ_TIMEOUT_SECONDS and GROQ_MAX_RETRIES must be numeric."
            ) from error
        return cls(
            model=get_groq_model(), token=api_key,
            timeout_seconds=timeout_seconds, max_retries=max_retries, **kwargs,
        )

    @property
    def provider_name(self) -> str:
        return f"Groq ({self.model})"

    def generate_text(
        self, system_prompt: str, user_prompt: str, *, max_tokens: int | None = None
    ) -> str:
        """Generate a plain text completion."""
        text = self._request(
            system_prompt, user_prompt, json_mode=False,
            max_tokens=min(max_tokens or get_groq_max_tokens(), get_groq_max_tokens()),
        )
        if not text.strip():
            raise HuggingFaceInferenceError("Groq returned an empty completion.")
        return text.strip()

    def generate(self, prompt: str, *, max_tokens: int | None = None) -> str:
        """Convenience one-prompt call used by provider smoke checks."""
        return self.generate_text("Follow the user's request exactly.", prompt, max_tokens=max_tokens)

    def generate_structured(
        self, response_model: type, system_prompt: str, user_prompt: str,
        *, max_tokens: int | None = None,
    ):
        """Generate schema-validated JSON within the configured Groq token ceiling."""
        return super().generate_structured(
            response_model, system_prompt, user_prompt,
            max_tokens=min(max_tokens or get_groq_max_tokens(), get_groq_max_tokens()),
        )

    def _request(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        json_mode: bool,
        max_tokens: int,
        max_attempts: int | None = None,
    ) -> str:
        """Request completion, preserving Sarvam's retries and exception types."""
        attempts = self.max_retries + 1 if max_attempts is None else max_attempts
        last_error: Exception | None = None
        rate_limit_retries = 0
        attempt = 0
        while attempt < attempts:
            try:
                arguments: dict[str, Any] = {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": 0.4,
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
                    raise HuggingFaceInferenceError("Groq returned no text content.")
                time.sleep(get_groq_inter_call_delay_seconds())
                return content
            except Exception as error:
                last_error = error
                status = _status_code(error)
                if status == 429 and rate_limit_retries < 3:
                    rate_limit_retries += 1
                    match = re.search(r"try again in\s+([\d.]+)\s*s", str(error), re.IGNORECASE)
                    time.sleep((float(match.group(1)) if match else 0.0) + 2.0)
                    continue
                if status in {400, 401, 403, 404, 422}:
                    break
                attempt += 1
                if attempt < attempts:
                    time.sleep(min(0.25 * (2**attempt), 1.0))
                    attempt += 1
                else:
                    break

        status = _status_code(last_error)
        if status in {401, 403}:
            detail = "Check GROQ_API_KEY permissions and account access."
        elif status == 402:
            detail = "The Groq account rejected payment or has insufficient credits."
        elif status == 404:
            detail = f"Check that GROQ_MODEL={self.model!r} is available."
        elif status == 429:
            detail = "The Groq rate limit or account quota was reached; retry later."
        elif last_error is not None and "timeout" in last_error.__class__.__name__.lower():
            detail = "The Groq request timed out; check network access or increase the client timeout."
        else:
            detail = "Check network access, model availability, and Groq account quota."
        raise HuggingFaceInferenceError(
            f"Groq inference failed after {attempts} attempt(s). {detail} Cause: {last_error}",
            status_code=status,
        ) from last_error


if __name__ == "__main__":
    provider = GroqProvider.from_environment()
    print(provider.generate_text("Reply briefly.", "Say: Groq connectivity verified.", max_tokens=64))
