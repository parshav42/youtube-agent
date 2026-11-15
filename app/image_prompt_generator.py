from typing import Protocol


class ImagePromptGenerator(Protocol):
    def generate_image_prompt(self, story_segment: str) -> str:
        """Return an image-generation prompt for one story segment."""


class LocalImagePromptGenerator:
    def generate_image_prompt(self, story_segment: str) -> str:
        segment = story_segment.strip()
        if not segment:
            raise ValueError("Story segment cannot be empty.")
        return (
            "Create a cinematic 16:9 illustration of this story moment: "
            f"{segment}. Do not include text or lettering."
        )
