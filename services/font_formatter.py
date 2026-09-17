"""Automatic Telegram message formatting for the self-bot account.

The feature uses Telegram message entities instead of decorative Unicode
alphabets, so it works with Persian, English and mixed text. Settings are
stored per Telegram user in the project's existing JSON user database.
"""
from __future__ import annotations

from services.access_service import can_run

from telethon import events
from telethon.tl.types import (
    MessageEntityBold,
    MessageEntityCode,
    MessageEntityItalic,
    MessageEntityStrike,
    MessageEntityUnderline,
)

import db


FONT_STYLES = {
    "bold": "𝗕𝗼𝗹𝗱 (ضخیم)",
    "italic": "𝘐𝘵𝘢𝘭𝘪𝘤 (کج)",
    "bold_italic": "𝗕𝗼𝗹𝗱 + 𝘐𝘵𝘢𝘭𝘪𝘤",
    "strike": "خط خورده",
    "underline": "زیر خط دار",
    "monospace": "Monospace",
}

DEFAULT_FONT_STYLE = "bold"


def normalize_font_style(value: str | None) -> str:
    style = str(value or "").strip().lower()
    return style if style in FONT_STYLES else DEFAULT_FONT_STYLE


def utf16_len(text: str) -> int:
    """Telegram entity lengths are measured in UTF-16 code units."""
    return len((text or "").encode("utf-16-le")) // 2


def utf16_offset(text: str, char_index: int) -> int:
    return utf16_len((text or "")[: max(0, int(char_index))])


def build_entities(text: str, style: str, *, start_char: int = 0, end_char: int | None = None):
    """Create Telegram entities for one span of ``text``."""
    style = normalize_font_style(style)
    if end_char is None:
        end_char = len(text)
    start_char = max(0, min(int(start_char), len(text)))
    end_char = max(start_char, min(int(end_char), len(text)))
    offset = utf16_offset(text, start_char)
    length = utf16_len(text[start_char:end_char])
    if length <= 0:
        return []

    if style == "bold":
        return [MessageEntityBold(offset=offset, length=length)]
    if style == "italic":
        return [MessageEntityItalic(offset=offset, length=length)]
    if style == "bold_italic":
        return [
            MessageEntityBold(offset=offset, length=length),
            MessageEntityItalic(offset=offset, length=length),
        ]
    if style == "strike":
        return [MessageEntityStrike(offset=offset, length=length)]
    if style == "underline":
        return [MessageEntityUnderline(offset=offset, length=length)]
    if style == "monospace":
        return [MessageEntityCode(offset=offset, length=length)]
    return []


def should_format_message(message) -> bool:
    """Return True only for safe plain outgoing text messages.

    Existing entities are preserved instead of being overwritten. Dot-commands,
    inline-bot results, media captions and messages with keyboards are skipped.
    """
    text = getattr(message, "raw_text", None) or getattr(message, "message", None) or ""
    if not text.strip():
        return False
    if text.lstrip().startswith("."):
        return False
    if getattr(message, "via_bot_id", None):
        return False
    if getattr(message, "reply_markup", None):
        return False
    if getattr(message, "media", None) is not None:
        return False
    if getattr(message, "entities", None):
        return False
    return True


def register_message_font_handler(client, owner_id: int) -> None:
    """Attach the per-user automatic font handler to one TelegramClient."""
    owner_id = int(owner_id)

    @client.on(events.NewMessage(outgoing=True))
    async def _message_font_handler(event):
        if not can_run(owner_id):
            return
        try:
            settings = db.get_user_settings(owner_id)
            if not settings.get("self_enabled", True):
                return
            if not settings.get("message_font_enabled", False):
                return
            message = getattr(event, "message", None)
            if message is None or not should_format_message(message):
                return

            text = getattr(event, "raw_text", "") or getattr(message, "message", "") or ""
            style = normalize_font_style(settings.get("message_font_style"))
            entities = build_entities(text, style)
            if not entities:
                return

            # Keep exactly the same text and apply only Telegram formatting.
            await event.edit(text, formatting_entities=entities, parse_mode=None)
        except Exception as exc:
            # Font formatting must never break normal self-bot message sending.
            print(f"⚠️ message font handler ({owner_id}): {type(exc).__name__}: {exc}")
