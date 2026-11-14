import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from elevenlabs import ElevenLabs
from elevenlabs.core.api_error import ApiError

if __package__:
    from .video_generator import create_video
else:
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

    prompt = input("Describe the video to create: ").strip()
    if not prompt:
        raise ValueError("Video description cannot be empty.")

    video_path = create_video(
        prompt=prompt,
        client=client,
        output_dir=project_root / "output",
    )
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
