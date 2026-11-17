import os
from pathlib import Path

from huggingface_hub import InferenceClient
from PIL import Image


class HuggingFaceMediaGenerator:
    def __init__(self) -> None:
        token = os.getenv("HF_TOKEN", "").strip()
        if not token:
            raise RuntimeError("Missing HF_TOKEN environment variable.")

        self.image_model = os.getenv(
            "HF_IMAGE_MODEL", "Tongyi-MAI/Z-Image-Turbo"
        ).strip()
        self.video_model = os.getenv("HF_VIDEO_MODEL", "Lightricks/LTX-2").strip()
        if not self.image_model:
            raise ValueError("HF_IMAGE_MODEL cannot be empty.")
        if not self.video_model:
            raise ValueError("HF_VIDEO_MODEL cannot be empty.")

        try:
            timeout = float(os.getenv("HF_MEDIA_TIMEOUT", "600"))
        except ValueError as error:
            raise ValueError("HF_MEDIA_TIMEOUT must be a number of seconds.") from error
        if timeout <= 0:
            raise ValueError("HF_MEDIA_TIMEOUT must be positive.")

        self.client = InferenceClient(
            token=token,
            provider="auto",
            timeout=timeout,
        )

    def generate_image(
        self,
        prompt: str,
        output_path: Path,
        width: int,
        height: int,
    ) -> Path:
        if not prompt.strip():
            raise ValueError("Image prompt cannot be empty.")
        if width <= 0 or height <= 0:
            raise ValueError("Image dimensions must be positive.")

        image = self.client.text_to_image(
            prompt=prompt,
            model=self.image_model,
            width=width,
            height=height,
        )
        if not isinstance(image, Image.Image):
            raise RuntimeError("Hugging Face returned an invalid generated image.")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.convert("RGB").save(output_path, format="PNG")
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise RuntimeError("Hugging Face image generation created an empty file.")
        return output_path

    def generate_video(
        self,
        image_path: Path,
        prompt: str,
        output_path: Path,
    ) -> Path:
        if not image_path.is_file() or image_path.stat().st_size == 0:
            raise ValueError("A generated image is required to create a video.")
        if not prompt.strip():
            raise ValueError("Video prompt cannot be empty.")

        video = self.client.image_to_video(
            image=image_path,
            model=self.video_model,
            prompt=prompt,
        )
        if not isinstance(video, bytes) or not video:
            raise RuntimeError("Hugging Face returned an empty generated video.")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(video)
        return output_path
