"""Shared Custom Emoji service for the main bot and self-bot.

All Custom Emoji extraction/entity creation/sending is centralized here so
both runtimes use the same UTF-16-safe implementation.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Iterable

from telethon.tl.functions.messages import GetCustomEmojiDocumentsRequest
from telethon.tl.types import DocumentAttributeCustomEmoji, MessageEntityCustomEmoji

from services.identity_service import DIGITS


INVALID_CUSTOM_EMOJI = (
    '❌ این Document ID معتبر نیست یا دسترسی به Custom Emoji وجود ندارد.'
)


class EmojiError(ValueError):
    """A user-facing custom emoji validation/sending error."""


@dataclass(frozen=True)
class CustomEmojiEntityPayload:
    """Prepared text and the real Telegram entity used to send a custom emoji."""

    text: str
    entities: tuple[MessageEntityCustomEmoji, ...]
    document_id: int


def parse_document_id(value) -> int:
    if isinstance(value, str):
        value = value.strip().translate(DIGITS)
        if not re.fullmatch(r'[0-9]{1,19}', value):
            raise EmojiError('یک document_id عددی معتبر وارد کنید.')
        value = int(value)
    if type(value) is not int or not 0 < value < (1 << 63):
        raise EmojiError('شناسهٔ ایموجی خارج از محدوده است.')
    return value


def utf16_length(text: str) -> int:
    return len((text or '').encode('utf-16-le')) // 2


def _message_text(message) -> str:
    # Telethon uses Message.message for both ordinary text and media captions.
    return getattr(message, 'message', None) or getattr(message, 'raw_text', None) or ''


def extract_custom_emojis(message) -> list[dict]:
    """Extract MessageEntityCustomEmoji records in visual/message order.

    Returned offsets and lengths are Telegram UTF-16 code-unit offsets.
    Ordinary Unicode emoji without MessageEntityCustomEmoji are ignored.
    """
    text = _message_text(message)
    raw = text.encode('utf-16-le')
    result: list[dict] = []

    for entity in getattr(message, 'entities', None) or []:
        if not isinstance(entity, MessageEntityCustomEmoji):
            continue
        try:
            document_id = parse_document_id(entity.document_id)
            offset = entity.offset
            length = entity.length
            if (
                type(offset) is not int
                or type(length) is not int
                or offset < 0
                or length <= 0
                or (offset + length) * 2 > len(raw)
            ):
                continue
            emoji_text = raw[offset * 2:(offset + length) * 2].decode('utf-16-le')
        except (EmojiError, UnicodeError):
            continue

        result.append({
            'document_id': document_id,
            'offset': offset,
            'length': length,
            'emoji_text': emoji_text,
            # Requested database field name. Kept alongside emoji_text for
            # backwards compatibility with the existing project/tests.
            'emoji': emoji_text,
        })

    return sorted(result, key=lambda item: item['offset'])


def create_custom_emoji(text: str, document_id) -> CustomEmojiEntityPayload:
    """Create a real MessageEntityCustomEmoji payload; never send a bare ID."""
    document_id = parse_document_id(document_id)
    if not isinstance(text, str) or not text:
        raise EmojiError('متن ایموجی برای ساخت Entity خالی است.')
    entity = MessageEntityCustomEmoji(
        offset=0,
        length=utf16_length(text),
        document_id=document_id,
    )
    return CustomEmojiEntityPayload(text=text, entities=(entity,), document_id=document_id)


async def resolve_custom_emoji(client, document_id) -> CustomEmojiEntityPayload:
    """Resolve Telegram's canonical alt text and build a real entity payload."""
    document_id = parse_document_id(document_id)
    documents = await asyncio.wait_for(
        client(GetCustomEmojiDocumentsRequest(document_id=[document_id])),
        timeout=20,
    )
    for document in documents or []:
        if getattr(document, 'id', None) != document_id:
            continue
        for attribute in getattr(document, 'attributes', None) or []:
            if isinstance(attribute, DocumentAttributeCustomEmoji) and attribute.alt:
                return create_custom_emoji(attribute.alt, document_id)
    raise EmojiError(INVALID_CUSTOM_EMOJI)


def contains_custom_emoji(message, document_id) -> bool:
    document_id = parse_document_id(document_id)
    return any(
        isinstance(entity, MessageEntityCustomEmoji)
        and getattr(entity, 'document_id', None) == document_id
        for entity in getattr(message, 'entities', None) or []
    )


