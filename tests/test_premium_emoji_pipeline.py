"""Acceptance tests — Unified Pipeline + Prefix Removal (v0.09.13 DEBUG_FINAL).

سناریوهای الزامی spec مالک، همه آفلاین و بدون شبکه (قرارداد AGENTS.md):
    1) Saved Messages   2) Private Chat   3) Group
    4) Channel          5) File Caption   6) Reply Message

تضمین‌های اصلی:
- متن ارسالی دقیقاً برابر متن ورودی است؛ هیچ ایموجی ثابتی (✨/placeholder/
  fallback document id) به ابتدای پیام اضافه نمی‌شود.
- همان ایموجی‌های کاربر به MessageEntityCustomEmoji تبدیل می‌شوند.
- fallback «ارسال سپس ویرایش» فقط برای پیام‌های رسیده از دستگاه‌های دیگر است.
- کلاینت Bot هرگز wrap نمی‌شود.
"""
import asyncio
import copy
import inspect
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from telethon import TelegramClient, errors, functions, types
from telethon.sessions import MemorySession

import config
import premium_emoji_mapping as mapping_module
from services import premium_emoji_converter as mod
from services import telegram_logger as tlog

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)

SELF_PEER = types.InputPeerSelf()                      # Saved Messages
PRIVATE_PEER = types.InputPeerUser(555, 999)           # Private chat
GROUP_PEER = types.InputPeerChat(777)                  # Basic group
CHANNEL_PEER = types.InputPeerChannel(888, 42)         # Channel

# نگاشت فعلی — هر ایموجی فقط شناسه‌های alt-تأییدشدهٔ خودش را دارد.
LAUGH = mapping_module.PREMIUM_EMOJI_MAP['😂'][0]
FIRE = mapping_module.PREMIUM_EMOJI_MAP['🔥'][0]
HEART = mapping_module.PREMIUM_EMOJI_MAP['❤️'][0]

# شناسه ثابت قدیمی که دیگر نباید هیچ‌جا ظاهر شود.
BANNED_FALLBACK_ID = 5938388342281343001

MEDIA = types.InputMediaPhoto(types.InputPhoto(100, 200, b'reference'))
TEXT = 'سلام 😂🔥❤️'


def run(coro):
    return asyncio.run(coro)


class OfflineClient(TelegramClient):
    """Real Telethon methods; only the network boundary is simulated."""

    def __init__(self, *, bot=False):
        super().__init__(MemorySession(), 12345, 'offline-test-only')
        self._mb_entity_cache.extend([types.User(123, access_hash=456)], [])
        self.calls = []
        self.committed = []
        self.fail = None
        self.account = NS(bot=bot, premium=True, id=8359698350)

    async def _call(self, sender, request, ordered=False, flood_sleep_threshold=None):
        bytes(request)  # real TL serialization
        self.calls.append(copy.deepcopy(request))
        if self.fail:
            exc = self.fail(request, len(self.calls))
            if exc:
                raise exc
        if isinstance(request, functions.users.GetUsersRequest):
            # فقط برای resolve خودکار Telethon (مثلاً InputPeerSelf → Saved)
            return [types.User(getattr(item, 'user_id', 123) or 123,
                               access_hash=456) for item in request.id]
        if isinstance(request, functions.messages.SendMessageRequest):
            self.committed.append(request)
            return types.UpdateShortSentMessage(len(self.committed), 1, 1, NOW,
                                                out=True, entities=request.entities)
        if isinstance(request, functions.messages.SendMultiMediaRequest):
            bodies = request.multi_media
        elif isinstance(request, (functions.messages.SendMediaRequest,
                                  functions.messages.EditMessageRequest)):
            bodies = [request]
        else:
            raise AssertionError('Unexpected network request: ' + type(request).__name__)
        updates = []
        for body in bodies:
            self.committed.append(body)
            mid = (request.id if isinstance(request, functions.messages.EditMessageRequest)
                   else len(self.committed))
            message = types.Message(mid, types.PeerUser(123), date=NOW, out=True,
                                    message=body.message, entities=body.entities)
            if isinstance(request, functions.messages.EditMessageRequest):
                updates.append(types.UpdateEditMessage(message, 1, 1))
            else:
                updates.extend([types.UpdateMessageID(mid, body.random_id),
                                types.UpdateNewMessage(message, 1, 1)])
        return types.Updates(updates, [], [], NOW, 1)


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    """Converter ON, strict mapping, both debug flags visible."""
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_CONVERTER_ENABLED', True)
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_DEBUG', True)
    monkeypatch.setattr(config, 'CUSTOM_EMOJI_DEBUG', True)
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_LOG_LEVEL', 'DEBUG')
    monkeypatch.setattr(config, 'LOG_LEVEL', 'ERROR')
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_OUTGOING_FIX', True)
    monkeypatch.setattr(tlog, 'log_token', lambda: '123456:dummy-token')
    monkeypatch.setattr(config, 'ADMIN_LOG_IDS', [8359698350])
    tlog.reset_rate_state()
    yield
    tlog.reset_rate_state()


