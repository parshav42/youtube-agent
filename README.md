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

All YouTube operations use Google's official YouTube Data API v3 and OAuth 2.0. Browser automation, scraping, and unofficial upload paths are not used. Public upload is requested explicitly and verified through the API after insertion.

#### Google Cloud setup and public-upload eligibility

1. Create/select a Google Cloud project owned by the channel operator, enable **YouTube Data API v3**, and enable **YouTube Analytics API** if reports/analytics are required. Confirm the API is enabled for the same project as the OAuth client.
2. Configure the OAuth consent screen with the real app name, support contact, developer contact, and privacy policy/terms URLs required by Google. Add the channel owner as a test user while the app is in Testing. Testing-mode refresh tokens can expire; reauthorization may be needed.
3. Create an OAuth **Web application** client. Register the exact redirect URI `http://127.0.0.1:8000/api/youtube/auth/callback`; set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`. The current callback validator is local-only. Use HTTPS and a hardened callback/auth boundary before any non-local deployment.
4. The app requests `https://www.googleapis.com/auth/youtube.upload` for uploads, `https://www.googleapis.com/auth/youtube.readonly` for channel identity, video reconciliation, and listing, and `https://www.googleapis.com/auth/yt-analytics.readonly` for analytics. Reconnect OAuth after changing scopes.
5. Complete Google's OAuth app verification when the consent screen requires it. OAuth verification and the **YouTube API Services audit** are distinct. YouTube's policy restricts public uploads from API projects that have not passed the required audit; uploads can be forced to private even when the API request asked for public. Follow Google's current API Services audit instructions in the Cloud Console/YouTube API Services documentation and submit the actual app, privacy disclosures, and compliant use case for review. Do not try to work around this restriction. MotivationOS detects a private result and records a clear failure; it will not call that a public success.
6. YouTube Data API's default project quota is commonly 10,000 units/day. A `videos.insert` costs about 1,600 units, so a default of five automatic uploads leaves headroom for other calls. This app enforces both the 10,000-unit budget and `MAX_UPLOADS_PER_DAY`. For more capacity, review actual Cloud Console quota metrics, use the quota-increase request workflow for YouTube Data API v3, explain the compliant use case and expected volume, and wait for Google's approval. A quota request is not a guarantee of an increase.

Generate `GOOGLE_TOKEN_ENCRYPTION_KEY` locally using `.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())`. It encrypts OAuth credentials and resumable upload URLs in SQLite; losing/rotating it requires reauthorization and prevents resuming saved sessions. Set `MOTIVATIONOS_ADMIN_TOKEN` to a locally generated high-entropy secret. Keep the API bound to loopback, disable Uvicorn access logging, and redact OAuth callback query strings.

#### Manual and automatic operation

Manual review remains available and is the default. Complete and review a production, then use `POST /api/publishing/{production_id}/review`, explicitly approve its visibility with `POST /api/publishing/{production_id}/approve`, and upload with `POST /api/publishing/{production_id}/upload`. Public approval requires explicit `confirm_public=true`. The per-production state and resumable upload session survive restarts.

Automatic mode is off by default and requires all of the following:

- Set `MOTIVATIONOS_SCHEDULER_ENABLED=true` to run the in-process scheduler, then set `AUTO_PUBLISH_ENABLED=true` and a five-field UTC `AUTO_PUBLISH_CRON` (default `0 9 * * *`). The `/api/scheduler/run-auto-publish` route runs one protected cycle immediately.
- Set `YOUTUBE_PRIVACY_STATUS=public`. Automatic publishing rejects other visibility values. Set `AUTO_APPROVE_SCRIPTS=true` and `AUTO_APPROVE_PRODUCTIONS=true` only after deciding to delegate both human gates.
- Set thresholds `MIN_SCRIPT_QUALITY_SCORE`, `MIN_AUDIO_QUALITY_SCORE`, and `MIN_VIDEO_QUALITY_SCORE` on a 0–10 scale. Current audio/video QA emit pass/fail rather than numeric scores, so a technical pass is represented as 10 and any other status as 0; audio and video must still report `passed`.
- Set `MAX_UPLOADS_PER_DAY` (default 5), `YOUTUBE_CATEGORY_ID` (default 22), and optionally `YOUTUBE_PLAYLIST_ID`. `YOUTUBE_NOTIFY_SUBSCRIBERS` and `YOUTUBE_MADE_FOR_KIDS` default to false. Audience designation is the operator's responsibility; set the value accurately for the content.
- Complete live Hugging Face configuration, real TTS configuration, FFmpeg/FFprobe setup, and provide one user-authorized scene image per scene plus complete `LICENSES.json` provenance. Missing providers/assets fail closed. The bundled sample research is not used by scheduled automation; it requires the configured Hugging Face research provider.

Every automatic decision checks the script evaluation and policy flags, configured scores, passing audio/video QA, scene asset provenance, and a recorded matching SHA-256 hash. A failed gate stays `needs_review` with reasons on the record. YouTube uploads are unique per production/channel, reserve quota durably, retry with backoff, and reconcile ambiguous outcomes before any retry. A missing OAuth connection, channel mismatch, quota exhaustion, incomplete provenance, failed QA, or unaudited project prevents public success.

Start the local API after configuration:

```bash
set -a; . ./.env; set +a
.venv/bin/uvicorn app.api:app --host 127.0.0.1 --port 8000 --no-access-log
```

