import shutil
import subprocess
from pathlib import Path

if __package__:
    from .image_generator import create_image
else:
    from image_generator import create_image


class LocalMediaGenerator:
    def generate_image(
        self,
        prompt: str,
        output_path: Path,
        width: int,
        height: int,
    ) -> Path:
        return create_image(prompt, output_path, width, height)

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

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("FFmpeg is required. Install FFmpeg and try again.")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-loop",
                    "1",
                    "-framerate",
                    "24",
                    "-i",
                    str(image_path),
                    "-vf",
                    "zoompan=z='min(zoom+0.0015,1.08)':d=96:s=1280x720:fps=24",
                    "-frames:v",
                    "96",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-an",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.CalledProcessError as error:
            detail = error.stderr.strip()[-1000:] if error.stderr else ""
            message = "FFmpeg could not create the local motion clip."
            if detail:
                message = f"{message} {detail}"
            raise RuntimeError(message) from error
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("FFmpeg timed out creating a local motion clip.") from error

        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise RuntimeError("FFmpeg finished without creating a local video clip.")
        return output_path