@pytest.fixture()
def deliveries(monkeypatch):
    """Record everything the logger would deliver to Telegram."""
    sent = []

    def _capture(text, chat_ids):
        sent.append({'text': text, 'chat_ids': list(chat_ids)})
        return True

    monkeypatch.setattr(tlog, '_enqueue', _capture)
    return sent


def make_client(*, bot=False):
    client = OfflineClient(bot=bot)
    engine = mod.install_premium_emoji_converter(
        client, account=client.account, is_enabled=lambda: True)
    return client, engine


def custom_entities_of(request):
    return [e for e in getattr(request, 'entities', None) or ()
            if isinstance(e, types.MessageEntityCustomEmoji)]


def last_send_request(client):
    return next(r for r in reversed(client.committed)
                if isinstance(r, (functions.messages.SendMessageRequest,
                                  functions.messages.SendMediaRequest)))


def last_edit_request(client):
    return next(r for r in reversed(client.committed)
                if isinstance(r, functions.messages.EditMessageRequest))


# ========================= سناریوهای ۱ تا ۴ — چهار نوع چت =========================
@pytest.mark.parametrize('peer,chat_type', [
    (SELF_PEER, 'Saved'),
    (PRIVATE_PEER, 'Private'),
    (GROUP_PEER, 'Group'),
    (CHANNEL_PEER, 'Channel'),
])
def test_all_chat_types_convert_without_prefix(deliveries, peer, chat_type):
    """مسیر اصلی (pre-send) در هر چهار نوع چت: همان ایموجی‌های کاربر تبدیل
    می‌شوند و متن پیام بدون هیچ کاراکتر اضافه‌ای ارسال می‌شود."""
    client, _ = make_client()
    sent = run(client.send_message(peer, TEXT, parse_mode=None))
    assert sent.message == TEXT  # هیچ prefix/placeholder اضافه نشده
    request = last_send_request(client)
    entities = custom_entities_of(request)
    assert {e.document_id for e in entities} == {LAUGH, FIRE, HEART}
    # آفست‌ها UTF-16: «سلام » = ۵ واحد → همه ایموجی‌ها ۲ واحدی‌اند.
    offsets = {(e.offset, e.length) for e in entities}
    assert (5, 2) in offsets
    blocks = [d['text'] for d in deliveries if d['text'].startswith('[CustomEmoji]')]
    assert blocks and f'Chat: {chat_type}' in blocks[-1]
    assert 'Send: SUCCESS' in blocks[-1]
    assert 'Entity: CREATED' in blocks[-1]
    # شناسه ممنوع هرگز ارسال نمی‌شود.
    assert BANNED_FALLBACK_ID not in {e.document_id for e in entities}


def test_utf16_offsets_match_real_positions():
    """آفست‌های entity دقیقاً روی موقعیت UTF-16 ایموجی‌ها هستند."""
    client, engine = make_client()
    _, entities = engine.convert(TEXT)
    utf16 = {}
    unit = 0
    for index, char in enumerate(TEXT):
        utf16[index] = unit
        unit += 2 if ord(char) > 0xffff else 1
    for entity in entities:
        start = next(i for i in utf16 if utf16[i] == entity.offset)
        assert TEXT[start] in '😂🔥❤️'
        assert entity.length == 2  # هر سه ایموجی BMP+VS16 = 2 واحد UTF-16


# ================================ سناریو ۵ — کپشن فایل ================================
def test_file_caption_converted(deliveries):
    client, _ = make_client()
    run(client.send_file(PRIVATE_PEER, MEDIA, caption='عکس جدید 🔥',
                         parse_mode=None))
    request = last_send_request(client)
    assert isinstance(request, functions.messages.SendMediaRequest)
    assert request.message == 'عکس جدید 🔥'
    assert {e.document_id for e in custom_entities_of(request)} == {FIRE}
    blocks = [d['text'] for d in deliveries if d['text'].startswith('[CustomEmoji]')]
    assert blocks and 'Chat: Private' in blocks[-1] and 'Send: SUCCESS' in blocks[-1]


