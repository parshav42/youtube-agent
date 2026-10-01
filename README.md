# MotivationOS

MotivationOS builds original Hindi-English motivational Shorts. Day 1 research and Day 2 script generation remain in LangGraph; Day 3 adds real audio, licensed visual acquisition, subtitles, video rendering, and resumable production records.

## Architecture

The typed LangGraph retains its original research route and selects the script path for a `script_request` or `selected_topic`. All LLM calls use the centralized `HuggingFaceLLMProvider` and the official `huggingface_hub.InferenceClient`; the application has no OpenAI integration or implicit mock fallback.

```mermaid
flowchart TD
    START([START]) --> route{Input}
    route -->|Research| hf_research[HF research or labeled Day 1 sample ideas]
    hf_research --> scoring --> selection --> research_db[(SQLite research_runs)]
    route -->|Script request| generation[HF structured script generation]
    generation --> story[HF story structure]
    story --> scenes[HF scene planning]
    scenes --> quality[HF qualitative review + deterministic checks]
    quality -->|Fail, up to 2 revisions| generation
    quality -->|Approved| scripts_db[(SQLite scripts)]
    scripts_db --> metadata[HF production metadata]
    metadata --> tts[ElevenLabs or explicitly selected Edge TTS]
    tts --> visuals[Licensed local scene assets]
    visuals --> subtitles[Approximate phrase SRT]
    subtitles --> ffmpeg[FFmpeg H.264/AAC render]
    ffmpeg --> media[FFprobe validation]
    media --> production_db[(SQLite productions)]
```

Research, story planning, scene planning, script generation, quality review, and video metadata share Hugging Face. Static Day 1 seed research is a separately labeled source and is not an LLM fallback. TTS and visual sourcing are independent media providers.

## Install

Python 3.10+, FFmpeg with `libx264`, AAC, and the `subtitles`/libass filter are required. FFprobe must be on `PATH`.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
```

On Debian/Ubuntu, install the media tools with `sudo apt install ffmpeg fonts-noto-core`. The Noto Sans Devanagari font is requested for burned-in Hindi captions; libass/fontconfig may use an installed fallback. Confirm support with `ffmpeg -filters | grep subtitles` and `ffmpeg -encoders | grep libx264`.

## Configuration

### Hugging Face LLM

- `HF_TOKEN` is required for script generation, HF research, story/scene planning, quality review, metadata, and `--check-hf`. Create a Hugging Face user token with access to Inference Providers and keep it in your ignored local `.env`.
- `HF_MODEL` is the exact model repository/identifier supported by an available Inference Provider.
- `HF_PROVIDER` selects a supported Inference Provider or `auto`.
- `HF_TIMEOUT_SECONDS` and `HF_MAX_RETRIES` bound request time and retries; authentication and missing-model errors fail with configuration guidance.
- Hugging Face Inference Providers have model/provider-specific availability, quotas, and billing. Do not assume a model is free or unlimited; check current account/provider pricing before generating.

Connectivity uses an actual model inference request and reports the configured model/provider and returned response without displaying the token:

```bash
python -m app.main --check-hf
```

### YouTube publishing and analytics (Day 4)

The local FastAPI service uses Google's official YouTube Data API v3 and YouTube Analytics API. It does not change or replace the Day 1–3 LangGraph/video workflows.

- In Google Cloud, enable **YouTube Data API v3** and **YouTube Analytics API**, configure the OAuth consent screen, and add the channel owner as a test user where applicable. Do not bypass Google's verification or app restrictions.
- Create an OAuth **Web application** client with the exact authorized redirect URI `http://127.0.0.1:8000/api/youtube/auth/callback`; set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`. The current callback validator is local-only. Use HTTPS and a hardened callback/auth boundary before non-local deployment.
- Set `GOOGLE_TOKEN_ENCRYPTION_KEY` to a Fernet key generated locally, for example with `.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`. It encrypts OAuth credentials and resumable upload session URLs in SQLite. Back it up securely; losing/rotating it requires Google reauthorization and prevents resuming saved upload sessions.
- Set `MOTIVATIONOS_ADMIN_TOKEN` to a locally generated high-entropy secret. Every administrative route requires it as a Bearer token. Keep the API bound to loopback; do not expose it publicly without TLS and a hardened authentication/deployment boundary. The OAuth callback carries a one-time authorization code in its query string; disable Uvicorn access logging and configure any reverse proxy to redact callback query strings.
- The OAuth flow requests only `youtube.upload`, `youtube.readonly`, and `yt-analytics.readonly`. Google may require consent-screen setup, test-user access, verification, or reauthorization depending on project state and account policy. OAuth Testing mode may restrict refresh-token lifetime; monitor `/api/youtube/status` and reauthorize when required.

