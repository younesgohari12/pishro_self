"""AvalAI-only Gemini TTS implementation using the OpenAI Python SDK.

The client talks only to https://api.avalai.ir/v1 and is locked to
``gemini-2.5-flash-tts``. There is intentionally no provider/model/voice fallback.
"""
from __future__ import annotations

import asyncio
import base64
import re
import uuid
from pathlib import Path
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from config import (
    AVALAI_API_KEY,
    AVALAI_BASE_URL,
    AVALAI_REQUEST_TIMEOUT,
    AVALAI_TTS_MAX_BYTES,
    AVALAI_TTS_MAX_CHARS,
    AVALAI_TTS_MODEL,
    UPLOAD_DIR,
)
from .voices import get_gemini_voice, get_style_prompt, get_user_voice


class TTSError(RuntimeError):
    """TTS error whose message is suitable for direct display to the user."""


def _real_api_error(exc: Exception) -> str:
    """Extract AvalAI's real error body/message when the SDK exposes it."""
    if isinstance(exc, APIStatusError):
        try:
            body = exc.response.json()
        except Exception:
            try:
                body = exc.response.text
            except Exception:
                body = None

        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"])
            for key in ("message", "detail"):
                if body.get(key):
                    return str(body[key])
        if body:
            return str(body)
    return str(exc)


def _detect_language_code(text: str) -> str:
    """Choose Persian or English for this FA/EN bot without another API call."""
    persian_chars = len(re.findall(r"[\u0600-\u06FF]", text))
    latin_chars = len(re.findall(r"[A-Za-z]", text))
    return "fa-IR" if persian_chars >= latin_chars and persian_chars else "en-US"


def _create_speech_file_sync(
    text: str,
    style_prompt: str,
    gemini_voice: str,
    language_code: str,
    output_path: Path,
) -> None:
    """Call AvalAI's native Gemini TTS route once and write the returned MP3."""
    client = OpenAI(
        api_key=AVALAI_API_KEY,
        base_url=AVALAI_BASE_URL,
        timeout=AVALAI_REQUEST_TIMEOUT,
        max_retries=0,
    )

    # OpenAI SDK supports custom/undocumented endpoint requests via client.post().
    # AvalAI's /v1/text:synthesize endpoint exposes Gemini's style-prompt field
    # directly, so the user's requested prompt is not mixed into the spoken text.
    # IMPORTANT: never use ``cast_to=dict`` here.
    # openai-python 3.x currently has a known bare-dict deserialization bug
    # that raises: ValueError: not enough values to unpack (expected 2, got 0).
    # Parameterizing the mapping keeps us on the OpenAI SDK while avoiding
    # that SDK bug for AvalAI's native JSON response.
    response = client.post(
        "/text:synthesize",
        cast_to=dict[str, Any],
        body={
            "input": {
                "prompt": style_prompt,
                "text": text,
            },
            "voice": {
                "languageCode": language_code,
                "name": gemini_voice,
                "model_name": AVALAI_TTS_MODEL,
            },
            "audioConfig": {
                "audioEncoding": "MP3",
            },
        },
    )

    if not isinstance(response, dict):
        raise TTSError(f"پاسخ نامعتبر AvalAI: {response!r}")

    encoded = response.get("audioContent")
    if not encoded:
        raise TTSError(f"پاسخ AvalAI فاقد audioContent است: {response}")

    try:
        audio_bytes = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise TTSError(f"audioContent برگشتی AvalAI معتبر نیست: {exc}") from exc

    if not audio_bytes:
        raise TTSError("AvalAI فایل صوتی خالی برگرداند.")

    output_path.write_bytes(audio_bytes)


def _validate_configuration() -> None:
    if not AVALAI_API_KEY or AVALAI_API_KEY == "YOUR_AVALAI_API_KEY":
        raise TTSError("کلید AvalAI را در فایل config.py داخل AVALAI_API_KEY وارد کن.")
    if AVALAI_BASE_URL.rstrip("/") != "https://api.avalai.ir/v1":
        raise TTSError("AVALAI_BASE_URL باید دقیقاً https://api.avalai.ir/v1 باشد.")
    if AVALAI_TTS_MODEL != "gemini-2.5-flash-tts":
        raise TTSError("مدل TTS باید دقیقاً gemini-2.5-flash-tts باشد.")


async def text_to_speech(text, user_id):
    """Create one MP3 for ``text`` using the user's saved TTS style.

    1. Load the user's selected voice/style.
    2. Load that style's exact prompt.
    3. Send exactly one AvalAI Gemini TTS request.
    4. Save the MP3.
    5. Return the file path.
    """
    _validate_configuration()

    text = str(text or "").strip()
    if not text:
        raise TTSError("متن برای ساخت صوت خالی است.")
    if len(text) > AVALAI_TTS_MAX_CHARS:
        raise TTSError(
            f"متن بیشتر از حد مجاز {AVALAI_TTS_MAX_CHARS} کاراکتر است."
        )
    text_bytes = len(text.encode("utf-8"))
    if text_bytes > AVALAI_TTS_MAX_BYTES:
        raise TTSError(
            f"متن بیشتر از حد مجاز AvalAI است: {text_bytes} بایت؛ حداکثر {AVALAI_TTS_MAX_BYTES} بایت."
        )

    voice_key = get_user_voice(user_id)
    style_prompt = get_style_prompt(voice_key)
    gemini_voice = get_gemini_voice(voice_key)
    language_code = _detect_language_code(text)

    output_dir = Path(UPLOAD_DIR) / "tts"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"avalai_{user_id}_{uuid.uuid4().hex}.mp3"

    try:
        await asyncio.to_thread(
            _create_speech_file_sync,
            text,
            style_prompt,
            gemini_voice,
            language_code,
            output_path,
        )
    except TTSError:
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    except (APIStatusError, APIConnectionError, APITimeoutError) as exc:
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise TTSError(_real_api_error(exc)) from exc
    except Exception as exc:
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise TTSError(_real_api_error(exc)) from exc

    if not output_path.is_file() or output_path.stat().st_size == 0:
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise TTSError("AvalAI فایل MP3 معتبر برنگرداند.")

    return str(output_path)