def test_album_captions_converted_per_item():
    client, _ = make_client()
    run(client.send_file(PRIVATE_PEER, [MEDIA] * 3,
                         caption=['یکی 😂', '', 'دو 💎'], parse_mode=None))
    singles = [r for r in client.committed if isinstance(r, types.InputSingleMedia)]
    assert len(singles) == 3
    captions = {body.message: {e.document_id for e in custom_entities_of(body)}
                for body in singles}
    assert captions['یکی 😂'] == {LAUGH}
    assert captions['دو 💎'] == {mapping_module.PREMIUM_EMOJI_MAP['💎'][0]}


# ============================== سناریو ۶ — پیام ریپلای ==============================
def test_reply_message_converted():
    """event.reply / respond در Telethon به send_message(reply_to=...) می‌رود."""
    client, _ = make_client()
    base = run(client.send_message(PRIVATE_PEER, 'پایه', parse_mode=None))
    client.committed.clear()
    reply = run(client.send_message(PRIVATE_PEER, 'پاسخ 😍', reply_to=base.id,
                                    parse_mode=None))
    assert reply.message == 'پاسخ 😍'
    request = last_send_request(client)
    # Telethon 1.44: reply در SendMessageRequest به‌صورت InputReplyToMessage است.
    assert request.reply_to.reply_to_msg_id == base.id
    assert {e.document_id for e in custom_entities_of(request)} == {
        mapping_module.PREMIUM_EMOJI_MAP['😍'][0]}


# ============================ تضمین حذف کامل Prefix ============================
def test_no_auto_prefix_anywhere_in_sources():
    """در کد زمان اجرا هیچ اثری از سیستم Prefix/placeholder/fallback نیست."""
    banned_fragments = (
        'PREMIUM_EMOJI_PREFIX_ENABLED',
        "PLACEHOLDER = ",
        'FALLBACK_DOCUMENT_ID =',
    )
    for relative in ('services/premium_emoji_converter.py',
                     'premium_emoji_mapping.py', 'config.py', 'self.py'):
        source = (ROOT / relative).read_text(encoding='utf-8')
        for fragment in banned_fragments:
            assert fragment not in source, f'{relative} contains {fragment}'


def test_sent_text_never_mutated_for_any_chat_type():
    """متن ارسالی برای همه نوع چت‌ها دقیقاً برابر ورودی است."""
    client, _ = make_client()
    for peer in (SELF_PEER, PRIVATE_PEER, GROUP_PEER, CHANNEL_PEER):
        client.committed.clear()
        sent = run(client.send_message(peer, TEXT, parse_mode=None))
        assert sent.message == TEXT
        assert not client.committed or last_send_request(client).message == TEXT


def test_unmapped_emoji_stays_untouched_and_no_crash():
    """ایموجی بدون نگاشت دقیق: بدون تبدیل، بدون خطا، بدون ایموجی اضافه."""
    client, _ = make_client()
    sent = run(client.send_message(GROUP_PEER, 'برویم 🚀 🤙', parse_mode=None))
    assert sent.message == 'برویم 🚀 🤙'
    assert custom_entities_of(last_send_request(client)) == []


# ============================== فایل مرکزی نگاشت ==============================
def test_central_emoji_map_file_loaded_by_default(monkeypatch, tmp_path):
    """کانورتر بدون mapping صریح، فایل مرکزی emoji_map.json را می‌خواند."""
    source = json.loads((ROOT / 'emoji_map.json').read_text(encoding='utf-8'))
    engine = mod.PremiumEmojiConverter()
    assert engine.map_source == 'emoji_map.json'
    for emoji, ids in source['map'].items():
        expected = tuple(int(value) for value in ids)
        assert engine.mapping[emoji] == list(expected)


def test_central_map_broken_file_falls_back_to_builtin(tmp_path):
    broken = tmp_path / 'broken.json'
    broken.write_text('{not-json', encoding='utf-8')
    assert mod.load_emoji_map(broken) is None
    engine = mod.PremiumEmojiConverter(map_path=broken)
    assert engine.map_source == 'builtin'
    assert engine.mapping == mapping_module.PREMIUM_EMOJI_MAP