async def send_custom_emoji(entity: CustomEmojiEntityPayload, *, client, peer, timeout: int = 20):
    """Send a prepared payload using Telegram formatting entities and verify it.

    ``entity`` is intentionally the first argument so all callers pass the
    entity object itself, not a document_id as message text.
    """
    if not isinstance(entity, CustomEmojiEntityPayload):
        raise TypeError('entity must be CustomEmojiEntityPayload')
    sent = await asyncio.wait_for(
        client.send_message(
            peer,
            entity.text,
            formatting_entities=list(entity.entities),
            parse_mode=None,
        ),
        timeout=timeout,
    )
    if not contains_custom_emoji(sent, entity.document_id):
        raise EmojiError(
            INVALID_CUSTOM_EMOJI + ' تلگرام Entity را تأیید نکرد؛ نمایش ایموجی تأیید نشد.'
        )
    return sent


async def build_test_message(client, document_id):
    """Compatibility helper used by existing tests/callers."""
    payload = await resolve_custom_emoji(client, document_id)
    prefix = '🧪 تست ایموجی: '
    text = prefix + payload.text
    entity = MessageEntityCustomEmoji(
        offset=utf16_length(prefix),
        length=utf16_length(payload.text),
        document_id=payload.document_id,
    )
    return text, [entity]


def _short_emoji(value: str, max_chars: int = 16) -> str:
    value = (value or '').replace('\n', ' ').replace('\r', ' ')
    return value if len(value) <= max_chars else value[:max_chars] + '…'


def result_pages(items: Iterable[dict]) -> list[str]:
    """Render extraction details without exceeding Telegram message limits."""
    items = list(items)
    if not items:
        return []
    pages = []
    # Preserve the existing 80-record paging contract while keeping each row
    # compact enough for Telegram's message limit.
    for start in range(0, len(items), 80):
        chunk = items[start:start + 80]
        lines = [
            '✨ **Custom Emoji پیدا شد**',
            '',
            f'📦 تعداد کل: **{len(items)}**',
            'نوع: **Custom Emoji**',
            'وضعیت: **قابل استفاده**',
            '',
        ]
        for index, item in enumerate(chunk, start + 1):
            emoji = _short_emoji(item.get('emoji_text') or item.get('emoji') or '')
            lines.append(
                f"{index}. e:{emoji} `{item['document_id']}` "
                f"o:{int(item.get('offset', 0))} l:{int(item.get('length', utf16_length(emoji) or 1))}"
            )
        pages.append('\n'.join(lines))
    return pages


def detailed_result_text(items: Iterable[dict]) -> str:
    """Human-friendly report used by the self-bot extraction workflow."""
    items = list(items)
    lines = ['✨ Custom Emoji پیدا شد', '']
    for index, item in enumerate(items, 1):
        emoji = item.get('emoji_text') or item.get('emoji') or ''
        lines.extend([
            f'{index}) {emoji}',
            '🆔 Document ID:',
            f"▫️ {item['document_id']}",
            f"Offset: {item.get('offset', 0)}",
            f"Length: {item.get('length', utf16_length(emoji) or 1)}",
            f'Emoji Text: {emoji}',
            'نوع: Custom Emoji',
            'وضعیت: قابل استفاده',
            '',
        ])
    return '\n'.join(lines).rstrip()


# One complete emoji token, including keycaps, flags, modifiers, tags and ZWJ.
# Broad symbol coverage is deliberate: only a matching Telegram alt can ever be
# injected. This is not a 39-item application whitelist or a Unicode NLP model.
_EMOJI_BASE = (r"[\U0001F000-\U0001F1E5\U0001F200-\U0001FAFF"
               r"\u2190-\u21FF\u2300-\u23FF\u2600-\u27BF\u2934\u2935\u2B00-\u2BFF"
               r"\u25AA\u25AB\u25B6\u25C0\u25FB-\u25FE"
               r"\u00A9\u00AE\u203C\u2049\u2122\u2139\u3030\u303D\u3297\u3299]")
_EMOJI_COMPONENT = r"(?![\U0001F3FB-\U0001F3FF])" + _EMOJI_BASE + r"[\ufe0e\ufe0f]?[\U0001F3FB-\U0001F3FF]?"
EMOJI_PATTERN = re.compile(
    r"[\U0001F1E6-\U0001F1FF]{2}|[0-9#*]\ufe0f?\u20e3|" + _EMOJI_COMPONENT
    + r"(?:\u200d" + _EMOJI_COMPONENT + r")*(?:[\U000E0020-\U000E007E]+\U000E007F)?"
)