Start the local API:

```bash
set -a; . ./.env; set +a
.venv/bin/uvicorn app.api:app --host 127.0.0.1 --port 8000 --no-access-log
```

Call `GET /api/youtube/auth/start` with the admin Bearer token, visit its returned authorization URL, and complete consent. The loopback callback validates single-use OAuth state and PKCE, then stores encrypted credentials. `/api/youtube/status` reports configuration/connection state without returning tokens.

Publishing requires three separate human actions. A completed production must have its actual `final.mp4` and Day 3 `metadata.json`. `POST /api/publishing/{production_id}/review` validates both and snapshots the generated title, description, tags, language, and category for inspection. `POST /api/publishing/{production_id}/approve` requires `{"confirm_approval":true}`; public visibility additionally requires `{"confirm_public":true}`. Only then can `POST /api/publishing/{production_id}/upload` upload. Visibility defaults to private, and public publishing is never scheduled. An upload is reported as successful only after YouTube returns a video ID.

Uploads use the official resumable MediaUpload API. Each production has a unique upload record, and its ID is embedded as a description reconciliation marker. Interrupted sessions resume with the encrypted saved session URL. If the outcome is ambiguous and no resumable URL exists, automatic re-upload is blocked. `POST /api/publishing/{production_id}/reconcile` searches the authenticated channel for that exact marker and persists an ID only when exactly one match is returned. No match does not authorize automatic re-upload. Inspect durable state at `/api/publishing/{production_id}/status`; cancellation retains its audit record.

`GET /api/youtube/videos` lists the authenticated channel's uploads. Analytics endpoints query Google and persist only returned metric/date values; empty results are marked unavailable, not fabricated zeroes. The default range is the prior 28 complete days because YouTube metrics may be delayed. Use `/api/analytics/channel` and `/api/analytics/videos/{video_id}` with optional ISO `start_date` and `end_date`.

`POST /api/reports/daily` saves Markdown under `MOTIVATIONOS_REPORT_DIR` (default `data/reports/`) before attempting email. Configure `SMTP_HOST`, `EMAIL_FROM`, `EMAIL_TO`, and, if required, `SMTP_USERNAME`/`SMTP_PASSWORD`; port 587 uses STARTTLS and 465 uses implicit TLS. If email settings are missing, the local report is still saved and email is `not_configured`. SMTP acceptance does not guarantee inbox delivery. An interrupted send is marked `delivery_unknown` and is not resent automatically.

Set `MOTIVATIONOS_SCHEDULER_ENABLED=true` to start the in-process UTC daily analytics/report job (default 08:00 UTC). It never uploads or publishes. SQLite stores job claims and limits retries to three; duplicate daily executions are blocked. The API also exposes an authenticated manual trigger. All new tables are additive; existing production, script, and research rows are retained.

Google enforces project-level API quotas, OAuth consent/testing policies, channel permissions, and upload restrictions. Check current quota in Google Cloud Console before production use. Unverified YouTube API projects may be restricted to private visibility; respect the current project-specific restriction and API terms. Quota exhaustion, API enablement errors, delayed metrics, or revoked credentials are surfaced as failures, not successful operations.

### Narration

- The existing production TTS path requires an explicit `MOTIVATIONOS_TTS_PROVIDER` selection; it is unset by default. Selecting `elevenlabs` requires `ELEVENLABS_API_KEY` and an account-authorized `ELEVENLABS_VOICE_ID`. `ELEVENLABS_MODEL_ID` defaults to `eleven_multilingual_v2`. Provider usage is billed and quota-limited according to the current account plan.
- `MOTIVATIONOS_TTS_PROVIDER=edge-tts` explicitly selects the Edge TTS network service with `EDGE_TTS_VOICE` (default `hi-IN-SwaraNeural`). It has no API-key setting, but depends on network/service availability and applicable service terms. No synthetic/fake audio fallback exists.
- Audio is probed by FFprobe before it is accepted.

Day 9 adds an independent, opt-in audio foundation in `app.audio.generation`. It accepts only scripts already marked approved with review metadata and a passing evaluation, and reads their SQLite record without changing review state or creating production records. `build_audio_provider("mock")` selects a deterministic tone WAV fixture explicitly labeled as not speech; real providers are not selected or called by this module. `validate_audio_file` accepts WAV, MP3, M4A, AAC, FLAC, and OGG files, enforces a 25 MiB default limit (customizable with `max_size_bytes`), and uses FFprobe for compressed formats. The module is not connected to the video production workflow.

