"""Centralized Hugging Face Inference Providers client for all LLM calls."""

import json
import os
import re
import time
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class HuggingFaceConfigurationError(RuntimeError):
    """Raised when required Hugging Face environment configuration is missing."""


class HuggingFaceInferenceError(RuntimeError):
    """Raised when inference fails after bounded retry handling."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class HuggingFaceLLMProvider:
    """Use HF Inference Providers for validated JSON or text generation."""

    def __init__(
        self,
        model: str,
        token: str,
        provider: str = "auto",
        timeout_seconds: float = 60,
        max_retries: int = 2,
        client: Any | None = None,
    ) -> None:
        if not model.strip():
            raise HuggingFaceConfigurationError("HF_MODEL is required.")
        if not token.strip():
            raise HuggingFaceConfigurationError(
                "HF_TOKEN is required. Create a Hugging Face token with Inference Providers "
                "access and set HF_TOKEN in your environment or ignored .env file."
            )
        if not provider.strip():
            raise HuggingFaceConfigurationError("HF_PROVIDER must be a provider name or 'auto'.")
        if max_retries < 0 or max_retries > 5:
            raise ValueError("max_retries must be between 0 and 5.")
        self.model = model.strip()
        self.provider = provider.strip()
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        if client is None:
            try:
                from huggingface_hub import InferenceClient

                client = InferenceClient(
                    model=self.model,
                    provider=self.provider,
                    token=token,
                    timeout=timeout_seconds,
                )
            except Exception as error:
                raise HuggingFaceConfigurationError(
                    f"Unable to initialize Hugging Face InferenceClient: {error}"
                ) from error
        self._client = client

    @classmethod
    def from_environment(cls, **kwargs: Any) -> "HuggingFaceLLMProvider":
        """Build the provider from HF_TOKEN, HF_MODEL, and HF_PROVIDER."""
        token = os.getenv("HF_TOKEN", "").strip()
        model = os.getenv("HF_MODEL", "").strip()
        provider = os.getenv("HF_PROVIDER", "auto").strip()
        if not token:
            raise HuggingFaceConfigurationError(
                "HF_TOKEN is not configured. Set it in the environment or ignored .env file; "
                "the application will not fall back to another LLM or mock."
            )
        if not model:
            raise HuggingFaceConfigurationError(
                "HF_MODEL is not configured. Set it to a model available through Hugging Face "
                "Inference Providers."
            )
        try:
            timeout_seconds = float(os.getenv("HF_TIMEOUT_SECONDS", "60"))
            max_retries = int(os.getenv("HF_MAX_RETRIES", "2"))
        except ValueError as error:
            raise HuggingFaceConfigurationError(
                "HF_TIMEOUT_SECONDS and HF_MAX_RETRIES must be numeric."
            ) from error
        return cls(
            model=model,
            token=token,
            provider=provider or "auto",
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            **kwargs,
        )

    @property
    def provider_name(self) -> str:
        return f"Hugging Face Inference Providers ({self.provider})"

    def generate_structured(
        self,
        response_model: type[T],
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int = 1800,
    ) -> T:
        """Generate JSON and validate it against the supplied Pydantic model."""
        schema = json.dumps(response_model.model_json_schema(), ensure_ascii=False)
        prompt = (
            f"Return one JSON object matching this JSON Schema exactly. Do not use markdown.\n"
            f"JSON Schema:\n{schema}\n\nRequest:\n{user_prompt}"
        )
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                raw = self._request(
                    system_prompt,
                    prompt,
                    json_mode=True,
                    max_tokens=max_tokens,
                    max_attempts=1,
                )
            except HuggingFaceInferenceError as error:
                last_error = error
                if error.status_code in {400, 401, 403, 404, 422}:
                    raise
                if attempt < self.max_retries:
                    time.sleep(min(0.25 * (2**attempt), 1.0))
                    continue
                raise
            try:
                cleaned = re.sub(
                    r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE
                )
                payload = json.loads(cleaned)
                return response_model.model_validate(payload)
            except (json.JSONDecodeError, ValidationError, TypeError) as error:
                last_error = error
                if attempt < self.max_retries:
                    time.sleep(min(0.25 * (2**attempt), 1.0))
        raise HuggingFaceInferenceError(
            f"Model response did not validate as {response_model.__name__} after "
            f"{self.max_retries + 1} response attempt(s): {last_error}"
        ) from last_error

    def generate_text(
        self, system_prompt: str, user_prompt: str, *, max_tokens: int = 256
    ) -> str:
        """Generate and return a plain text completion."""
        text = self._request(system_prompt, user_prompt, json_mode=False, max_tokens=max_tokens)
        if not text.strip():
            raise HuggingFaceInferenceError("Hugging Face returned an empty completion.")
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
        last_error: Exception | None = None
        attempts = self.max_retries + 1 if max_attempts is None else max_attempts
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
                }
                if json_mode:
                    arguments["response_format"] = {"type": "json_object"}
                response = self._client.chat_completion(**arguments)
                text = response.choices[0].message.content
                if isinstance(text, list):
                    text = "".join(
                        item.get("text", "") if isinstance(item, dict) else str(item)
                        for item in text
                    )
                if not isinstance(text, str) or not text.strip():
                    raise HuggingFaceInferenceError("Model returned no text content.")
                return text
            except Exception as error:
                last_error = error
                status_code = _status_code(error)
                if status_code in {400, 401, 403, 404, 422}:
                    break
                if attempt + 1 < attempts:
                    time.sleep(min(0.25 * (2**attempt), 1.0))
        status_code = _status_code(last_error) if last_error else None
        if status_code == 401 or status_code == 403:
            detail = "Check HF_TOKEN permissions and Hugging Face Inference Providers access."
        elif status_code == 402:
            detail = "The Hugging Face account has no available provider credits. Check billing or add credits, then retry."
        elif status_code == 404:
            detail = f"Check that HF_MODEL={self.model!r} is available from HF_PROVIDER={self.provider!r}."
        elif status_code == 429:
            detail = "The Inference Provider rate limit or account quota was reached; check provider billing and retry later."
        elif isinstance(last_error, TimeoutError) or (
            last_error is not None
            and "timeout" in last_error.__class__.__name__.lower()
        ):
            detail = "The Hugging Face request timed out; check network access or increase the client timeout."
        else:
            detail = "Check network access, model/provider availability, and provider quota."
        raise HuggingFaceInferenceError(
            f"Hugging Face inference failed after {attempts} attempt(s). "
            f"{detail} Cause: {last_error}",
            status_code=status_code,
        ) from last_error


def _status_code(error: Exception | None) -> int | None:
    if error is None:
        return None
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None) or getattr(error, "status_code", None)
    return status if isinstance(status, int) else None