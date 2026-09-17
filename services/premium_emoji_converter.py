"""Premium Emoji Converter — تبدیل ایموجی یونیکد پیام‌های سلف به Custom Emoji تلگرام.

فقط کلاینت کاربری (Self Account) بسته‌بندی می‌شود؛ کلاینت‌های Bot Token هرگز
تغییر نمی‌کنند (نگهبان account.bot در نصب + عدم نصب روی کلاینت‌های ربات).
متن پیام و تمام فرمت‌های موجود (Bold/Italic/Underline/Strike/Spoiler/URL/Code/
Pre/Custom Emoji) حفظ می‌شوند؛ تنها MessageEntityCustomEmoji با آفست صحیح
UTF-16 اضافه می‌شود. سیستم idempotent است: ایموجیِ زیر Custom Emoji موجود هرگز
دوباره تبدیل نمی‌شود.

⚖️ Strict Mapping (پیش‌فرض: config.PREMIUM_EMOJI_STRICT_MODE=True):
هر ایموجی فقط به Custom Emoji با alt دقیقاً برابر تبدیل می‌شود؛ یعنی فقط اگر
نگاشت مرکزی برای آن ایموجی شناسه تأییدشده داشته باشد. نگاشت با لیست خالی
یعنی «بررسی‌شده اما غیرفعال» و ایموجی دست‌نخورده می‌ماند. fallback عمومی
(شناسه ثابت برای همه ایموجی‌ها) و سیستم Prefix Emoji کاملاً حذف شده‌اند؛
هیچ پیامی هرگز به‌صورت خودکار ایموجی اضافه نمی‌گیرد. نتیجه: 😂 هرگز به
Premium غیر-😂 تبدیل نمی‌شود و پیام همیشه بدون کاراکتر اضافه ارسال می‌شود.

🚰 Unified Pipeline (v0.09.13 DEBUG_FINAL):
    Input → Pre Processor (_parse_message_text) → Custom Emoji Converter
          → Telegram Entity Builder (UTF-16 offsets) → Sender
تنها این wrapper های پیش از ارسال روی کلاینت سلف نصب می‌شوند؛ send-then-edit
فقط fallback است (برای پیام‌های رسیده از دستگاه‌های دیگر که فیزیکی از این
مسیر عبور نمی‌کنند).

Design constraints (same philosophy as the previous strict release):
- No network request on the send path; mapping is a static verified file.
- No outgoing event handler in the main path; only send/edit wrappers.
- forward_messages is never wrapped; forwards stay byte-identical.
- On Telegram emoji rejection the original content is retried exactly once;
  DocumentInvalid on media/albums re-raises to avoid replaying committed chunks.
"""
from __future__ import annotations

import copy
import inspect
import json
import os
import random
import re
import time
import traceback
from contextvars import ContextVar
from functools import wraps

import config
from telethon import errors, events, functions, utils
from telethon.tl import types
from telethon.tl.types import Message, MessageEntityCustomEmoji

from services import custom_emoji_service as custom
from services import telegram_logger as tlog
from premium_emoji_mapping import (
    PREMIUM_EMOJI_MAP,
    PREMIUM_EMOJI_MODES,
    MAX_IDS_PER_EMOJI,
)

CONVERTER_MODULE = 'services/premium_emoji_converter.py'

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


def _safe_chat_id(entity):
    """شناسه عددی چت برای لاگ؛ هرگز اجازه خطا به مسیر ارسال نمی‌دهد."""
    try:
        if isinstance(entity, Message):
            return getattr(entity, 'chat_id', None)
        return utils.get_peer_id(entity)
    except Exception:  # noqa: BLE001 - لاگ نباید مسیر ارسال را بشکند
        return getattr(entity, 'chat_id', None) or getattr(entity, 'id', None)


def _unique_emojis(text):
    seen = []
    for match in custom.EMOJI_PATTERN.finditer(text or ''):
        token = match.group()
        if token not in seen:
            seen.append(token)
    return seen


def _new_custom_entities(result, existing):
    """فقط entity های تازه‌ساخته‌شده (برای گزارش دقیق Document IDs)."""
    old = {(e.offset, e.length, getattr(e, 'document_id', None))
           for e in existing or () if isinstance(e, MessageEntityCustomEmoji)}
    return [e for e in result or () if isinstance(e, MessageEntityCustomEmoji)
            and (e.offset, e.length, getattr(e, 'document_id', None)) not in old]


