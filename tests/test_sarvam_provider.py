"""Tests for the Sarvam OpenAI-compatible provider and LLM selection."""

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.providers.huggingface_llm import (
    HuggingFaceInferenceError,
    HuggingFaceLLMProvider,
)
from app.providers.llm_factory import build_llm_provider
from app.providers.sarvam_llm import SarvamLLMProvider


class Answer(BaseModel):
    answer: str


class FakeCompletions:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.outputs.pop(0)
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=result))]
        )


class FakeOpenAIClient:
    def __init__(self, outputs):
        self.completions = FakeCompletions(outputs)
        self.chat = SimpleNamespace(completions=self.completions)


def test_provider_uses_sarvam_openai_configuration_and_bearer_header(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "sarvam")
    monkeypatch.setenv("SARVAM_API_KEY", "local-test-key")
    monkeypatch.setenv("SARVAM_BASE_URL", "https://api.sarvam.ai/v1")
    monkeypatch.setenv("SARVAM_MODEL_NAME", "sarvam-105b")

    provider = build_llm_provider()

    assert isinstance(provider, SarvamLLMProvider)
    assert provider.base_url == "https://api.sarvam.ai/v1"
    assert provider.model == "sarvam-105b"
    assert str(provider._client.base_url).rstrip("/") == provider.base_url
    assert provider._client.auth_headers["Authorization"] == "Bearer local-test-key"


def test_provider_generates_structured_json_and_plain_text() -> None:
    client = FakeOpenAIClient([json.dumps({"answer": "namaste"}), "ready"])
    provider = SarvamLLMProvider(
        model="sarvam-105b", token="local-test-key", max_retries=0, client=client
    )

    structured = provider.generate_structured(Answer, "system", "json prompt")
    text = provider.generate_text("system", "text prompt")

    assert structured.answer == "namaste"
    assert text == "ready"
    assert client.completions.calls[0]["model"] == "sarvam-105b"
    assert client.completions.calls[0]["response_format"] == {"type": "json_object"}
    assert client.completions.calls[0]["reasoning_effort"] is None
    assert "response_format" not in client.completions.calls[1]


def test_provider_retries_transient_errors(monkeypatch) -> None:
    monkeypatch.setattr("app.providers.sarvam_llm.time.sleep", lambda _: None)
    transient_error = RuntimeError("rate limited")
    transient_error.status_code = 429
    client = FakeOpenAIClient([transient_error, "ready"])
    provider = SarvamLLMProvider(
        model="sarvam-105b", token="local-test-key", max_retries=1, client=client
    )

    assert provider.generate_text("system", "prompt") == "ready"
    assert len(client.completions.calls) == 2


def test_provider_factory_selects_huggingface(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "huggingface")
    monkeypatch.setenv("HF_TOKEN", "local-test-token")
    monkeypatch.setenv("HF_MODEL", "org/test-model")

    provider = build_llm_provider()

    assert isinstance(provider, HuggingFaceLLMProvider)
    assert not isinstance(provider, SarvamLLMProvider)
    assert provider.model == "org/test-model"


@pytest.mark.parametrize(
    ("status_code", "message_fragment"),
    [
        (401, "SARVAM_API_KEY"),
        (402, "insufficient credits"),
        (500, "Check network access"),
    ],
)
def test_provider_errors_match_huggingface_exception_shape(
    status_code: int, message_fragment: str
) -> None:
    response_error = RuntimeError(f"HTTP {status_code}")
    response_error.status_code = status_code
    provider = SarvamLLMProvider(
        model="sarvam-105b",
        token="local-test-key",
        max_retries=0,
        client=FakeOpenAIClient([response_error]),
    )

    with pytest.raises(HuggingFaceInferenceError) as error:
        provider.generate_text("system", "prompt")

    assert type(error.value) is HuggingFaceInferenceError
    assert error.value.status_code == status_code
    assert "Sarvam inference failed after 1 attempt(s)." in str(error.value)
    assert message_fragment in str(error.value)