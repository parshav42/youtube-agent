# ElevenLabs Narrated Video Maker

Turn a written story into a narrated MP4. ElevenLabs Text to Speech reads the
story aloud, and the app creates deterministic, locally illustrated scene
images from each narration segment. It overlays the text and assembles the
images with the narration using FFmpeg. The current image generator is a local
Pillow-based implementation; it does not use an image-generation API.
The story is expanded into a coherent narration/dialogue by a local Ollama
model before being sent to ElevenLabs. The generated script is split into
segments; a local prompt builder creates a visual description for each
illustration.

## Requirements

- Python 3.10+
- Node.js 18+ and npm
- [Ollama](https://ollama.com/) with the `qwen3:1.7b` model (or set
  `OLLAMA_MODEL` to another installed model)
- [ElevenLabs API key](https://elevenlabs.io/) with Text to Speech access
- FFmpeg and `ffprobe`

## Setup

Install FFmpeg if needed (on Ubuntu/Debian, run `sudo apt install ffmpeg`),
then install the Python and frontend dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm --prefix frontend install
```

On Windows, activate the environment with:

```powershell
.venv\Scripts\Activate.ps1
```

The app loads `.env` and reads `ELEVENLABS_API_KEY`. The ElevenLabs onboarding
CLI can configure this key. Optionally set `ELEVENLABS_VOICE_ID` to a voice ID
available to your account; otherwise, the app uses the first available voice.
Ollama must be running locally. Optionally set `OLLAMA_BASE_URL` (defaults to
`http://localhost:11434`) and `OLLAMA_MODEL` (defaults to `qwen3:1.7b`) in
`.env`.

To persist video ideas, job progress, results, and generated files in Supabase:

1. Run [`supabase/schema.sql`](supabase/schema.sql) in the Supabase SQL Editor.
   This creates the `video_jobs` table and a private `video-assets` Storage bucket.
2. Add the project URL and **service-role** key to the server's `.env`:

   ```dotenv
   SUPABASE_URL=https://your-project.supabase.co
   SUPABASE_SECRET_KEY=your-secret-key
   ```

The secret key is server-only; never put it in frontend code or expose it to
users. Existing `supabase_url` and `supabase_sec` variable names are also
supported. The publishable key is not used by the server-side storage client.
When the URL and secret key are present, video jobs and generated assets are
saved to Supabase, and asset links use temporary signed URLs. When they are
absent, the app keeps its existing local-only behavior. Keep `.env` private;
do not commit keys.

## Run

Build and run the frontend and local API from the project root:

```bash
npm --prefix frontend run build
python app/server.py
```

Open `http://127.0.0.1:8000`, enter a video idea, and click **Create video**.
The page shows progress as Ollama writes the script, images are generated,
ElevenLabs creates speech, and FFmpeg assembles the final video. Play or
download the result from the page. Click **Stop** during generation to cancel
the current job; FFmpeg is terminated if it is assembling the video. An active
Ollama, ElevenLabs, or Supabase request may finish before cancellation is
handled. For frontend development, run the API in one terminal with
`python app/server.py`, then run
`npm --prefix frontend run dev` in another terminal and open
`http://127.0.0.1:5173`.

Each run is saved in a date-stamped folder under `output/`, with this
structure:

```text
output/YYYY-MM-DD/video-name-time/
├── dialogue.txt
├── narration.mp3
├── video-name.mp4
└── images/
    ├── shot_001.png
    └── ...
```

The generated images are kept there and used to build the 720p MP4. The video
includes the ElevenLabs narration audio. Images are local Pillow illustrations,
not images from an external AI image service.

## Frontend website

The React application includes the Home, Create, Features, Pricing, FAQ,
Contact, Privacy, and Terms routes. Its cinematic media areas are CSS-animated
placeholders by default; no marketing video files are required. See
[`MEDIA.md`](MEDIA.md) for the optional video slot setup.

Install and run the frontend in development:

```bash
npm --prefix frontend install
npm --prefix frontend run dev
```

The Vite development server proxies `/api` and `/output` to the local backend.
For a separately hosted API, set `VITE_API_URL` to its base URL before building.
This value is public configuration, not a place for API keys or secrets.

Check and build the frontend:

```bash
npm --prefix frontend run lint
npm --prefix frontend run format:check
npm --prefix frontend run build
```

For the production deployment used by this project, build the static frontend
and run the Python server:

```bash
npm --prefix frontend run build
python app/server.py
```

The server serves `frontend/dist` and falls back to the React app for direct
visits to application routes such as `/features` and `/privacy`.

### UTubeAgent loader

The shared `UtubeAgentLoader` is an inline-SVG, CSS-only loader used by the
initial page splash. Its five-second loop draws the red U, sends an orange
particle around it, then runs a subtle blue pulse along the inner edge. It
respects reduced-motion preferences.

Reuse it in a component with:

```tsx
import { UtubeAgentLoader } from "./components/UtubeAgentLoader";

function LoadingState() {
  return (
    <UtubeAgentLoader
      size="medium"
      background="transparent"
      text="Initializing AI Agents..."
      progress={42}
    />
  );
}
```

`size` accepts `small`, `medium`, or `large`; `background` accepts
`transparent` or `white`. Omit `progress` for an indeterminate animation or
set `text={null}` to hide the loading message. The header and footer use the
provided logo at `frontend/public/utube-agent-logo.png`.
