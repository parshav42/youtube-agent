"""Hugging Face metadata generation for approved video productions."""

from app.providers.huggingface_llm import HuggingFaceLLMProvider
from app.schemas.script_schema import MotivationalScript, ProductionMetadata


def generate_metadata(
    script: MotivationalScript, llm_provider: HuggingFaceLLMProvider
) -> ProductionMetadata:
    """Generate conservative YouTube metadata from the approved script."""
    return llm_provider.generate_structured(
        ProductionMetadata,
        "Write accurate YouTube Shorts metadata from only the provided original script. "
        "Do not add factual claims, guarantees, or engagement predictions. "
        "Use a valid BCP-47 language code (hi-IN for Hindi/Hinglish, en-IN for English), "
        "and use a valid numeric YouTube category ID.",
        f"Topic: {script.topic}\nScript language: {script.language}\nNarration: {script.narration}\n"
        f"Existing title candidates: {script.title_candidates}\n",
        max_tokens=500,
    )