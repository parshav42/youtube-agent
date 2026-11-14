# ElevenLabs Narrated Video Maker

Turn a written story into a narrated MP4. ElevenLabs Text to Speech reads the
story aloud, and the app creates matching local title cards and assembles them
with the narration using FFmpeg. This workflow does not use Google or paid
image/video generation APIs.

## Requirements

- Python 3.10+
- [ElevenLabs API key](https://elevenlabs.io/) with Text to Speech access
- FFmpeg and `ffprobe`

## Setup

Install FFmpeg if needed (on Ubuntu/Debian, run `sudo apt install ffmpeg`),
then install the project dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On Windows, activate the environment with:

```powershell
.venv\Scripts\Activate.ps1
```

The app loads `.env` and reads `ELEVENLABS_API_KEY`. The ElevenLabs onboarding
CLI can configure this key. Optionally set `ELEVENLABS_VOICE_ID` to a voice ID
available to your account; otherwise, the app uses the first available voice.
Keep `.env` private; do not commit your key.

## Run

From the project root:

```bash
python app/main.py
```

Paste your story or narration when prompted. The generated 720p MP4 is saved
in `output/`. Longer narration creates more title cards. Image and video
generation through ElevenLabs Flows is not used.
