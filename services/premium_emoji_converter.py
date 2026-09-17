"""Premium Emoji Converter — تبدیل ایموجی یونیکد پیام‌های سلف به Custom Emoji تلگرام.

فقط کلاینت کاربری (Self Account) بسته‌بندی می‌شود؛ کلاینت‌های Bot Token هرگز
تغییر نمی‌کنند (نگهبان account.bot در نصب + عدم نصب روی کلاینت‌های ربات).
متن پیام و تمام فرمت‌های موجود (Bold/Italic/Underline/Strike/Spoiler/URL/Code/
Pre/Custom Emoji) حفظ می‌شوند؛ تنها MessageEntityCustomEmoji با آفست صحیح
UTF-16 اضافه می‌شود. سیستم idempotent است: ایموجیِ زیر Custom Emoji موجود هرگز
دوباره تبدیل نمی‌شود.

⚖️ Strict Mapping (پیش‌فرض: config.PREMIUM_EMOJI_STRICT_MODE=True):
هر ایموجی فقط به Custom Emoji با alt دقیقاً برابر تبدیل می‌شود؛ یعنی فقط اگر
PREMIUM_EMOJI_MAP برای آن ایموجی شناسه تأییدشده داشته باشد. نگاشت با
لیست خالی یعنی «بررسی‌شده اما غیرفعال» و ایموجی دست‌نخورده می‌ماند.
fallback عمومی (شناسه ثابت برای همه ایموجی‌ها) ممنوع است و FALLBACK_DOCUMENT_ID
فقط برای قابلیت مستقل Premium Prefix و رفتار قدیمی strict=False نگه داشته
شده است. نتیجه: 😂 هرگز به Premium غیر-😂 تبدیل نمی‌شود.

Design constraints (same philosophy as premium_emoji_prefix):
- No network request on the send path; mapping is a static verified file.
- No outgoing event handler; only public send/edit wrappers are wrapped.
- forward_messages is never wrapped; forwards stay byte-identical.
- On Telegram emoji rejection the original content is retried exactly once;
  DocumentInvalid on media/albums re-raises to avoid replaying committed chunks.
"""
from __future__ import annotations

import copy
import inspect
import random
import re
import time
from contextvars import ContextVar
from functools import wraps

import config
from telethon import errors, utils
from telethon.tl import types
from telethon.tl.types import Message, MessageEntityCustomEmoji

from services import custom_emoji_service as custom
from premium_emoji_mapping import (
    FALLBACK_DOCUMENT_ID,
    PREMIUM_EMOJI_MAP,
    PREMIUM_EMOJI_MODES,
    MAX_IDS_PER_EMOJI,
)

# Telegram rejects custom-emoji entities for these reasons only; everything
# else (timeouts, FloodWait, generic RPC) must surface unchanged.
REJECT_MESSAGES = {
    'PREMIUM_ACCOUNT_REQUIRED', 'EMOTICON_INVALID', 'CUSTOM_EMOJI_INVALID',
    'CUSTOM_EMOJI_DOCUMENT_INVALID', 'EMOTICON_DOCUMENT_INVALID',
    'EMOTICON_DOCUMENT_EMPTY',
}
REJECTED_ERRORS = tuple(getattr(errors, name) for name in (
    'PremiumAccountRequiredError', 'EmoticonInvalidError',
    'EntityBoundsInvalidError', 'EntitiesTooLongError',
) if hasattr(errors, name)) + (errors.DocumentInvalidError,)

# Code/pre/URL spans and any existing custom emoji must never be reconverted;
# ```code```/`inline`/link literals are protected the same way the injector does.
LITERAL_PATTERN = re.compile(r'```[\s\S]*?(?:```|$)|`[^`\n]*`|https?://[^\s<>]+', re.IGNORECASE)

MAX_MESSAGE_CHARS = 16384
MAX_CONVERSIONS_PER_MESSAGE = 50
REJECTION_COOLDOWN_SECONDS = 300
ENTITY_OVERLAP_TYPES = (MessageEntityCustomEmoji, types.MessageEntityCode,
                        types.MessageEntityPre, types.MessageEntityUrl,
                        types.MessageEntityTextUrl)