def whole_emoji(text, start, end):
    """Reject matches which are only a fragment of an emoji sequence."""
    def continuation(char):
        return (char in '\u200d\ufe0e\ufe0f\u20e3'
                or '\U0001f3fb' <= char <= '\U0001f3ff'
                or '\U000e0020' <= char <= '\U000e007f')
    if start and text[start - 1] == '\u200d':
        return False
    if end < len(text) and continuation(text[end]):
        return False
    if '\U0001f1e6' <= text[start] <= '\U0001f1ff':
        if start and '\U0001f1e6' <= text[start - 1] <= '\U0001f1ff':
            return False
        if end < len(text) and '\U0001f1e6' <= text[end] <= '\U0001f1ff':
            return False
    return True


@dataclass(frozen=True)
class CustomEmojiDocument:
    document_id: int
    alt: str
    free: bool
    animated: bool
    mime_type: str = ''

    @property
    def media_kind(self):
        return 'video' if self.mime_type == 'video/webm' else ('animated' if self.animated else 'static')


@dataclass(frozen=True)
class EmojiResolution:
    documents: tuple[CustomEmojiDocument, ...]
    rejected: dict[int, str]
    unresolved: dict[int, str]
    retry_after: int = 0


async def resolve_custom_emoji_catalogue(client, document_ids, *, timeout=30):
    """Resolve only requested IDs. Distinguish invalid data from network failure.

    Isolate a server-rejected document by bounded bisection. A timeout/FloodWait
    preserves completed batches; it must not label unqueried IDs as invalid.
    No Telegram request originates at module import or from local validation.
    """
    from telethon import errors
    ids = tuple(dict.fromkeys(parse_document_id(value) for value in document_ids))
    found, rejected, unresolved = {}, {}, {}
    calls, retry_after = 0, 0

    async def batch_lookup(batch):
        nonlocal calls, retry_after
        if calls >= 64:
            unresolved.update((i, 'request_budget') for i in batch)
            return
        calls += 1
        try:
            documents = await client(GetCustomEmojiDocumentsRequest(document_id=list(batch)))
        except errors.DocumentInvalidError:
            if len(batch) == 1:
                rejected[batch[0]] = 'document_invalid'
            else:
                middle = len(batch) // 2
                await batch_lookup(batch[:middle])
                await batch_lookup(batch[middle:])
            return
        except errors.FloodWaitError as exc:
            retry_after = max(retry_after, exc.seconds)
            raise
        requested = set(batch)
        # Returned order is not a preference; retain the source publication order.
        for document in documents or ():
            doc_id = getattr(document, 'id', None)
            if doc_id not in requested:
                continue
            attribute = next((a for a in getattr(document, 'attributes', ()) or ()
                              if isinstance(a, DocumentAttributeCustomEmoji)), None)
            if attribute is None:
                rejected[doc_id] = 'missing_custom_emoji_attribute'
                continue
            alt = attribute.alt
            if not isinstance(alt, str) or not EMOJI_PATTERN.fullmatch(alt):
                rejected[doc_id] = 'invalid_alt'
                continue
            mime = getattr(document, 'mime_type', '')
            if mime not in ('application/x-tgsticker', 'video/webm', 'image/webp'):
                rejected[doc_id] = 'unsupported_media_type'
                continue
            found[doc_id] = CustomEmojiDocument(doc_id, alt, bool(attribute.free),
                mime in ('application/x-tgsticker', 'video/webm'), mime)
            rejected.pop(doc_id, None)
        for doc_id in batch:
            if doc_id not in found and doc_id not in rejected:
                rejected[doc_id] = 'missing_or_deleted'

    async def fetch():
        for start in range(0, len(ids), 100):
            await batch_lookup(ids[start:start + 100])
    try:
        await asyncio.wait_for(fetch(), timeout=timeout)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        reason = 'timeout' if isinstance(exc, asyncio.TimeoutError) else type(exc).__name__
        for doc_id in ids:
            if doc_id not in found and doc_id not in rejected:
                unresolved[doc_id] = reason
    return EmojiResolution(tuple(found[i] for i in ids if i in found), rejected, unresolved, retry_after)


async def resolve_custom_emoji_documents(client, document_ids, *, timeout=5):
    """Compatibility tuple API; detailed warm-up uses EmojiResolution instead."""
    return (await resolve_custom_emoji_catalogue(client, document_ids, timeout=timeout)).documents