Run its offline tests with:

```bash
.venv/bin/pytest -q tests/test_audio_generation.py
```

Day 10 adds an opt-in Edge TTS adapter to the same interface. `MOTIVATIONOS_AUDIO_PROVIDER` is unset by default; select `mock` for a local tone fixture or `edge-tts` to explicitly allow the Edge network service. `MOTIVATIONOS_AUDIO_TIMEOUT_SECONDS` sets the request timeout, and the existing `EDGE_TTS_VOICE` selects the voice. The new module rejects ElevenLabs selection and never reads its credentials. Real generation remains a direct, explicit call to `generate_audio_for_approved_script`; it does not enter the video production, publishing, or scheduling workflows. Tests replace the Edge client and do not contact the service.

### Licensed visuals

Set `MOTIVATIONOS_LICENSED_ASSET_DIR` to a directory of user-supplied, authorized still images. Day 3 does not scrape sites, follow arbitrary URLs, download creator content, or fabricate replacement assets. Provide one image for each scene, using `scene_01.jpg`, `scene_02.png`, etc. Supported formats: JPEG, PNG, WebP; minimum dimensions 320x320. Add a `LICENSES.json` file keyed by each exact filename:

```json
{
  "scene_01.jpg": {
    "source": "Provider or original creator/source record",
    "license": "License name and terms",
    "rights_holder": "Rights holder",
    "attribution": "Attribution required by the license, if any"
  }
}
```

The manifest is an operator-provided provenance assertion; MotivationOS does not independently adjudicate rights. Invalid, missing, or undocumented assets fail closed.

### Paths

- `MOTIVATIONOS_DB_PATH` defaults to `data/motivationos.sqlite3`. `scripts` and `productions` tables are additive; existing research data is preserved.
- `MOTIVATIONOS_SPEAKING_RATE_WPS` defaults to `2.5`; duration checks allow narration within a bounded range around that assumption.
- `MOTIVATIONOS_OUTPUT_DIR` defaults to `data/productions`. Each run receives a unique production ID directory containing `assets/`, `captions.srt`, `final.mp4`, and `metadata.json` when those stages complete.
- `LANGSMITH_*` only configure optional tracing and do not replace the HF model provider.

## Commands

Generate a Day 2 script (requires HF configuration):

```bash
python -m app.main --topic "Consistency during exam season" --duration 35
```

Every generated script is saved as `needs_review`, even when its automated quality evaluation passes. A passing evaluation makes it eligible for human review; it is not approval. The `--draft-for-review` flag remains as a deprecated compatibility option.

Use the authenticated local API to review scripts:

- `GET /api/scripts/review` lists scripts awaiting review.
- `GET /api/scripts/{script_id}/review` returns the script's current review state.
- `POST /api/scripts/{script_id}/approve` explicitly approves a passing script.
- `POST /api/scripts/{script_id}/reject` explicitly rejects a script.

Approval and rejection require the admin Bearer token and a JSON body containing `{"confirm_review": true, "reviewer": "your-reviewer-id"}`. Script review is separate from YouTube publishing approval. The review endpoints only update script review state; they do not start production or upload a video.

Unreviewed legacy scripts with status `completed` also appear in the review queue. They are not production-approved; an admin must make a new confirmed decision. The review details response includes the script and its quality evaluation so the reviewer can inspect both before deciding.

### Local Script Review Dashboard (Day 8)

The same FastAPI service serves a local review UI at `http://127.0.0.1:8000/review`. It uses the existing review endpoints and has no production, scheduling, or publishing controls. On the sign-in form, enter a reviewer name and the value configured as `MOTIVATIONOS_ADMIN_TOKEN`; the token stays in page memory only and is cleared on sign-out or when the tab closes. The browser sends it only as the Bearer token to the same-origin local API.

Start the dashboard from the project directory. The `.env` file must already contain a locally configured admin token; do not paste or print it in a command:

```bash
cd ~/MotivationOS
source .venv/bin/activate
set -a
. ./.env
set +a
export MOTIVATIONOS_SCHEDULER_ENABLED=false
.venv/bin/uvicorn app.api:app --host 127.0.0.1 --port 8000 --no-access-log
```

Open `http://127.0.0.1:8000/review` in your browser. The service is bound to loopback and must not be exposed publicly. The UI does not persist the admin token. Approving only changes script review status and makes an eligible script available to the separate production command; it never starts production. Rejection records the decision and removes the script from the review queue. These decisions update the database selected by `MOTIVATIONOS_DB_PATH` (default `data/motivationos.sqlite3`), so use a separate temporary database when exploring with test data.