class _Picker:
    """Tiny per-emoji selector; no heavy cache, one int index of state."""

    __slots__ = ('ids', 'index')

    def __init__(self, ids):
        self.ids = tuple(ids)
        self.index = 0

    def next(self, mode, rng):
        # استخر خالی یعنی «هیچ شناسه تأییدشده‌ای نیست» → None؛ در حالت Strict
        # این یعنی ایموجی دست‌نخورده می‌ماند و هرگز به شناسه ثابت نمی‌افتد.
        if not self.ids:
            return None
        if mode == 'random' and len(self.ids) > 1:
            return self.ids[rng.randrange(len(self.ids))]
        value = self.ids[self.index]
        self.index = (self.index + 1) % len(self.ids)
        return value


def _mapping_key(token):
    """Return the mapping entry for a matched emoji token, VS16-tolerant."""
    if token in PREMIUM_EMOJI_MAP:
        return token
    stripped = token.replace('\ufe0f', '')
    if stripped in PREMIUM_EMOJI_MAP:
        return stripped
    decorated = stripped + '\ufe0f'
    if decorated in PREMIUM_EMOJI_MAP:
        return decorated
    return None


def _sanitize_pool(ids, strict=True):
    """Validate/dedupe configured IDs.

    strict=True (پیش‌فرض): ورودی نامعتبر یا خالی → استخر خالی؛ یعنی بدون
    تبدیل. هیچ شناسه ثابتی جایگزین نمی‌شود (fallback عمومی ممنوع).
    strict=False: رفتار قدیمی نسخه قبل — استخر خالی/نامعتبر به شناسه
    تست‌شده مالک ارجاع می‌شود (فقط برای بازگشت موقت؛ پیش‌فرض نیست).
    """
    pool = []
    for value in ids if isinstance(ids, (list, tuple)) else []:
        try:
            value = custom.parse_document_id(value)
        except custom.EmojiError:
            continue
        if value not in pool:
            pool.append(value)
        if len(pool) == MAX_IDS_PER_EMOJI:
            break
    if pool:
        return tuple(pool)
    return () if strict else (FALLBACK_DOCUMENT_ID,)


