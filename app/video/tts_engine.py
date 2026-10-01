"""Real provider-based speech synthesis with media validation."""

import asyncio
import base64
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.schemas.script_schema import MotivationalScript


class TTSError(RuntimeError):
    """Raised when narration cannot be synthesized or validated."""


@dataclass(frozen=True)
class AudioMetadata:
    path: str
    provider: str
    voice: str
    duration_seconds: float
    codec: str
    word_timings: list[dict[str, float | str]] | None = None
    alignment_source: str | None = None


class EdgeTTSProvider:
    """Synthesize Hindi/Hinglish narration through Microsoft's Edge TTS service."""

    provider_name = "Microsoft Edge TTS"

    def __init__(self, voice: str = "hi-IN-SwaraNeural", timeout_seconds: int = 120) -> None:
        self.voice = voice
        self.timeout_seconds = timeout_seconds

    def synthesize(self, script: MotivationalScript, output_path: str | Path) -> AudioMetadata:
        output = self._generate_text(script.narration, output_path)
        return validate_audio(output, self.provider_name, self.voice)

    def generate(self, approved_script_text: str, output_path: str | Path) -> Path:
        """Generate audio from approved narration text for the audio-provider protocol."""
        return self._generate_text(approved_script_text, output_path)

    def _generate_text(
        self,
        text: str,
        output_path: str | Path,
    ) -> Path:
        if not isinstance(text, str) or not text.strip():
            raise TTSError("Approved script narration is empty.")
        output = Path(output_path)
        if output.exists() or output.is_symlink():
            raise TTSError("Audio output already exists; refusing to overwrite it.")
        try:
            import edge_tts

            output.parent.mkdir(parents=True, exist_ok=True)
            communicator = edge_tts.Communicate(
                text,
                self.voice,
                connect_timeout=10,
                receive_timeout=min(self.timeout_seconds, 120),
            )
            asyncio.run(asyncio.wait_for(communicator.save(str(output)), self.timeout_seconds))
        except asyncio.TimeoutError as error:
            raise TTSError(
                f"TTS request timed out after {self.timeout_seconds}s; check network access and retry."
            ) from error
        except TTSError:
            raise
        except Exception as error:
            message = str(error)
            lowered = message.lower()
            if "401" in lowered or "403" in lowered or "unauthorized" in lowered:
                help_text = "The TTS service rejected the request; verify service access and voice availability."
            elif "429" in lowered or "quota" in lowered or "throttl" in lowered:
                help_text = "The TTS service rate-limited the request; wait and retry."
            else:
                help_text = "Check network access, service availability, and the configured voice name."
            raise TTSError(f"{help_text} Provider error: {message}") from error
        return output


class ElevenLabsTTSProvider:
    """Generate real multilingual narration through the credentialed ElevenLabs API."""

    provider_name = "ElevenLabs Text to Speech"

    def __init__(
        self,
        api_key: str,
        voice_id: str,
        model_id: str = "eleven_multilingual_v2",
        timeout_seconds: float = 90,
        http_client: object | None = None,
    ) -> None:
        if not api_key.strip():
            raise TTSError("ELEVENLABS_API_KEY is required for the ElevenLabs TTS provider.")
        if not voice_id.strip():
            raise TTSError("ELEVENLABS_VOICE_ID is required for the ElevenLabs TTS provider.")
        self._api_key = api_key.strip()
        self.voice = voice_id.strip()
        self.model_id = model_id.strip()
        self.timeout_seconds = timeout_seconds
        self._http_client = http_client

    def synthesize(self, script: MotivationalScript, output_path: str | Path) -> AudioMetadata:
        if not script.narration.strip():
            raise TTSError("Approved script narration is empty.")
        output = Path(output_path)
        if output.exists() or output.is_symlink():
            raise TTSError("Audio output already exists; refusing to overwrite it.")
        try:
            import httpx

            output.parent.mkdir(parents=True, exist_ok=True)
            client = self._http_client
            if client is None:
                with httpx.Client(timeout=self.timeout_seconds) as owned_client:
                    response = self._request(owned_client, script)
            else:
                response = self._request(client, script)
            if response.status_code in {401, 403}:
                raise TTSError("ElevenLabs authentication/permission failed; verify API key and voice access.")
            if response.status_code == 429:
                raise TTSError("ElevenLabs quota/rate limit reached; check account credits and retry later.")
            if response.status_code >= 500:
                raise TTSError(f"ElevenLabs service returned HTTP {response.status_code}; retry later.")
            response.raise_for_status()
            try:
                payload = response.json()
                audio_content = base64.b64decode(payload["audio_base64"], validate=True)
            except (ValueError, KeyError, TypeError) as error:
                raise TTSError("ElevenLabs timestamp response did not contain valid audio_base64.") from error
            has_id3_tag = audio_content.startswith(b"ID3")
            has_mpeg_frame = (
                len(audio_content) > 1
                and audio_content[0] == 0xFF
                and audio_content[1] & 0xE0 == 0xE0
            )
            if len(audio_content) < 512 or not (has_id3_tag or has_mpeg_frame):
                raise TTSError("ElevenLabs returned a response that is not recognizable MP3 audio.")
            output.write_bytes(audio_content)
            alignment = payload.get("normalized_alignment") or payload.get("alignment")
            word_timings = _alignment_words(alignment)
        except TTSError:
            raise
        except Exception as error:
            if error.__class__.__name__ in {"TimeoutException", "ConnectTimeout", "ReadTimeout"}:
                message = "ElevenLabs request timed out; check connectivity and retry."
            else:
                message = f"ElevenLabs network/provider error: {error}"
            raise TTSError(message) from error
        return validate_audio(
            output,
            self.provider_name,
            f"{self.voice} ({self.model_id})",
            word_timings=word_timings,
            alignment_source="ElevenLabs character alignment" if word_timings else None,
        )

    def _request(self, client, script: MotivationalScript):
        return client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice}/with-timestamps",
            params={"output_format": "mp3_44100_128"},
            headers={"xi-api-key": self._api_key, "Accept": "application/json"},
            json={
                "text": script.narration,
                "model_id": self.model_id,
                "language_code": "hi",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            },
        )


