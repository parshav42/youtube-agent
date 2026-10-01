"""Tests for centralized Hugging Face InferenceClient behavior."""

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.providers.huggingface_llm import (
    HuggingFaceConfigurationError,
    HuggingFaceInferenceError,
    HuggingFaceLLMProvider,
)


class Answer(BaseModel):
    answer: str


class FakeClient:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        result = self.outputs.pop(0)
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=result))]
        )


def make_provider(client, retries=0):
    return HuggingFaceLLMProvider(
        model="org/test-model",
        token="test-secret",
        provider="auto",
        max_retries=retries,
        client=client,
    )


def test_provider_validates_structured_json_and_uses_hf_model() -> None:
    client = FakeClient([json.dumps({"answer": "namaste"})])
    provider = make_provider(client)

    result = provider.generate_structured(Answer, "system", "prompt")

    assert result.answer == "namaste"
    assert client.calls[0]["model"] == "org/test-model"
    assert client.calls[0]["response_format"] == {"type": "json_object"}
    assert provider.provider_name == "Hugging Face Inference Providers (auto)"


def test_provider_retries_rate_limits_and_reports_quota(monkeypatch) -> None:
    monkeypatch.setattr("app.providers.huggingface_llm.time.sleep", lambda _: None)
    rate_limit = RuntimeError("rate limited")
    rate_limit.status_code = 429
    client = FakeClient([rate_limit, "ready"])

    result = make_provider(client, retries=1).generate_text("system", "prompt")

    assert result == "ready"
    assert len(client.calls) == 2

    client = FakeClient([rate_limit])
    with pytest.raises(HuggingFaceInferenceError, match="quota"):
        make_provider(client, retries=0).generate_text("system", "prompt")

    client = FakeClient([rate_limit, json.dumps({"answer": "recovered"})])
    result = make_provider(client, retries=1).generate_structured(Answer, "system", "prompt")
    assert result.answer == "recovered"


def test_provider_does_not_retry_auth_or_invalid_json() -> None:
    unauthorized = RuntimeError("unauthorized")
    unauthorized.status_code = 401
    client = FakeClient([unauthorized])
    with pytest.raises(HuggingFaceInferenceError, match="HF_TOKEN"):
        make_provider(client, retries=3).generate_text("system", "prompt")
    assert len(client.calls) == 1

    client = FakeClient(["not json", "also not json"])
    with pytest.raises(HuggingFaceInferenceError, match="did not validate"):
        make_provider(client, retries=1).generate_structured(Answer, "system", "prompt")
    assert len(client.calls) == 2


def test_provider_requires_hf_token_and_model(monkeypatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HF_MODEL", raising=False)

    with pytest.raises(HuggingFaceConfigurationError, match="HF_TOKEN"):
        HuggingFaceLLMProvider.from_environment()

    monkeypatch.setenv("HF_TOKEN", "test-secret")
    with pytest.raises(HuggingFaceConfigurationError, match="HF_MODEL"):
        HuggingFaceLLMProvider.from_environment()