class PremiumEmojiConverter:
    """Stateless-per-message converter; per-emoji picker holds only an index."""

    def __init__(self, *, premium=False, is_enabled=None, mode=None, mapping=None,
                 strict=None):
        self.premium = bool(premium)
        self.is_enabled = is_enabled  # optional callable -> None/True/False
        self.mapping = dict(mapping if mapping is not None else PREMIUM_EMOJI_MAP)
        mode_value = str(mode or getattr(config, 'PREMIUM_EMOJI_MODE', 'round_robin') or '')
        self.mode = mode_value if mode_value in PREMIUM_EMOJI_MODES else 'round_robin'
        if strict is None:
            strict = getattr(config, 'PREMIUM_EMOJI_STRICT_MODE', True)
        self.strict = bool(strict)
        self.rng = random.Random()  # isolated instance: no global seeding side effects
        self.selectors = {
            emoji: _Picker(_sanitize_pool(ids, self.strict))
            for emoji, ids in self.mapping.items()
        }
        self.disabled_until = 0.0
        self.bypass = ContextVar('premium_converter_bypass', default=False)
        self.originals = {}

    # ------------------------------------------------------------- enable
    def effective_enabled(self):
        """Config default + optional per-account panel toggle (None -> default)."""
        if time.monotonic() < self.disabled_until:
            return False
        if self.is_enabled is not None:
            try:
                user_choice = self.is_enabled()
            except Exception:
                user_choice = None
            if user_choice is not None:
                return bool(user_choice)
        return bool(getattr(config, 'PREMIUM_EMOJI_CONVERTER_ENABLED', False))

    # ----------------------------------------------------------- pure core
    def pick_document_id(self, emoji):
        """شناسه فقط از نگاشت دقیق همان ایموجی؛ بدون نگاشت دقیق → None.

        None یعنی «هیچ تبدیلی انجام نشود» (متن اصلی حفظ می‌شود). در حالت
        Strict هیچ مسیری به شناسه ثابت fallback ختم نمی‌شود."""
        entry = _mapping_key(emoji)
        if entry is None:
            return None
        return self.selectors[entry].next(self.mode, self.rng)

    def convert(self, text, entities=None):
        """Return (text, entities). Text never changes; only entities are added.

        Idempotent: spans already carrying MessageEntityCustomEmoji are skipped,
        so re-converting an already converted message is a no-op. Inputs are
        never mutated.
        """
        original = list(entities or ())
        if (not isinstance(text, str) or not text or len(text) > MAX_MESSAGE_CHARS
                or not self.selectors):
            return text, original
        # UTF-16 offset table (astral code points count as two units).
        offsets = [0]
        for char in text:
            offsets.append(offsets[-1] + (2 if ord(char) > 0xffff else 1))
        if custom.utf16_length(text) != offsets[-1]:
            return text, original  # lone surrogates: never send malformed text
        boundaries = set(offsets)
        for entity in original:
            if (getattr(entity, 'offset', None) not in boundaries
                    or getattr(entity, 'length', 0) <= 0
                    or entity.offset + entity.length not in boundaries):
                return text, original
        literals = [m.span() for m in LITERAL_PATTERN.finditer(text)]
        added = []
        for match in custom.EMOJI_PATTERN.finditer(text):
            if len(added) >= MAX_CONVERSIONS_PER_MESSAGE:
                break
            start, end = match.span()
            if not custom.whole_emoji(text, start, end):
                continue
            offset, stop = offsets[start], offsets[end]
            if any(a < end and b > start for a, b in literals):
                continue
            document_id = self.pick_document_id(match.group())
            if document_id is None:
                continue
            overlapping = [e for e in original if e.offset < stop and e.offset + e.length > offset]
            if any(isinstance(e, ENTITY_OVERLAP_TYPES) for e in overlapping):
                continue  # already custom / code / link: idempotency + formatting
            if any(not (e.offset <= offset and e.offset + e.length >= stop)
                   for e in overlapping):
                continue  # entity covers only part of the emoji: stay safe
            entity = MessageEntityCustomEmoji(offset=offset, length=stop - offset,
                                              document_id=document_id)
            added.append(entity)
        if not added:
            return text, original
        # Original entities are cloned untouched (offsets stay valid: text is
        # unchanged); new custom-emoji entities merge in visual order.
        adjusted = [copy.copy(e) for e in original]
        merged = sorted([*adjusted, *added], key=lambda e: (e.offset, -e.length))
        return text, merged

    # -------------------------------------------------------- send wrapper
    async def prepare(self, client, text, formatting, parse_mode):
        """Parse once through Telethon, convert, and report whether anything changed.

        Markdown/HTML are parsed by Telethon itself when no explicit entities
        are supplied, so every existing formatting entity survives with the
        exact offsets Telegram produced.
        """
        if not isinstance(text, str) or not text:
            return text, formatting, False
        if not custom.EMOJI_PATTERN.search(text):
            return text, formatting, False
        if formatting is None:
            parsed, existing = await client._parse_message_text(text, parse_mode)
        else:
            parsed, existing = text, formatting
        updated, result = self.convert(parsed, existing)
        old_count = sum(isinstance(e, MessageEntityCustomEmoji) for e in existing or [])
        new_count = sum(isinstance(e, MessageEntityCustomEmoji) for e in result or [])
        return updated, result, new_count > old_count