def test_central_map_flat_format_and_invalid_ids_dropped(tmp_path):
    flat = tmp_path / 'flat.json'
    flat.write_text(json.dumps({'🔥': '6123027087960843224', '😅': ['bad', -1]},
                               ensure_ascii=False), encoding='utf-8')
    loaded = mod.load_emoji_map(flat)
    assert loaded['🔥'] == [6123027087960843224]
    assert loaded['😅'] == []  # شناسه نامعتبر → استخر خالی (بدون crash)


def test_missing_emoji_never_crashes_pipeline():
    client, _ = make_client()
    sent = run(client.send_message(SELF_PEER, 'متن عادی بدون ایموجی', parse_mode=None))
    assert sent.message == 'متن عادی بدون ایموجی'


# ============================== fallback و debug ==============================
def test_outgoing_injector_never_edits_phone_message(deliveries):
    """پیام رسیده از گوشی: هندلر outgoing هیچ edit ای انجام نمی‌دهد.

    طبق spec مالک، مسیر تنها با مدیر ارسال دوباره (حذف + ارسال جدید)
    است؛ بدون مدیر نصب‌شده، پیام دست‌نخورده می‌ماند.
    """
    client, engine = make_client()
    handler = mod.install_premium_emoji_outgoing_injector(client, engine)
    phone_message = types.Message(31, types.PeerUser(555), date=NOW, out=True,
                                  message=TEXT, entities=None)
    phone_message._input_chat = PRIVATE_PEER
    run(handler(NS(message=phone_message, chat_id=555)))
    # هیچ EditMessageRequest و هیچ درخواست شبکه‌ای صادر نشده است
    assert not [r for r in client.committed
                if isinstance(r, functions.messages.EditMessageRequest)]
    assert not [r for r in client.committed
                if isinstance(r, (functions.messages.SendMessageRequest,
                                  functions.messages.SendMediaRequest))]
    blocks = [d['text'] for d in deliveries
              if 'outgoing_fix' in d['text'] or 'EDITED' in d['text']]
    assert blocks == []  # هیچ گزارش edit هم صادر نمی‌شود


def test_send_failure_reports_failed_status(deliveries):
    client, _ = make_client()

    def fail_always(request, call_index):
        if isinstance(request, functions.messages.SendMessageRequest):
            return errors.TimeoutError(request=request)
        return None

    client.fail = fail_always
    with pytest.raises(errors.TimeoutError):
        run(client.send_message(PRIVATE_PEER, 'سلام 🔥', parse_mode=None))
    blocks = [d['text'] for d in deliveries if d['text'].startswith('[CustomEmoji]')]
    assert blocks and 'Send: FAILED' in blocks[-1]


def test_debug_flags_off_silence_custom_blocks(monkeypatch, deliveries):
    monkeypatch.setattr(config, 'CUSTOM_EMOJI_DEBUG', False)
    client, _ = make_client()
    run(client.send_message(PRIVATE_PEER, 'سلام 🔥', parse_mode=None))
    assert not [d for d in deliveries if d['text'].startswith('[CustomEmoji]')]


def test_custom_emoji_block_never_leaks_secrets(deliveries):
    """قرارداد امنیتی: توکن/سشن هرگز در بلوک debug ظاهر نمی‌شود."""
    session_like = 'A' * 72  # شبیه session string
    block = tlog.format_custom_emoji_debug(chat_type='Group', emoji='🔥',
                                           document_id=FIRE,
                                           send_status=f'SUCCESS {session_like}')
    assert session_like not in tlog.redact(block)


# ============================== کلاینت Bot دست‌نخورده ==============================
def test_bot_client_fully_untouched():
    client = OfflineClient(bot=True)
    assert mod.install_premium_emoji_converter(client, account=client.account) is None
    assert inspect.ismethod(client.send_message)
    assert inspect.ismethod(client.send_file)
    assert not hasattr(client, '_premium_emoji_converter')
    run(client.send_message(PRIVATE_PEER, 'سلام 🔥', parse_mode=None))
    assert custom_entities_of(last_send_request(client)) == []


def test_config_default_converter_and_debug_flags():
    source = (ROOT / 'config.py').read_text(encoding='utf-8')
    assert 'PREMIUM_EMOJI_DEBUG = True' in source
    assert 'CUSTOM_EMOJI_DEBUG = True' in source
    assert "LOG_LEVEL = 'ERROR'" in source
    assert 'ADMIN_LOG_IDS = [8359698350]' in source
    assert 'PREMIUM_EMOJI_OUTGOING_FIX = True' in source