Run the focused dashboard and review tests, check JavaScript syntax, and compile Python with:

```bash
.venv/bin/pytest -q tests/test_review_dashboard.py tests/test_script_review.py
node --check app/static/review.js
python -m compileall -q app
git diff --check
```

Run the full offline test suite with:

```bash
.venv/bin/pytest -q
```

For local development, set an admin token in your shell and start the API on loopback with scheduled jobs disabled:

```bash
export MOTIVATIONOS_ADMIN_TOKEN='replace-with-a-local-test-value'
export MOTIVATIONOS_SCHEDULER_ENABLED=false
.venv/bin/uvicorn app.api:app --host 127.0.0.1 --port 8000 --no-access-log
```

In another terminal, list pending and legacy scripts or inspect one script. Replace `1` with an ID from the list:

```bash
curl -H "Authorization: Bearer $MOTIVATIONOS_ADMIN_TOKEN" http://127.0.0.1:8000/api/scripts/review
curl -H "Authorization: Bearer $MOTIVATIONOS_ADMIN_TOKEN" http://127.0.0.1:8000/api/scripts/1/review
```

Approval or rejection requires a deliberate POST with `confirm_review: true`; for example:

```bash
curl -X POST http://127.0.0.1:8000/api/scripts/1/approve \\
  -H "Authorization: Bearer $MOTIVATIONOS_ADMIN_TOKEN" \\
  -H 'Content-Type: application/json' \\
  -d '{"confirm_review":true,"reviewer":"local-admin"}'
```

Run the isolated review, database, and script-workflow tests with:

```bash
.venv/bin/pytest -q tests/test_script_review.py tests/test_database.py tests/test_script_workflow.py
```

This local review walkthrough does not require an LLM call. Production remains separately gated on a passing evaluation and recorded human approval; the API review routes do not render video, upload or schedule YouTube content, or send email. The Day 7 verification uses temporary databases and mocked production gates. Real media rendering, YouTube actions, email delivery, and paid-provider behavior are not part of that verification.

Retain Day 1's bundled sample research, or explicitly use HF research:

```bash
python -m app.main --research
python -m app.main --research-with-hf --niche "Hinglish discipline"
```

Produce a video from a script-table record only after explicit script approval. Legacy `completed` records are not treated as approved:

```bash
python -m app.main --produce-script 2
```

The command prints the actual production status and MP4 path only after FFprobe validation. On failure, it persists the error and intermediate metadata and prints a resume command:

```bash
python -m app.main --resume-production <PRODUCTION_ID>
```

Production runs the HF metadata stage, TTS, local licensed asset acquisition, SRT generation, FFmpeg render, and FFprobe verification. Subtitles are UTF-8 phrase captions with approximate scene/word-count timing; no forced aligner is currently integrated. The renderer creates 1080x1920, 9:16, 30 FPS H.264/AAC MP4 with burned captions and scene transitions.

## Production Recovery and Tests

Production states (`queued`, `generating_audio`, `acquiring_visuals`, `generating_subtitles`, `rendering`, `completed`, `failed`) and stage metadata are stored in SQLite and mirrored to `metadata.json`. Failures preserve validated intermediate assets. `--resume-production` reuses a probed narration file and reacquires/revalidates visuals before rendering.

Run all tests:

```bash
pytest -v
```

Tests use temporary generated images, an explicitly marked tone-audio fixture, and injected fake model/TTS providers. The production integration test invokes the real local FFmpeg and FFprobe binaries and validates the rendered MP4; it does not contact HF or ElevenLabs, use real licensed footage, or claim an actual user production was created. A real production requires valid provider configuration and a fully documented asset library.

## Quality and Limitations

Quality scores combine local checks (language, speaking pace, repetition, beat labels, caption-to-scene alignment, factual claims, and outcome guarantees) with HF qualitative review. The per-check report is retained with each new script row; scores are not engagement predictions. Originality/factual checks are safeguards, not guarantees. HF provider availability, quota, and cost vary by model and account. Hinglish pronunciation depends on the selected TTS voice. SRT alignment is approximate. Local image licensing information must be supplied and verified by the operator. No video is produced if credentials, authorized assets, TTS, FFmpeg, or media validation fail.

Day 3 leaves YouTube upload/publishing, a forced-alignment transcription provider, and automatic licensed image/video generation for future integrations. No API secret belongs in source control.