Call `GET /api/youtube/auth/start` with the admin Bearer token, visit its returned authorization URL, and complete consent. OAuth state and PKCE are validated; tokens are stored encrypted. `/api/youtube/status` never returns tokens.

Uploads use the official resumable MediaUpload API. Each production has a unique upload record, and its ID is embedded as a description reconciliation marker. Interrupted sessions resume with the encrypted saved session URL. If the outcome is ambiguous and no resumable URL exists, the scheduler searches the authenticated channel for that exact marker and persists an ID only when exactly one match is returned; no match does not authorize re-upload. Inspect `/api/publishing/{production_id}/status`; cancellation retains its audit record. Available thumbnails are uploaded through the official API, and a configured playlist is added after the video upload.

`GET /api/youtube/videos` lists the authenticated channel's uploads. Analytics endpoints query Google and persist only returned metric/date values; empty results are marked unavailable, not fabricated zeroes. The default range is the prior 28 complete days because YouTube metrics may be delayed. Use `/api/analytics/channel` and `/api/analytics/videos/{video_id}` with optional ISO `start_date` and `end_date`.

`POST /api/reports/daily` saves Markdown under `MOTIVATIONOS_REPORT_DIR` (default `data/reports/`) before attempting email. Configure `SMTP_HOST`, `EMAIL_FROM`, `EMAIL_TO`, and, if required, `SMTP_USERNAME`/`SMTP_PASSWORD`; port 587 uses STARTTLS and 465 uses implicit TLS. If email settings are missing, the local report is still saved and email is `not_configured`. SMTP acceptance does not guarantee inbox delivery. An interrupted send is marked `delivery_unknown` and is not resent automatically.

Set `MOTIVATIONOS_SCHEDULER_ENABLED=true` to start the UTC daily report job (default 08:00) and, independently, the auto-publish cron when enabled. Durable SQLite claims prevent duplicate schedule-slot execution; upload claims and uniqueness prevent duplicate production uploads. The API exposes authenticated manual triggers for both jobs. Additive tables and columns preserve existing production, script, and research data.

#### Verify and troubleshoot a live upload

1. Keep `AUTO_PUBLISH_ENABLED=false` while setting up and reviewing. Start the loopback API, complete OAuth, and confirm `/api/youtube/status` reports connected.
2. Complete a production and human review, verify its media and provenance, then create and approve a **public** publishing record. Use `/api/publishing/{production_id}/upload` for the manual path.
3. Confirm the response and `/api/publishing/{production_id}/status` show `status=uploaded`, `visibility=public`, a video ID, and the saved URL. Open the URL in a signed-out/private browser window to confirm public access. The API status is authoritative for the recorded privacy setting; YouTube processing/publication can still take time.
4. For one explicitly approved live smoke test, run `RUN_LIVE_YOUTUBE=1 .venv/bin/python scripts/live_publish_smoke.py <production_id>`. It requires an existing approved public record and prints the verified video URL. Never run it against an unreviewed production.

If an upload is recorded as private after a public request, stop automatic retries and complete the YouTube API Services audit for the OAuth API project; changing the request or creating another project to evade the restriction is not a remedy. For 403 errors, check audit status, OAuth scopes/consent, the authenticated channel ID, API enablement, and quota. `quotaExceeded` pauses retries until the next UTC day. For an ambiguous upload, reconcile first; the app deliberately blocks a second insert if it cannot prove that the first one did not publish.

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

Day 11 adds independent offline checks in `app.audio.quality`. `run_audio_quality_assurance(path, target_duration_seconds=...)` returns file validity, format, duration, size, silence/clipping measurements, target deviation, findings, and a technical status. Defaults: 25 MiB maximum file size, 0.1-second minimum audio duration, 2-second target tolerance, silence below -50 dBFS for at least 0.25 seconds, clipping at or above 0.99 full scale, 10 ms measurement windows, 8 kHz compressed-audio analysis, 120-second analysis cap, and a 30-second FFmpeg timeout. WAV samples are inspected with Python's standard library. Compressed audio is decoded by FFmpeg to mono 8 kHz PCM; those signal measurements are marked approximate and always require review. Missing capabilities are reported as unavailable, not passed. A technical pass does not assess pronunciation, factual correctness, or voice naturalness.

Run the offline QA tests with:

```bash
.venv/bin/pytest -q tests/test_audio_quality.py
```

Day 12 adds `app.audio.review_service.generate_audio_for_human_review`. It requires an approved script ID, an explicit database path, output directory and simple output filename, and an explicitly selected provider. It returns generation metadata and the QA report together; the QA status is informational and never changes script review state or approves audio. The service is library-only: no API route is added because a request could create local files or invoke an explicitly selected network TTS provider. A local caller can present the returned structured report to a human reviewer. `needs_review`, `failed`, and unavailable checks remain visible as returned by the QA engine.

Day 13 refines the same library service into a structured workflow result. `workflow_status` reports `completed`, `blocked`, or `failed`; `generation_status` distinguishes `generated`, `not_run`, `unavailable`, `timed_out`, and `failed`; `qa_status` is passed through from the QA report or is `not_run`. The report includes script ID, provider type, generation metadata, QA findings, and safe error text. Provider details, filesystem paths, and internal exception text are not included in failure messages. A completed workflow is not audio approval: QA `needs_review` and `failed` remain separate human decisions.

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