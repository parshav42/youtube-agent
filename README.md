# ElevenLabs Narrated Video Maker

Turn a written story into a narrated MP4. Hugging Face Inference Providers
generate the story script, scene images, and image-to-video clips. ElevenLabs
generates the narration audio, and FFmpeg assembles the animated scenes,
captions, and narration into a 720p MP4.

## Requirements

- Python 3.10+
- Node.js 18+ and npm
- A Hugging Face token with Inference Providers access
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
Configure Hugging Face Inference Providers in `.env`:

```dotenv
HF_TOKEN=your-server-only-token
HF_MODEL=Qwen/Qwen3-14B
HF_BASE_URL=https://router.huggingface.co/v1
HF_IMAGE_MODEL=Tongyi-MAI/Z-Image-Turbo
HF_VIDEO_MODEL=Lightricks/LTX-2
HF_MEDIA_TIMEOUT=600
```

`HF_MODEL`, `HF_BASE_URL`, `HF_IMAGE_MODEL`, `HF_VIDEO_MODEL`, and
`HF_MEDIA_TIMEOUT` default to the values above. The token must have permission
to call Hugging Face Inference Providers. Inference is billed according to the
selected provider and model availability. Keep `HF_TOKEN` private; it is used
only by the backend and must not be added to frontend configuration.
The backend defaults to `LLM_PROVIDER=huggingface`; if `HF_TOKEN` is missing,
generation fails explicitly instead of falling back to a local service.
Deployed environments force Hugging Face for normal provider selections and
reject `LLM_PROVIDER=mock` rather than silently using it. Add `HF_TOKEN` to
the API host's service variables; a local `.env` file is not automatically
available to Railway.

### Zero-cost local text generation

