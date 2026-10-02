"""DeepSeek provider configuration, output, and failure contract tests."""

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.providers.deepseek_llm import DeepSeekProvider
from app.providers.huggingface_llm import HuggingFaceInferenceError
from app.providers.llm_factory import build_llm_provider


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


def test_factory_configures_deepseek_endpoint_model_and_authorization(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "local-test-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-pro")

    provider = build_llm_provider()

    assert isinstance(provider, DeepSeekProvider)
    assert provider.base_url == "https://api.deepseek.com"
    assert provider.model == "deepseek-v4-pro"
    assert str(provider._client.base_url).rstrip("/") == "https://api.deepseek.com"
    assert provider._client.auth_headers["Authorization"] == "Bearer local-test-key"


def test_default_constructor_reads_key_and_default_model_from_environment(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "local-test-key")
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)

    provider = DeepSeekProvider()

    assert provider.model == "deepseek-v4-pro"
    assert provider._client.auth_headers["Authorization"] == "Bearer local-test-key"


def test_generates_text_and_structured_json():
    client = FakeClient([json.dumps({"answer": "namaste"}), "नमस्ते मित्र"])
    provider = DeepSeekProvider("deepseek-v4-pro", "test-key", max_retries=0, client=client)

    assert provider.generate_structured(Answer, "system", "return JSON").answer == "namaste"
    assert provider.generate_text("system", "say hi") == "नमस्ते मित्र"
    assert client.completions.calls[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in client.completions.calls[1]


@pytest.mark.parametrize("status_code", [401, 402, 429, 500])
def test_http_failures_use_shared_typed_error(status_code):
    response_error = RuntimeError(f"HTTP {status_code}")
    response_error.status_code = status_code
    provider = DeepSeekProvider(
        "deepseek-v4-pro", "test-key", max_retries=0,
        client=FakeClient([response_error]),
    )

    with pytest.raises(HuggingFaceInferenceError) as captured:
        provider.generate_text("system", "prompt")

    assert type(captured.value) is HuggingFaceInferenceError
    assert captured.value.status_code == status_code
    assert "DeepSeek inference failed after 1 attempt(s)." in str(captured.value)