def _conversion_record(text, added):
    """خلاصه تبدیل برای گزارش‌های پس از ارسال (در meta ذخیره می‌شود)."""
    return {
        'text': text,
        'emojis': _unique_emojis(text)[:10],
        'document_ids': [getattr(e, 'document_id', None) for e in added],
        'entity_count': len(added),
    }


def _emit_premium_debug_block(meta, record):
    """بلوک [PREMIUM DEBUG] در لحظه ساخته‌شدن entity (پرچم PREMIUM_EMOJI_DEBUG)."""
    if not getattr(config, 'PREMIUM_EMOJI_DEBUG', False):
        return
    meta = meta or {}
    tlog.send_premium_debug_block(tlog.format_premium_debug(
        method=meta.get('method', 'unknown'), chat_id=meta.get('chat_id'),
        original_text=(record['text'] or '')[:300], detected=record['emojis'],
        document_ids=record['document_ids'], entity_count=record['entity_count'],
        chat_type=meta.get('chat_type', 'Unknown')))


def _emit_custom_emoji_block(meta, *, entity_status, send_status):
    """بلوک [CustomEmoji] — Chat/Emoji/ID/Entity/Send (پرچم CUSTOM_EMOJI_DEBUG)."""
    if not getattr(config, 'CUSTOM_EMOJI_DEBUG', False):
        return
    meta = meta or {}
    record = meta.get('conversion') or {}
    if not record.get('document_ids'):
        return
    tlog.send_custom_emoji_debug(tlog.format_custom_emoji_debug(
        chat_type=meta.get('chat_type', 'Unknown'), emoji=record.get('emojis'),
        document_id=record['document_ids'], entity_status=entity_status,
        send_status=send_status, method=meta.get('method', 'send')))


def _emit_success_report(engine, meta):
    """گزارش 🎨 تبدیل موفق — فقط بعد از ارسال موفق پیام با entity جدید."""
    meta = meta or {}
    record = meta.get('conversion') or {}
    document_ids = record.get('document_ids') or []
    tlog.send_premium_event(
        '🎨 Premium Emoji Converted',
        {
            'User': engine.owner_id,
            'Chat': meta.get('chat_id'),
            'Chat Type': meta.get('chat_type', 'Unknown'),
            'Method': meta.get('method', 'unknown'),
            'Emoji': ' '.join(record.get('emojis') or []),
            'Document ID': document_ids[0] if len(document_ids) == 1 else document_ids,
            'Status': 'SUCCESS',
        },
        level='INFO', kind='converted')


def _mapping_key(token, mapping=None):
    """Return the mapping entry for a matched emoji token, VS16-tolerant."""
    source = mapping if mapping is not None else PREMIUM_EMOJI_MAP
    if token in source:
        return token
    stripped = token.replace('\ufe0f', '')
    if stripped in source:
        return stripped
    decorated = stripped + '\ufe0f'
    if decorated in source:
        return decorated
    return None


