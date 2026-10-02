"""Factory for the configured application-wide LLM provider."""

from app.config import get_llm_provider
from app.providers.huggingface_llm import HuggingFaceLLMProvider
from app.providers.sarvam_llm import SarvamLLMProvider
from app.providers.deepseek_llm import DeepSeekProvider
from app.providers.groq_llm import GroqProvider


def build_llm_provider() -> HuggingFaceLLMProvider | SarvamLLMProvider | DeepSeekProvider | GroqProvider:
    """Instantiate the provider selected by LLM_PROVIDER."""
    if get_llm_provider() == "sarvam":
        return SarvamLLMProvider.from_environment()
    if get_llm_provider() == "deepseek":
        return DeepSeekProvider.from_environment()
    if get_llm_provider() == "groq":
        return GroqProvider.from_environment()
    return HuggingFaceLLMProvider.from_environment()
