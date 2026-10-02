"""Groq provider configuration, output, and failure contract tests."""

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.providers.groq_llm import GroqProvider
from app.providers.huggingface_llm import HuggingFaceInferenceError
from app.providers.llm_factory import build_llm_provider
import app.providers.groq_llm as groq_module


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
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=result))])


class FakeClient:
    def __init__(self, outputs):
        self.completions = FakeCompletions(outputs)
        self.chat = SimpleNamespace(completions=self.completions)


def test_factory_configures_groq_endpoint_model_and_authorization(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "local-test-key")
    monkeypatch.setenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    provider = build_llm_provider()

    assert isinstance(provider, GroqProvider)
    assert provider.base_url == "https://api.groq.com/openai/v1"
    assert provider.model == "llama-3.3-70b-versatile"
    assert str(provider._client.base_url).rstrip("/") == "https://api.groq.com/openai/v1"
    assert provider._client.auth_headers["Authorization"] == "Bearer local-test-key"


def test_default_constructor_reads_key_and_default_model_from_environment(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "local-test-key")
    monkeypatch.delenv("GROQ_MODEL", raising=False)

    provider = GroqProvider()

    assert provider.model == "llama-3.3-70b-versatile"
    assert provider._client.auth_headers["Authorization"] == "Bearer local-test-key"


def test_generates_text_and_structured_json(monkeypatch):
    monkeypatch.setattr(groq_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setenv("GROQ_INTER_CALL_DELAY_SECONDS", "0")
    client = FakeClient([json.dumps({"answer": "namaste"}), "नमस्ते मित्र"])
    provider = GroqProvider("llama-3.3-70b-versatile", "test-key", max_retries=0, client=client)

    assert provider.generate_structured(Answer, "system", "return JSON").answer == "namaste"
    assert provider.generate_text("system", "say hi") == "नमस्ते मित्र"
    assert client.completions.calls[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in client.completions.calls[1]


@pytest.mark.parametrize("status_code", [401, 429, 500])
def test_http_failures_use_shared_typed_error(status_code):
    response_error = RuntimeError(f"HTTP {status_code}")
    response_error.status_code = status_code
    outputs = [response_error] * (4 if status_code == 429 else 1)
    provider = GroqProvider(
        "llama-3.3-70b-versatile", "test-key", max_retries=0,
        client=FakeClient(outputs),
    )

    with pytest.raises(HuggingFaceInferenceError) as captured:
        provider.generate_text("system", "prompt")

    assert type(captured.value) is HuggingFaceInferenceError
    assert captured.value.status_code == status_code
    assert "Groq inference failed after 1 attempt(s)." in str(captured.value)


def test_rate_limit_waits_then_retries(monkeypatch):
    sleeps = []
    monkeypatch.setattr(groq_module.time, "sleep", sleeps.append)
    monkeypatch.setenv("GROQ_INTER_CALL_DELAY_SECONDS", "0")
    limited = RuntimeError("Rate limit: Please try again in 9.78s")
    limited.status_code = 429
    client = FakeClient([limited, "नमस्ते"])
    provider = GroqProvider("model", "key", max_retries=0, client=client)

    assert provider.generate_text("system", "prompt") == "नमस्ते"
    assert sleeps == [11.78, 0.0]
    assert len(client.completions.calls) == 2


def test_three_rate_limit_retries_raise_typed_error(monkeypatch):
    monkeypatch.setattr(groq_module.time, "sleep", lambda _seconds: None)
    limited = RuntimeError("Please try again in 1.0s")
    limited.status_code = 429
    provider = GroqProvider(
        "model", "key", max_retries=0,
        client=FakeClient([limited, limited, limited, limited]),
    )

    with pytest.raises(HuggingFaceInferenceError) as captured:
        provider.generate_text("system", "prompt")
    assert captured.value.status_code == 429


def test_token_limit_comes_from_config(monkeypatch):
    monkeypatch.setenv("GROQ_MAX_TOKENS", "1536")
    monkeypatch.setenv("GROQ_INTER_CALL_DELAY_SECONDS", "0")
    client = FakeClient([json.dumps({"answer": "ok"})])
    provider = GroqProvider("model", "key", max_retries=0, client=client)

    assert provider.generate_structured(Answer, "system", "prompt").answer == "ok"
    assert client.completions.calls[0]["max_tokens"] == 1536
