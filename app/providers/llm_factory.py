"""Factory for the configured application-wide LLM provider."""

from app.config import get_llm_provider
from app.providers.huggingface_llm import HuggingFaceLLMProvider
from app.providers.sarvam_llm import SarvamLLMProvider


def build_llm_provider() -> HuggingFaceLLMProvider | SarvamLLMProvider:
    """Instantiate the provider selected by LLM_PROVIDER."""
    if get_llm_provider() == "sarvam":
        return SarvamLLMProvider.from_environment()
    return HuggingFaceLLMProvider.from_environment()