For local development, Ollama can generate dialogue without a hosted text
inference request. [Install Ollama](https://ollama.com/download) and ensure its
service is running (on Linux, run `ollama serve` in a terminal), then download
the default model:

```bash
ollama pull qwen3:1.7b
```

Set these variables in the local `.env`:

```dotenv
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:1.7b
```

The explicit Ollama setting is honored locally even if `HF_TOKEN` is present.
For tests or UI development that only needs deterministic placeholder dialogue,
set `LLM_PROVIDER=mock`; this provider makes no LLM API request and cannot be
selected in a deployed environment.

These settings replace only the **text/dialogue** provider. Full video
generation still uses the configured Hugging Face image/video models and
ElevenLabs text-to-speech integration, which may have their own usage costs.
The local Ollama and mock providers do not add any paid services.

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
The page shows progress as Hugging Face writes the script, images are generated,
ElevenLabs creates speech, and FFmpeg assembles the final video. Play or
download the result from the page. Click **Stop** during generation to cancel
the current job; FFmpeg is terminated if it is assembling the video. An active
Hugging Face, ElevenLabs, or Supabase request may finish before cancellation is
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
    ├── shot_001.mp4
    └── ...
```

Each generated image and its animated clip are kept in the run folder. The
final video includes the ElevenLabs narration audio and captions.

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

## Deploy the website to Vercel

The root [`vercel.json`](vercel.json) configures Vercel to install the frontend,
build the Vite app, publish `frontend/dist`, and serve React Router routes on
direct visits. Import the repository with the **repository root** as the Vercel
project root; do not set it to `frontend`.

The Vercel build clears `VITE_API_URL`, and the rewrites in
[`vercel.json`](vercel.json) proxy `/api` and `/output` requests to the
production Railway API, keeping browser requests on the Vercel origin. If the
Railway domain changes, update both rewrite destinations and redeploy.
For a frontend hosted elsewhere, set `VITE_API_URL` to the API origin before
building; that direct cross-origin setup also requires the frontend origin in
the API's `CORS_ALLOWED_ORIGINS`.

### Video-generation API

Vercel hosts the static website, not this repository's Python video-generation
server. The API uses a long-running HTTP server, background threads, FFmpeg,
Hugging Face Inference Providers, and generated-file storage, so it must run
separately on a persistent Python/container host. The root
[`Dockerfile`](Dockerfile) builds the frontend with
`npm --prefix frontend run build`, then packages `frontend/dist` alongside the
API, Python dependencies, and FFmpeg. Create a Docker web service from the
repository root, expose the port assigned by the host, and use `/health` as its
health-check path if supported.

#### Deploying the full app on Railway

Create a Railway service from this repository and select the root
[`Dockerfile`](Dockerfile) as its builder. Railway will build the frontend into
the image and run the Python server, which serves both the site and API. Do not
set a separate frontend build command or put credentials in Docker build
arguments; configure server credentials as Railway service variables instead.
Railway's assigned `PORT` is used by the server, which binds to `0.0.0.0`.
Configure `/health` as the health-check path if desired. Set the runtime
variables below. Keep the Hugging Face token in Railway's server-side variables:

```dotenv
HF_TOKEN=your-server-only-token
HF_MODEL=Qwen/Qwen3-14B
HF_BASE_URL=https://router.huggingface.co/v1
HF_IMAGE_MODEL=Tongyi-MAI/Z-Image-Turbo
HF_VIDEO_MODEL=Lightricks/LTX-2
LLM_PROVIDER=huggingface
ELEVENLABS_API_KEY=your-server-only-key
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SECRET_KEY=your-server-only-key
```

If a separately hosted frontend calls the API directly instead of using the
Vercel same-origin proxy, set `CORS_ALLOWED_ORIGINS` on the API service.

```dotenv
HF_TOKEN=your-server-only-token
HF_MODEL=Qwen/Qwen3-14B
HF_BASE_URL=https://router.huggingface.co/v1
HF_IMAGE_MODEL=Tongyi-MAI/Z-Image-Turbo
HF_VIDEO_MODEL=Lightricks/LTX-2
LLM_PROVIDER=huggingface
ELEVENLABS_API_KEY=your-server-only-key
CORS_ALLOWED_ORIGINS=https://your-project.vercel.app,https://www.your-domain.com
```

The Docker image listens on `0.0.0.0` and uses the `PORT` supplied by the
hosting service. Set `SUPABASE_URL` and `SUPABASE_SECRET_KEY` on the API host
for persistent jobs and assets, after applying
[`supabase/schema.sql`](supabase/schema.sql).

For browser clients that call the API directly, add their exact origins to
`CORS_ALLOWED_ORIGINS` (no paths); preview deployments need their own origins
if they should access the API. The Vercel same-origin proxy does not require
CORS. CORS is disabled by default and does not replace API authentication or
rate limiting.
Protect the generation endpoint before making a provider-backed API public.
Keep `HF_TOKEN`, `ELEVENLABS_API_KEY`, and Supabase service credentials on the
API host only, never in Vercel's `VITE_*` variables. For cloud hosting, configure
Supabase or another persistent/shared asset store rather than relying on
ephemeral local output files.

After the API deploys, update the Vercel rewrite destinations to its public
base URL and redeploy the frontend.

For a self-hosted deployment that serves both the static frontend and API from
the same process, build the frontend and run the Python server:

```bash
npm --prefix frontend run build
python app/server.py
```

The server serves `frontend/dist` and falls back to the React app for direct
visits to application routes such as `/features` and `/privacy`.

### UtubeAgent loader

The shared `UtubeAgentLoader` is an inline-SVG, CSS-only loader used by the
initial page splash. Its five-second loop draws the red U, sends an orange
particle around it, then moves a blue energy pulse along the inner edge. The
U stays visible through the cycle, and all animated elements are synchronized
to the same five-second loop. It respects reduced-motion preferences.

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
`transparent` or `white`. Omit `progress` for an indeterminate animation,
set `showText={false}` to hide the loading message, or add `fullScreen` to
center the loader in the viewport. The header and footer use a text wordmark
styled in the brand's red, black, and blue.
