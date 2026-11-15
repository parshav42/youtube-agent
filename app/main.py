import os
import uuid
from pathlib import Path

import requests
from dotenv import load_dotenv
from elevenlabs import ElevenLabs
from elevenlabs.core.api_error import ApiError

if __package__:
    from .image_prompt_generator import LocalImagePromptGenerator
    from .supabase_store import StoredVideoJob, StoredVideoResult, SupabaseStore
    from .text_generator import OllamaTextGenerator
    from .video_generator import create_video
else:
    from image_prompt_generator import LocalImagePromptGenerator
    from supabase_store import StoredVideoJob, StoredVideoResult, SupabaseStore
    from text_generator import OllamaTextGenerator
    from video_generator import create_video


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env")

    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Missing ELEVENLABS_API_KEY in the project .env file. "
            "Run `npx -y @elevenlabs/cli@latest onboard check --env-path .env`."
        )
    client = ElevenLabs(api_key=api_key, timeout=60)

    story = input("Paste the story or narration for the video: ").strip()
    if not story:
        raise ValueError("Video story cannot be empty.")

    job_id = uuid.uuid4().hex
    job: StoredVideoJob = {
        "jobId": job_id,
        "idea": story,
        "status": "queued",
        "stage": "dialogue",
        "message": "Waiting to start...",
        "error": None,
        "result": None,
    }
    store = SupabaseStore.from_env()
    if store is not None:
        store.save_job(job)

    def report_progress(stage: str, message: str) -> None:
        job.update(status="running", stage=stage, message=message)
        if store is not None:
            store.save_job(job)

    try:
        video_path = create_video(
            prompt=story,
            client=client,
            output_dir=project_root / "output",
            image_prompt_generator=LocalImagePromptGenerator(),
            text_generator=OllamaTextGenerator(),
            progress_callback=report_progress,
        )
        if store is not None:
            run_dir = video_path.parent
            image_paths = sorted((run_dir / "images").glob("shot_*.png"))
            assets = store.upload_assets(job_id, run_dir)
            result: StoredVideoResult = {
                "videoUrl": assets[video_path.relative_to(run_dir).as_posix()],
                "dialogueUrl": assets["dialogue.txt"],
                "audioUrl": assets["narration.mp3"],
                "imageUrls": [
                    assets[path.relative_to(run_dir).as_posix()] for path in image_paths
                ],
                "outputFolder": run_dir.relative_to(project_root / "output").as_posix(),
            }
            job.update(
                status="complete",
                stage="complete",
                message="Your video is ready.",
                result=result,
            )
            store.save_job(job)
    except Exception as error:
        if store is not None:
            job.update(
                status="failed",
                stage="failed",
                message="Video generation failed.",
                error=f"{type(error).__name__}: {error}",
            )
            store.save_job(job)
        raise
    print(f"Video saved to: {video_path}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nVideo generation cancelled.")
    except requests.RequestException as error:
        print(f"\nA network request failed: {error}")
    except ApiError as error:
        print(f"\nElevenLabs API error ({error.status_code}): {error.body}")
    except (RuntimeError, ValueError) as error:
        print(f"\nError: {error}")