def install_premium_emoji_converter(client, *, account, is_enabled=None):
    """Install once, only on a verified non-bot (user/self) account.

    send_message/send_file(caption)/edit_message/_send_album are wrapped so
    replies, AI answers, auto replies, translation and crypto outputs — which
    all delegate to these Telethon methods — are covered automatically.
    forward_messages is deliberately NOT wrapped.
    """
    if getattr(account, 'bot', None) is not False:
        return None
    installed = getattr(client, '_premium_emoji_converter', None)
    if isinstance(installed, PremiumEmojiConverter):
        installed.premium = bool(getattr(account, 'premium', False))
        return installed
    engine = PremiumEmojiConverter(
        premium=getattr(account, 'premium', False), is_enabled=is_enabled)

    def wrap(original, field, method):
        signature = inspect.signature(original)

        @wraps(original)
        async def wrapped(*args, **kwargs):
            if (engine.bypass.get() or not engine.effective_enabled()):
                return await original(*args, **kwargs)
            bound = signature.bind(*args, **kwargs)
            target = field
            if method == 'edit_message':
                peer = bound.arguments.get('entity')
                if isinstance(peer, (types.InputBotInlineMessageID,
                                     types.InputBotInlineMessageID64)):
                    return await original(*args, **kwargs)
                if isinstance(peer, Message):
                    target = 'message'  # edit_message(message_object, new_text)
            if method in ('send_message', 'send_file') and utils.is_list_like(
                    bound.arguments.get('file')):
                return await original(*args, **kwargs)  # albums: _send_album handles

            value = bound.arguments.get(target, '')
            supplied = bound.arguments.get('formatting_entities')
            parse_mode = bound.arguments.get('parse_mode', ())

            async def prepare_value(text, formatting):
                return await engine.prepare(client, text, formatting, parse_mode)

            changed = False
            try:
                if isinstance(value, Message):
                    text, entities, changed = await prepare_value(
                        value.message, value.entities or [])
                    if changed:
                        value = copy.copy(value)
                        value.message, value.entities = text, entities
                        bound.arguments[target] = value
                elif method == '_send_album' and isinstance(value, (list, tuple)):
                    supplied = supplied or []
                    if supplied and not all(isinstance(x, (list, tuple)) for x in supplied):
                        supplied = [supplied]
                    prepared = [
                        await prepare_value(caption, supplied[i] if i < len(supplied) else None)
                        for i, caption in enumerate(value)
                    ]
                    changed = any(item[2] for item in prepared)
                    if changed:
                        texts, all_entities = [], []
                        for i, (caption, fmt, item_changed) in enumerate(prepared):
                            if not item_changed and fmt is None:
                                caption, fmt = await client._parse_message_text(
                                    caption or '', parse_mode)
                            texts.append(caption)
                            all_entities.append(fmt or [])
                        bound.arguments[target] = texts
                        bound.arguments['formatting_entities'] = all_entities
                        bound.arguments['parse_mode'] = None
                else:
                    text, entities, changed = await prepare_value(value, supplied)
                    if changed:
                        bound.arguments[target] = text
                        bound.arguments['formatting_entities'] = entities
                        bound.arguments['parse_mode'] = None
            except Exception:
                changed = False  # never break the send because of conversion
            if not changed:
                return await original(*args, **kwargs)
            token = engine.bypass.set(True)
            try:
                try:
                    return await original(*bound.args, **bound.kwargs)
                except REJECTED_ERRORS as exc:
                    media = bound.arguments.get('file', kwargs.get('file'))
                    message_value = bound.arguments.get('message')
                    has_media = (field == 'caption' or media is not None
                                 or (isinstance(message_value, Message)
                                     and message_value.media is not None))
                    if utils.is_list_like(media) or (has_media
                                              and isinstance(exc, errors.DocumentInvalidError)):
                        raise  # a committed album chunk must never be replayed
                    # Telegram refused the custom emoji: one clean retry of the
                    # message exactly as the caller produced it. No duplicates.
                    engine.disabled_until = time.monotonic() + REJECTION_COOLDOWN_SECONDS
                    return await original(*args, **kwargs)
            finally:
                engine.bypass.reset(token)
        return wrapped

    for method, field in (('send_message', 'message'), ('send_file', 'caption'),
                          ('edit_message', 'text'), ('_send_album', 'caption')):
        original = getattr(client, method, None)
        if callable(original) and field in inspect.signature(original).parameters:
            engine.originals[method] = original
            setattr(client, method, wrap(original, field, method))
    client._premium_emoji_converter = engine
    return engine


def uninstall_premium_emoji_converter(client):
    engine = getattr(client, '_premium_emoji_converter', None)
    if isinstance(engine, PremiumEmojiConverter):
        for name, original in engine.originals.items():
            setattr(client, name, original)
        del client._premium_emoji_converter