def _sanitize_pool(ids, strict=True):
    """Validate/dedupe configured IDs.

    ورودی نامعتبر یا خالی همیشه → استخر خالی؛ یعنی بدون تبدیل. هیچ شناسه
    ثابتی جایگزین نمی‌شود (fallback عمومی در این نسخه کاملاً حذف شده است).
    پارامتر ``strict`` فقط برای سازگاری با فراخوان‌های قدیمی نگه داشته شده
    و دیگر هیچ اثری روی رفتار ندارد.
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
    return tuple(pool)


def _map_file_path(path=None):
    """مسیر مطلق فایل نگاشت مرکزی؛ خالی/نامعتبر → None (بدون crash)."""
    path = path if path is not None else getattr(
        config, 'PREMIUM_EMOJI_MAP_FILE', 'emoji_map.json')
    if path is None:
        return None
    try:
        path = os.fspath(path)
    except TypeError:
        return None
    if not path or not isinstance(path, str):
        return None
    if not os.path.isabs(path):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, path)
    return path


def load_emoji_map(path=None):
    """خواندن فایل مرکزی emoji_map.json ({emoji: [document_id, ...]}).

    قرارداد امنیتی spec مالک: هر خطا (فایل نبود، JSON خراب، شناسه نامعتبر)
    فقط یعنی «نگاشت فایل نادیده گرفته شود»؛ None برمی‌گردد تا نگاشت داخلی
    تأییدشده استفاده شود. هیچ‌وقت exception بالا نمی‌دهد.
    """
    location = _map_file_path(path)
    if not location:
        return None
    try:
        with open(location, 'r', encoding='utf-8') as handle:
            raw = json.load(handle)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(raw, dict) or not raw:
        return None
    # هم فرمت ساده ({ایموجی: شناسه}) و هم فرمت ابزار رسمی export
    # ({"_comment": ..., "_source": ..., "map": {...}}) پذیرفته می‌شود.
    if isinstance(raw.get('map'), dict):
        raw = raw['map']
    result = {}
    for emoji, ids in raw.items():
        if not isinstance(emoji, str) or not emoji:
            continue
        if isinstance(ids, (str, int)):
            ids = [ids]
        if not isinstance(ids, (list, tuple)):
            continue
        cleaned = []
        for value in ids:
            try:
                cleaned.append(custom.parse_document_id(value))
            except custom.EmojiError:
                continue
            if len(cleaned) == MAX_IDS_PER_EMOJI:
                break
        result[emoji] = cleaned
    return result or None


def _chat_kind(peer):
    """نوع چت فقط از نوع آبجکت/شناسه علامت‌گذاری‌شده؛ بدون هیچ درخواست شبکه.

    خروجی یکی از: Saved / Private / Group / Channel / Unknown.
    قرارداد شناسه‌های علامت‌گذاری Telethon: کاربر مثبت، گروه‌های پایه منفیِ
    کوچک، کانال/سوپرگروه با پیشوند -100.
    """
    try:
        if peer is None:
            return 'Unknown'
        if isinstance(peer, types.InputPeerSelf):
            return 'Saved'
        if isinstance(peer, (types.InputPeerUser, types.InputUser, types.User)):
            return 'Private'
        if isinstance(peer, types.InputPeerChat):
            return 'Group'
        if isinstance(peer, (types.InputPeerChannel, types.InputChannel,
                             types.Channel)):
            if isinstance(peer, types.Channel) and getattr(peer, 'megagroup', False):
                return 'Group'
            return 'Channel'
        if isinstance(peer, (types.Chat, types.ChatForbidden)):
            return 'Group'
        if isinstance(peer, (types.PeerUser,)):
            return 'Private'
        if isinstance(peer, (types.PeerChat,)):
            return 'Group'
        if isinstance(peer, (types.PeerChannel,)):
            return 'Channel'
        marked = utils.get_peer_id(peer)
        if isinstance(marked, int):
            if marked > 0:
                return 'Private'
            if marked > -1000000000000:
                return 'Group'
            return 'Channel'
    except Exception:  # noqa: BLE001 - نوع چت هرگز نباید مسیر ارسال را بشکند
        pass
    return 'Unknown'



class PremiumEmojiConverter:
    """Stateless-per-message converter; per-emoji picker holds only an index."""

    def __init__(self, *, premium=False, is_enabled=None, mode=None, mapping=None,
                 strict=None, map_path=None):
        self.premium = bool(premium)
        self.is_enabled = is_enabled  # optional callable -> None/True/False
        # فایل مرکزی emoji_map.json فقط وقتی فراخوان نگاشتی نداده است؛
        # هر خطا در خواندن فایل یعنی نگاشت داخلی تأییدشده استفاده می‌شود.
        file_map = None
        if mapping is None:
            file_map = load_emoji_map(map_path)
        self.map_source = 'emoji_map.json' if file_map is not None else 'builtin'
        self.mapping = dict(file_map if file_map is not None else
                            (mapping if mapping is not None else PREMIUM_EMOJI_MAP))
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
        self.owner_id = None  # برای گزارش‌های User-oriented (پر می‌شود هنگام install)

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

        None یعنی «هیچ تبدیلی انجام نشود» (متن اصلی حفظ می‌شود). استخر خالی
        هرگز به شناسه ثابت fallback ختم نمی‌شود (fallback حذف شده است)."""
        entry = _mapping_key(emoji, self.mapping)
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
    async def prepare(self, client, text, formatting, parse_mode, meta=None):
        """Parse once through Telethon, convert, and report whether anything changed.

        Markdown/HTML are parsed by Telethon itself when no explicit entities
        are supplied, so every existing formatting entity survives with the
        exact offsets Telegram produced.

        ``meta`` فقط برای گزارش‌هاست: method/chat_id/chat_type؛ اگر تبدیل
        اتفاق بیفتد خلاصه آن در ``meta['conversion']`` می‌ماند تا بلوک
        [CustomEmoji] و گزارش 🎨 بعد از ارسال با وضعیت واقعی Send صادر شوند.
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
        changed = new_count > old_count
        if changed:
            added = _new_custom_entities(result, existing)
            record = _conversion_record(parsed, added)
            if meta is not None:
                meta['conversion'] = record
            try:
                _emit_premium_debug_block(meta, record)
            except Exception:  # noqa: BLE001 - گزارش هرگز تبدیل را نمی‌شکند
                pass
        return updated, result, changed


def install_premium_emoji_converter(client, *, account, is_enabled=None):
    """Install once, only on a verified non-bot (user/self) account.

    Unified Pipeline (مسیر اصلی): send_message/send_file(caption)/edit_message/
    _send_album are wrapped PRE-SEND so replies, AI answers, auto replies,
    translation, crypto, tabchi, command and scheduler outputs — which all
    delegate to these Telethon methods — leave the client with real
    MessageEntityCustomEmoji entities from the first byte on the wire.
    forward_messages is deliberately NOT wrapped.
    """
    if getattr(account, 'bot', None) is not False:
        return None
    installed = getattr(client, '_premium_emoji_converter', None)
    if isinstance(installed, PremiumEmojiConverter):
        installed.premium = bool(getattr(account, 'premium', False))
        installed.owner_id = getattr(account, 'id', None)
        return installed
    engine = PremiumEmojiConverter(
        premium=getattr(account, 'premium', False), is_enabled=is_enabled)
    engine.owner_id = getattr(account, 'id', None)

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
            peer = bound.arguments.get('entity')
            if isinstance(peer, Message):
                # edit_message(message_object, new_text): نوع چت از خود پیام
                peer = getattr(peer, 'peer_id', None) or getattr(peer, 'input_chat', None)
            meta = {'method': method,
                    'chat_id': _safe_chat_id(peer),
                    'chat_type': _chat_kind(peer),
                    'conversion': None}

            async def prepare_value(text, formatting):
                return await engine.prepare(client, text, formatting, parse_mode,
                                            meta=meta)

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
            except Exception as exc:
                changed = False  # never break the send because of conversion
                # RC-C قدیمی: خطاهای تبدیل بی‌صدا بودند؛ اکنون دقیقاً گزارش می‌شوند.
                try:
                    frame = traceback.extract_tb(exc.__traceback__)[-1]
                    line_number = frame.lineno
                except Exception:  # noqa: BLE001
                    line_number = None
                tlog.send_error(
                    '❌ Premium Emoji Error',
                    {
                        'File': CONVERTER_MODULE,
                        'Line': line_number,
                        'Method': method,
                        'Error': f'{type(exc).__name__}: {exc}',
                    },
                    where=CONVERTER_MODULE)
                tlog.send_premium_event(
                    '⚠️ Premium Emoji Fallback',
                    {
                        'Reason': f'conversion failed ({type(exc).__name__})',
                        'Action': 'Original message sent',
                        'Method': method,
                        'Chat': meta.get('chat_id'),
                    },
                    level='WARNING', kind='fallback')
            if not changed:
                return await original(*args, **kwargs)
            token = engine.bypass.set(True)
            try:
                try:
                    result = await original(*bound.args, **bound.kwargs)
                except REJECTED_ERRORS as exc:
                    _emit_custom_emoji_block(meta, entity_status='CREATED',
                                             send_status=f'FAILED ({type(exc).__name__})')
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
                    tlog.send_premium_event(
                        '⚠️ Premium Emoji Fallback',
                        {
                            'Reason': f'Telegram rejected entity ({type(exc).__name__})',
                            'Action': 'Original message sent',
                            'Cooldown': f'{REJECTION_COOLDOWN_SECONDS}s',
                            'Method': method,
                            'Chat': meta.get('chat_id'),
                            'Chat Type': meta.get('chat_type', 'Unknown'),
                        },
                        level='WARNING', kind='fallback')
                    return await original(*args, **kwargs)
                except errors.FloodWaitError as exc:
                    _emit_custom_emoji_block(meta, entity_status='CREATED',
                                             send_status=f'FAILED (FloodWait {exc.seconds}s)')
                    tlog.send_error(
                        '❌ Telegram FloodWait',
                        {
                            'Method': method,
                            'Wait': f'{exc.seconds}s',
                            'Chat': meta.get('chat_id'),
                        },
                        where=CONVERTER_MODULE)
                    raise
                except Exception as exc:
                    # خطاهای Send/Edit/RPC: ثبت و propagate (رفتار قبل حفظ می‌شود)
                    _emit_custom_emoji_block(meta, entity_status='CREATED',
                                             send_status=f'FAILED ({type(exc).__name__})')
                    tlog.send_error(
                        '❌ Telegram Send Error',
                        {
                            'Method': method,
                            'Error': f'{type(exc).__name__}: {exc}'[:400],
                            'Chat': meta.get('chat_id'),
                            'Chat Type': meta.get('chat_type', 'Unknown'),
                        },
                        where=CONVERTER_MODULE)
                    raise
                # ارسال موفق: بلوک [CustomEmoji] با Send: SUCCESS + گزارش 🎨
                _emit_custom_emoji_block(meta, entity_status='CREATED',
                                         send_status='SUCCESS')
                _emit_success_report(engine, meta)
                return result
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


def install_premium_emoji_outgoing_injector(client, engine):
    """Fallback پس از ارسال (Post-Send Fix) — فقط روی کلاینت Self نصب می‌شود.

    ⚠️ این مسیر فقط FALLBACK است؛ مسیر اصلی، تبدیل «قبل از ارسال» توسط
    install_premium_emoji_converter است. این هندلر فقط برای پیام‌هایی است که
    از دستگاه دیگری (گوشی/اپ رسمی) ارسال شده‌اند و فیزیکی از wrapper های
    کلاینت پایتون عبور نکرده‌اند: پیام خروجی رسیده بررسی می‌شود و اگر ایموجی
    قابل‌نگاشتِ بدون entity دارد، همان متن با MessageEntityCustomEmoji دقیق
    edit می‌کند (متن عوض نمی‌شود؛ glyph عوض نمی‌شود؛ فقط entity اضافه می‌شود).

    هرگز اجرا نمی‌شود روی: forward ها، پیام‌های via_bot (پنل اینلاین = محتوای
    ربات)، سرویس/اکشن‌ها، پیام‌هایی که قبلاً Custom Emoji دارند، وقتی کانورتر
    خاموش یا در cooldown است. کلاینت Bot هرگز این هندلر را نمی‌گیرد.
    هر خطا (رد entity، عدم دسترسی edit، مشکل پیگیری Message ID و ...) با
    دلیل دقیق و نوع چت به ربات گزارش Admin ارسال می‌شود — هیچ شکست بی‌صدایی
    وجود ندارد.
    """
    if getattr(client, '_premium_emoji_outgoing_injector', None) is not None:
        return client._premium_emoji_outgoing_injector
    if getattr(client, '_premium_emoji_converter', None) is not engine:
        # گارد بات/کلاینت بدون کانورتر: تزریق فقط روی کلاینت Self مجهز مجاز است.
        return None

    @client.on(events.NewMessage(outgoing=True))
    async def _outgoing_premium_fix(event):
        message = getattr(event, 'message', None)
        try:
            if not getattr(config, 'PREMIUM_EMOJI_OUTGOING_FIX', True):
                return
            if message is None or getattr(message, 'action', None) is not None:
                return
            if getattr(message, 'fwd_from', None) is not None:
                return  # forward_messages: طبق قانون هرگز دست‌نخورده
            if getattr(message, 'via_bot_id', None):
                return  # پیام‌های پنل اینلاین: محتوای ربات، مستقل از کانورتر
            text = getattr(message, 'message', None)
            if not isinstance(text, str) or not text or len(text) > MAX_MESSAGE_CHARS:
                return
            existing = list(getattr(message, 'entities', None) or [])
            if any(isinstance(e, MessageEntityCustomEmoji) for e in existing):
                return  # همین حالا پرمیوم است؛ idempotency
            if not custom.EMOJI_PATTERN.search(text):
                return
            if not engine.effective_enabled() or time.monotonic() < engine.disabled_until:
                return
            updated, merged = engine.convert(text, existing)
            added = _new_custom_entities(merged, existing)
            if not added:
                return  # هیچ نگاشت دقیقی موجود نیست؛ همان ایموجی معمولی می‌ماند
            chat_id = getattr(event, 'chat_id', None)
            peer = getattr(message, 'input_chat', None)
            if isinstance(peer, types.InputPeerSelf):
                chat_type = 'Saved'
            else:
                chat_type = _chat_kind(peer if peer is not None
                                       else getattr(message, 'peer_id', None))
                if chat_type == 'Unknown' and chat_id is not None:
                    # اگر پیام به Saved خودم رسیده باشد، chat_id برابر id خودم است.
                    chat_type = 'Saved' if chat_id == getattr(
                        client, '_self_id', None) else _chat_kind(chat_id)
            meta = {'method': 'outgoing_fix', 'chat_id': chat_id,
                    'chat_type': chat_type,
                    'conversion': _conversion_record(updated, added)}
            if peer is None:
                try:
                    peer = await client.get_input_entity(chat_id)
                except (ValueError, TypeError) as exc:
                    # علت دقیق در گزارش می‌آید؛ پیام دست‌نخورده می‌ماند.
                    tlog.send_premium_event(
                        '⚠️ Premium Emoji Fallback',
                        {
                            'Reason': f'resolve chat failed ({type(exc).__name__})',
                            'Action': 'Original message kept (post-send fix skipped)',
                            'Method': 'outgoing_fix',
                            'Chat': chat_id,
                            'Chat Type': chat_type,
                        },
                        level='WARNING', kind='fallback')
                    return
            # فراخوانی مستقیم RPC: از wrap های send/edit عبور نمی‌کند تا
            # نه تزریق دوم رخ دهد و نه تبدیل دوگانه (idempotent).
            await client(functions.messages.EditMessageRequest(
                peer=peer, id=message.id, message=updated, entities=merged or None))
            _emit_premium_debug_block(meta, meta['conversion'])
            _emit_custom_emoji_block(meta, entity_status='CREATED',
                                     send_status='EDITED')
            _emit_success_report(engine, meta)
        except REJECTED_ERRORS as exc:
            # تلگرام entity را رد کرد: cooldown + گزارش fallback (دلیل همان لحظه)
            engine.disabled_until = time.monotonic() + REJECTION_COOLDOWN_SECONDS
            tlog.send_premium_event(
                '⚠️ Premium Emoji Fallback',
                {
                    'Reason': f'Telegram rejected entity ({type(exc).__name__})',
                    'Action': 'Original message kept (post-send fix skipped)',
                    'Cooldown': f'{REJECTION_COOLDOWN_SECONDS}s',
                    'Method': 'outgoing_fix',
                    'Chat': getattr(event, 'chat_id', None),
                },
                level='WARNING', kind='fallback')
        except (errors.MessageNotModifiedError, errors.MessageIdInvalidError,
                errors.MessageAuthorRequiredError):
            return  # رقابت با edit های دیگر؛ پیام سالم است
        except Exception as exc:  # noqa: BLE001 - هیچ‌وقت پیام‌رسانی را نشکند
            try:
                frame = traceback.extract_tb(exc.__traceback__)[-1]
                line_number = frame.lineno
            except Exception:  # noqa: BLE001
                line_number = None
            tlog.send_error(
                '❌ Premium Emoji Error',
                {
                    'File': CONVERTER_MODULE,
                    'Line': line_number,
                    'Method': 'outgoing_fix',
                    'Error': f'{type(exc).__name__}: {exc}',
                    'Chat': getattr(event, 'chat_id', None),
                },
                where=CONVERTER_MODULE)

    client._premium_emoji_outgoing_injector = _outgoing_premium_fix
    return _outgoing_premium_fix


def uninstall_premium_emoji_outgoing_injector(client):
    handler = getattr(client, '_premium_emoji_outgoing_injector', None)
    if handler is not None:
        try:
            client.remove_event_handler(handler)
        except Exception:  # noqa: BLE001
            pass
        del client._premium_emoji_outgoing_injector


def uninstall_premium_emoji_converter(client):
    engine = getattr(client, '_premium_emoji_converter', None)
    if isinstance(engine, PremiumEmojiConverter):
        for name, original in engine.originals.items():
            setattr(client, name, original)
        del client._premium_emoji_converter
