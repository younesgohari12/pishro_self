"""Voice/style presets and persistent per-user TTS settings."""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

from config import DATA_DIR

VOICE_SETTINGS_FILE = Path(DATA_DIR) / "voice_settings.json"
DEFAULT_VOICE = "female"

# Each preset has exactly one Gemini voice. There is no fallback voice.
# The style prompts below are the exact prompts requested for each mode.
VOICE_OPTIONS = {
    "male": {
        "label": "👨 مرد",
        "gemini_voice": "Orus",
        "prompt": "Speak with a deep male voice, confident and natural.",
    },
    "female": {
        "label": "👩 زن",
        "gemini_voice": "Sulafat",
        "prompt": "Speak with a warm female voice, clear and natural.",
    },
    "young": {
        "label": "🧑 جوان",
        "gemini_voice": "Leda",
        "prompt": "Speak with a young energetic voice.",
    },
    "old": {
        "label": "👴 پیر",
        "gemini_voice": "Gacrux",
        "prompt": "Speak with an old wise voice.",
    },
    "formal": {
        "label": "🎧 رسمی",
        "gemini_voice": "Charon",
        "prompt": "Speak professionally like a news presenter.",
    },
    "casual": {
        "label": "😎 خودمونی",
        "gemini_voice": "Zubenelgenubi",
        "prompt": "Speak casually like a friendly person.",
    },
    "robotic": {
        "label": "🤖 رباتی",
        "gemini_voice": "Iapetus",
        "prompt": "Speak like an advanced AI assistant.",
    },
    "angry": {
        "label": "😡 عصبانی",
        "gemini_voice": "Fenrir",
        "prompt": "Speak with an angry emotional tone.",
    },
    "kind": {
        "label": "❤️ مهربان",
        "gemini_voice": "Vindemiatrix",
        "prompt": "Speak gently and warmly.",
    },
}

VOICE_ALIASES = {
    "مرد": "male",
    "زن": "female",
    "جوان": "young",
    "پیر": "old",
    "رسمی": "formal",
    "خودمونی": "casual",
    "خودمانی": "casual",
    "رباتی": "robotic",
    "عصبانی": "angry",
    "مهربان": "kind",
    "👨 مرد": "male",
    "👩 زن": "female",
    "🧑 جوان": "young",
    "👴 پیر": "old",
    "🎧 رسمی": "formal",
    "😎 خودمونی": "casual",
    "🤖 رباتی": "robotic",
    "😡 عصبانی": "angry",
    "❤️ مهربان": "kind",
    "❤ مهربان": "kind",
}

VOICE_MENU_TEXT = (
    "انتخاب کن:\n\n"
    "👨 مرد\n"
    "👩 زن\n"
    "🧑 جوان\n"
    "👴 پیر\n"
    "🎧 رسمی\n"
    "😎 خودمونی\n"
    "🤖 رباتی\n"
    "😡 عصبانی\n"
    "❤️ مهربان\n\n"
    "✍️ فقط اسم گزینه را بفرست؛ مثلاً: مهربان"
)

_settings_lock = threading.Lock()


def normalize_voice_choice(text: str) -> str:
    text = (text or "").strip().lower()
    return text.replace("ي", "ی").replace("ك", "ک").replace("‌", "")


def resolve_voice_choice(text: str) -> str | None:
    return VOICE_ALIASES.get(normalize_voice_choice(text))


def _read_settings_unlocked() -> dict[str, str]:
    try:
        data = json.loads(VOICE_SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except (OSError, ValueError, TypeError):
        pass
    return {}


def get_voice_settings() -> dict[str, str]:
    with _settings_lock:
        return _read_settings_unlocked()


def _write_settings_unlocked(data: dict[str, str]) -> None:
    VOICE_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix="voice_settings_",
        suffix=".json.tmp",
        dir=str(VOICE_SETTINGS_FILE.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, VOICE_SETTINGS_FILE)
    finally:
        try:
            os.remove(tmp_name)
        except OSError:
            pass


def get_user_voice(user_id: int | str) -> str:
    with _settings_lock:
        value = _read_settings_unlocked().get(str(user_id), DEFAULT_VOICE)
    return value if value in VOICE_OPTIONS else DEFAULT_VOICE


def set_user_voice(user_id: int | str, voice_key: str) -> None:
    if voice_key not in VOICE_OPTIONS:
        raise ValueError(f"Unknown voice style: {voice_key}")
    with _settings_lock:
        data = _read_settings_unlocked()
        data[str(user_id)] = voice_key
        _write_settings_unlocked(data)


def get_voice_label(voice_key: str) -> str:
    return VOICE_OPTIONS.get(voice_key, VOICE_OPTIONS[DEFAULT_VOICE])["label"]


def get_style_prompt(voice_key: str) -> str:
    return VOICE_OPTIONS.get(voice_key, VOICE_OPTIONS[DEFAULT_VOICE])["prompt"]


def get_gemini_voice(voice_key: str) -> str:
    return VOICE_OPTIONS.get(voice_key, VOICE_OPTIONS[DEFAULT_VOICE])["gemini_voice"]