def build_tts_provider() -> ElevenLabsTTSProvider | EdgeTTSProvider:
    """Build only an explicitly selected real speech service."""
    provider = os.getenv("MOTIVATIONOS_TTS_PROVIDER", "").strip().lower()
    if not provider:
        raise TTSError("MOTIVATIONOS_TTS_PROVIDER must explicitly select a TTS provider.")
    if provider == "elevenlabs":
        api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
        voice_id = os.getenv("ELEVENLABS_VOICE_ID", "").strip()
        model_id = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2").strip()
        return ElevenLabsTTSProvider(api_key, voice_id, model_id)
    if provider != "edge-tts":
        raise TTSError("Unsupported MOTIVATIONOS_TTS_PROVIDER; supported providers: elevenlabs, edge-tts.")
    voice = os.getenv("EDGE_TTS_VOICE", "hi-IN-SwaraNeural").strip()
    if not voice:
        raise TTSError("EDGE_TTS_VOICE must name an available Hindi/Hinglish-capable voice.")
    return EdgeTTSProvider(voice=voice)


def validate_audio(
    path: str | Path,
    provider: str,
    voice: str,
    word_timings: list[dict[str, float | str]] | None = None,
    alignment_source: str | None = None,
) -> AudioMetadata:
    """Use FFprobe to confirm a non-empty file has a readable audio stream."""
    audio_path = Path(path)
    if not audio_path.is_file() or audio_path.stat().st_size < 512:
        raise TTSError(f"TTS output is missing or too small to contain audio: {audio_path}")
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_streams", "-show_format",
                "-of", "json", str(audio_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        metadata = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        raise TTSError(f"Unable to inspect TTS audio with FFprobe: {error}") from error
    stream = next(
        (item for item in metadata.get("streams", []) if item.get("codec_type") == "audio"),
        None,
    )
    duration = float((stream or {}).get("duration") or metadata.get("format", {}).get("duration") or 0)
    if stream is None or duration <= 0:
        raise TTSError(f"TTS output contains no readable audio stream: {audio_path}")
    return AudioMetadata(
        path=str(audio_path.resolve()),
        provider=provider,
        voice=voice,
        duration_seconds=duration,
        codec=str(stream.get("codec_name", "unknown")),
        word_timings=word_timings,
        alignment_source=alignment_source,
    )


def _alignment_words(alignment) -> list[dict[str, float | str]] | None:
    if not isinstance(alignment, dict):
        return None
    characters = alignment.get("characters")
    starts = alignment.get("character_start_times_seconds")
    ends = alignment.get("character_end_times_seconds")
    if not isinstance(characters, list) or not isinstance(starts, list) or not isinstance(ends, list):
        return None
    if not (len(characters) == len(starts) == len(ends)):
        return None
    words = []
    word_chars = []
    word_start = None
    word_end = None
    for character, start, end in zip(characters, starts, ends, strict=True):
        if not isinstance(character, str):
            return None
        if character.isspace():
            if word_chars:
                words.append({"word": "".join(word_chars), "start": word_start, "end": word_end})
                word_chars = []
                word_start = None
                word_end = None
            continue
        try:
            numeric_start = float(start)
            numeric_end = float(end)
        except (ValueError, TypeError):
            return None
        if numeric_start < 0 or numeric_end < numeric_start:
            return None
        if word_start is None:
            word_start = numeric_start
        word_end = numeric_end
        word_chars.append(character)
    if word_chars:
        words.append({"word": "".join(word_chars), "start": word_start, "end": word_end})
    return words or None