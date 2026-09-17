"""Acceptance tests — Premium Emoji Debug + Telegram Logging System (offline).

فقط مرز شبکه شبیه‌سازی می‌شود؛ هیچ لاگین واقعی تلگرام و هیچ ارسال واقعی
رباتی در تست‌ها انجام نمی‌شود (قرارداد AGENTS.md). ارسال‌های logger با
monkeypatch روی _enqueue ضبط می‌شوند.
"""
import asyncio
import copy
import inspect
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from types import SimpleNamespace as NS

import pytest
from telethon import TelegramClient, errors, functions, types
from telethon.sessions import MemorySession

import config
import premium_emoji_mapping as mapping_module
from services import premium_emoji_converter as mod
from services import telegram_logger as tlog

NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)
PEER = types.InputPeerUser(123, 456)
LAUGH = mapping_module.PREMIUM_EMOJI_MAP['😂'][0]
FIRE = mapping_module.PREMIUM_EMOJI_MAP['🔥'][0]
HEART = mapping_module.PREMIUM_EMOJI_MAP['❤️'][0]


def run(coro):
    return asyncio.run(coro)


class OfflineClient(TelegramClient):
    """Real Telethon methods; only the network boundary is simulated."""

    def __init__(self, *, bot=False, premium=True):
        super().__init__(MemorySession(), 12345, 'offline-test-only')
        self._mb_entity_cache.extend([types.User(123, access_hash=456)], [])
        self.calls = []
        self.committed = []
        self.fail = None
        self.account = NS(bot=bot, premium=premium, id=8359698350)

    async def _call(self, sender, request, ordered=False, flood_sleep_threshold=None):
        bytes(request)  # real TL serialization
        self.calls.append(copy.deepcopy(request))
        if self.fail:
            exc = self.fail(request, len(self.calls))
            if exc:
                raise exc
        if isinstance(request, functions.messages.SendMessageRequest):
            self.committed.append(request)
            return types.UpdateShortSentMessage(len(self.committed), 1, 1, NOW,
                                                out=True, entities=request.entities)
        if isinstance(request, functions.messages.EditMessageRequest):
            self.committed.append(request)
            message = types.Message(request.id, types.PeerUser(123), date=NOW,
                                    out=True, message=request.message,
                                    entities=request.entities)
            return types.Updates([types.UpdateEditMessage(message, 1, 1)], [], [],
                                 NOW, 1)
        raise AssertionError('Unexpected network request: ' + type(request).__name__)


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    """Default patch state: converter ON, strict mapping, DEBUG visible."""
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_CONVERTER_ENABLED', True)
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_DEBUG', True)
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


def make_engine(client, *, flag=True):
    return mod.install_premium_emoji_converter(
        client, account=client.account, is_enabled=lambda: flag)


def custom_entities_of(request):
    return [e for e in getattr(request, 'entities', None) or ()
            if isinstance(e, types.MessageEntityCustomEmoji)]


# ---------------------------------------------------- 1) premium debug logging
def test_premium_debug_block_emitted_for_self_send(deliveries):
    client = OfflineClient()
    make_engine(client)
    run(client.send_message(PEER, 'سلام 😂🔥❤️', parse_mode=None))
    blocks = [d['text'] for d in deliveries if '[PREMIUM DEBUG]' in d['text']]
    assert blocks, 'debug block must be emitted when PREMIUM_EMOJI_DEBUG=True'
    block = blocks[-1]
    assert 'Client: Self' in block
    assert 'Method: send_message' in block
    assert 'Original Text: سلام 😂🔥❤️' in block
    assert 'Detected Emoji:' in block and '😂' in block
    assert 'Created Custom Emoji Entities: 3' in block
    assert 'Final Entity: MessageEntityCustomEmoji' in block


# ----------------------------------------------------- 2) self send logging
def test_self_send_conversion_logged_as_success(deliveries):
    client = OfflineClient()
    engine = make_engine(client)
    run(client.send_message(PEER, 'سلام 🔥', parse_mode=None))
    events_ = [d for d in deliveries if 'Premium Emoji Converted' in d['text']]
    assert events_, 'converted message must produce a SUCCESS report'
    text = events_[-1]['text']
    assert 'Status: SUCCESS' in text
    assert 'Method: send_message' in text
    assert str(engine.owner_id) in text


def test_conversion_report_targets_admin_log_ids(deliveries):
    client = OfflineClient()
    make_engine(client)
    run(client.send_message(PEER, 'سلام 🔥', parse_mode=None))
    converted = [d for d in deliveries if 'Premium Emoji Converted' in d['text']]
    assert converted and converted[-1]['chat_ids'] == [8359698350]


def test_reports_go_to_admin_not_to_target_chat(deliveries):
    """گزارش‌ها به Admin Log IDs می‌روند؛ نه به چتی که پیام به آن ارسال شده."""
    client = OfflineClient()
    make_engine(client)
    run(client.send_message(PEER, 'سلام 🔥', parse_mode=None))
    for delivery in deliveries:
        assert delivery['chat_ids'] == [8359698350]
        # شناسه چت فقط محتوای گزارش است (Chat / Chat ID)، نه مقصد ارسال
        assert ('Chat:' in delivery['text']) or ('Chat ID:' in delivery['text'])


# --------------------------------------------------------- 3) bot untouched
def test_bot_client_never_gets_converter_or_injector():
    client = OfflineClient(bot=True)
    assert mod.install_premium_emoji_converter(
        client, account=client.account) is None
    # متد کلاس دست‌نخورده = bound method واقعی Telethon (بدون wrap روی instance)
    assert inspect.ismethod(client.send_message)
    # تزریق‌کننده هم فقط روی کلاینت Self مجهز به کانورتر نصب می‌شود:
    assert mod.install_premium_emoji_outgoing_injector(client, NS(
        convert=lambda *a, **k: None, effective_enabled=lambda: True,
        disabled_until=0.0, owner_id=None)) is None
    assert not hasattr(client, '_premium_emoji_outgoing_injector')


def test_bot_send_never_converted_or_logged(deliveries):
    """کلاینت بات: هیچ wrap ای وجود ندارد؛ ارسال کامل بدون entity و بدون لاگ."""
    client = OfflineClient(bot=True)
    run(client.send_message(PEER, 'سلام 🔥', parse_mode=None))
    request = client.committed[0]
    assert isinstance(request, functions.messages.SendMessageRequest)
    assert custom_entities_of(request) == []
    assert not deliveries


# -------------------------------------------- 4) entity creation doc-id match
def test_entity_creation_logging_matches_mapping_ids(deliveries):
    client = OfflineClient()
    make_engine(client)
    run(client.send_message(PEER, 'سلام 😂', parse_mode=None))
    converted = [d for d in deliveries if 'Premium Emoji Converted' in d['text']]
    assert converted
    assert str(LAUGH) in converted[-1]['text']
    request = next(r for r in client.committed
                   if isinstance(r, functions.messages.SendMessageRequest))
    ids = {e.document_id for e in custom_entities_of(request)}
    assert ids == {LAUGH}


# ---------------------------------------------------------- 5) edit logging
def test_edit_message_conversion_logged(deliveries):
    client = OfflineClient()
    make_engine(client)
    sent = run(client.send_message(PEER, 'متن قدیمی', parse_mode=None))
    deliveries.clear()
    run(client.edit_message(PEER, sent.id, 'متن جدید 🔥', parse_mode=None))
    blocks = [d['text'] for d in deliveries if '[PREMIUM DEBUG]' in d['text']]
    assert blocks and 'Method: edit_message' in blocks[-1]
    request = next(r for r in client.committed
                   if isinstance(r, functions.messages.EditMessageRequest))
    assert {e.document_id for e in custom_entities_of(request)} == {FIRE}


def test_edit_message_entity_not_lost_when_reconverted(deliveries):
    """RC-B: پیام پرمیوم ویرایش می‌شود؛ اگر همان ایموجی در متن جدید باشد،
    entity از نو ساخته می‌شود و حذف نمی‌شود."""
    client = OfflineClient()
    make_engine(client)
    sent = run(client.send_message(PEER, 'سلام 🔥', parse_mode=None))
    assert custom_entities_of(next(
        r for r in client.committed
        if isinstance(r, functions.messages.SendMessageRequest)))
    deliveries.clear()
    edited = run(client.edit_message(sent, 'سلام دوباره 🔥', parse_mode=None))
    assert custom_entities_of(next(
        r for r in client.committed
        if isinstance(r, functions.messages.EditMessageRequest)))
    assert edited.message == 'سلام دوباره 🔥'


# ------------------------------------------------------- 6) fallback logging
def test_telegram_rejection_logs_fallback_and_sends_original(deliveries):
    client = OfflineClient()
    make_engine(client)

    def fail_once(request, call_index):
        if call_index == 1 and isinstance(request, functions.messages.SendMessageRequest):
            return errors.PremiumAccountRequiredError(request)
        return None

    client.fail = fail_once
    sent = run(client.send_message(PEER, 'سلام 🔥', parse_mode=None))
    fallbacks = [d for d in deliveries if 'Premium Emoji Fallback' in d['text']]
    assert fallbacks, 'rejection must produce a fallback report'
    text = fallbacks[-1]['text']
    assert 'Telegram rejected entity' in text
    assert 'Original message sent' in text
    # پیام اصلی بدون entity ارسال شده؛ هیچ پیام دوم/تکراری وجود ندارد
    sends = [r for r in client.committed
             if isinstance(r, functions.messages.SendMessageRequest)]
    assert len(client.calls) == 2  # تلاش پرمیوم + retry اصلی (یک‌بار)
    assert len(sends) == 1
    assert custom_entities_of(sends[0]) == []
    assert sent.message == 'سلام 🔥'


def test_conversion_failure_logs_error_and_sends_original(deliveries, monkeypatch):
    client = OfflineClient()
    engine = make_engine(client)

    def boom(*args, **kwargs):
        raise RuntimeError('cache exploded')

    monkeypatch.setattr(engine, 'convert', boom)
    sent = run(client.send_message(PEER, 'سلام 🔥', parse_mode=None))
    errors_ = [d for d in deliveries if 'Premium Emoji Error' in d['text']]
    assert errors_, 'conversion failure must be reported'
    text = errors_[-1]['text']
    assert 'services/premium_emoji_converter.py' in text
    assert 'RuntimeError' in text
    assert 'Original message sent' in [
        d['text'] for d in deliveries if 'Premium Emoji Fallback' in d['text']][-1]
    assert sent.message == 'سلام 🔥'


# ------------------------------------------------------- 7) error reporting
def test_send_error_contains_file_line_error(deliveries):
    try:
        raise ValueError('MessageEntity invalid')
    except ValueError as exc:
        frame = __import__('traceback').extract_tb(exc.__traceback__)[-1]
        tlog.send_error('❌ Premium Emoji Error', {
            'File': 'services/premium_emoji_converter.py',
            'Line': frame.lineno,
            'Error': str(exc),
        })
    assert deliveries and 'MessageEntity invalid' in deliveries[-1]['text']


def test_floodwait_and_rpc_logged_and_reraised(deliveries):
    client = OfflineClient()
    make_engine(client)

    def fail_all(request, call_index):
        return errors.FloodWaitError(request, capture=3)

    client.fail = fail_all
    with pytest.raises(errors.FloodWaitError):
        run(client.send_message(PEER, 'سلام 🔥', parse_mode=None))
    assert any('FloodWait' in d['text'] for d in deliveries)


# ---------------------------------------------- 8) token leak protection
def test_bot_token_never_appears_in_logs(monkeypatch):
    # توکن ساختگی؛ هم توسط redact پوشش داده می‌شود و هم شکل توکن واقعی (30+) نیست.
    secret = '111222333:AAFakeTokenForLeakTest'
    monkeypatch.setattr(tlog, 'log_token', lambda: secret)
    scrubbed = tlog.redact(f'payload token={secret} end')
    assert secret not in scrubbed
    assert '[REDACTED-TOKEN]' in scrubbed or '[REDACTED]' in scrubbed
    assert tlog.redact('bot 1234567890:AAExampleTokenValue1234567890 ok') \
        .count('AAExampleTokenValue') == 0


def test_source_never_contains_log_bot_token():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for path in [root / 'services' / 'telegram_logger.py',
                 root / 'services' / 'premium_emoji_converter.py',
                 root / 'config.py', root / 'self.py']:
        source = path.read_text(encoding='utf-8')
        assert 'PREMIUM_LOG_BOT_TOKEN' in source or path.name != 'telegram_logger.py'
        # هیچ توکن واقعی (شکل numeric-id:AA...) در سورس مجاز نیست
        import re
        assert not re.search(r'\b\d{8,10}:AA[A-Za-z0-9_-]{30,}', source), path


# --------------------------------------------- 9) session leak protection
def test_session_string_never_appears_in_logs():
    fake_session = ('1BQANOTEuMTA4LAD5AxLBMQD1BLMnJ5Xl1cWu5vEeBq0h9HkXQmZl7'
                    'bGZvFZq0Ckq3nZm5eY2FpZm9vYmFyX2lkIGlzIG5vdCByZWFs')
    scrubbed = tlog.redact(f'SESSION={fake_session}')
    assert fake_session not in scrubbed
    assert '[REDACTED' in scrubbed
    scrubbed2 = tlog.redact(f'login ok session={fake_session[:80]}AAAA tail')
    assert fake_session[:80] not in scrubbed2


def test_password_and_api_hash_redacted():
    scrubbed = tlog.redact('API_HASH=abcdef0123456789abcdef0123456789 PASS=s3cret')
    assert 'abcdef0123456789' not in scrubbed
    assert 's3cret' not in scrubbed


# --------------------------------------------------- level & anti-spam rules
def test_log_level_off_silences_generic_channel(monkeypatch, deliveries):
    monkeypatch.setattr(config, 'LOG_LEVEL', 'OFF')
    assert tlog.send_log('generic info', {'Chat': 1}, level='INFO') is False
    assert tlog.send_warning('generic warning') is False
    assert not deliveries


def test_generic_error_passes_default_log_level(deliveries):
    assert config.LOG_LEVEL == 'ERROR'
    assert tlog.send_log('boom happened', {'Error': 'X'}, level='ERROR') is True
    assert deliveries and 'boom happened' in deliveries[-1]['text']


def test_premium_channel_respects_its_level(monkeypatch, deliveries):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_LOG_LEVEL', 'ERROR')
    assert tlog.send_premium_event('converted', {'Chat': 1},
                                   level='INFO') is False
    assert tlog.send_premium_event('premium error', {'Error': 'x'},
                                   level='ERROR', kind='premium_error') is True


def test_debug_block_requires_debug_flag_and_level(monkeypatch, deliveries):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_LOG_LEVEL', 'INFO')
    assert tlog.send_premium_debug_block('[PREMIUM DEBUG]\nClient: Self') is False
    assert not deliveries
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_LOG_LEVEL', 'DEBUG')
    monkeypatch.setattr(tlog, '_anti_spam_ok', lambda kind, now: True)
    assert tlog.send_premium_debug_block('[PREMIUM DEBUG]\nClient: Self') is True
    assert deliveries


def test_anti_spam_coalesces_repeated_events(deliveries):
    tlog.reset_rate_state()
    first = tlog.send_premium_event('🎨 Premium Emoji Converted',
                                    {'Chat': 1}, kind='converted')
    second = tlog.send_premium_event('🎨 Premium Emoji Converted',
                                     {'Chat': 1}, kind='converted')
    assert first is True and second is False
    assert len([d for d in deliveries if 'Converted' in d['text']]) == 1


def test_suppressed_counter_appears_on_next_send(deliveries, monkeypatch):
    tlog.reset_rate_state()
    tlog.send_premium_event('🎨 Premium Emoji Converted', {'Chat': 1},
                            kind='converted')
    tlog.send_premium_event('🎨 Premium Emoji Converted', {'Chat': 1},
                            kind='converted')  # suppressed
    with tlog._state_lock:
        tlog._last_sent.pop('converted', None)  # بازه فاصله منقضی شد
    tlog.send_premium_event('🎨 Premium Emoji Converted', {'Chat': 1},
                            kind='converted')
    last = [d for d in deliveries if 'Converted' in d['text']][-1]['text']
    assert '(+1 suppressed)' in last


# ------------------------------------------------------ local rotation files
def test_local_log_files_use_required_rotation(tmp_path, monkeypatch):
    monkeypatch.setattr(tlog, 'DATA_DIR', str(tmp_path), raising=False)
    logger = tlog._build_file_logger('rotcheck', 'rotcheck.log')
    handler = logger.handlers[-1]
    assert isinstance(handler, RotatingFileHandler)
    assert handler.maxBytes == 10 * 1024 * 1024
    assert handler.backupCount == 5


# ----------------------------------------------- 10) outgoing injector (fix)
def _outgoing_event(message, chat_id=777):
    return NS(message=message, chat_id=chat_id)


def test_outgoing_injector_fixes_unconverted_message(deliveries):
    """RC-A: پیام تایپ‌شده از اپ رسمی (بدون entity) بعد از ارسال پرمیوم می‌شود."""
    client = OfflineClient()
    engine = make_engine(client)
    handler = mod.install_premium_emoji_outgoing_injector(client, engine)
    message = types.Message(11, types.PeerUser(123), date=NOW, out=True,
                            message='سلام 😂🔥❤️', entities=None)
    message._input_chat = PEER
    run(handler(_outgoing_event(message)))
    edits = [r for r in client.committed
             if isinstance(r, functions.messages.EditMessageRequest)]
    assert len(edits) == 1
    entities = custom_entities_of(edits[0])
    assert {e.document_id for e in entities} == {LAUGH, FIRE, HEART}
    # متن هرگز عوض نمی‌شود؛ فقط entity اضافه می‌شود
    assert edits[0].message == 'سلام 😂🔥❤️'
    blocks = [d['text'] for d in deliveries if '[PREMIUM DEBUG]' in d['text']]
    assert blocks and 'Method: outgoing_fix' in blocks[-1]


def test_outgoing_injector_skips_already_premium_forwards_and_bot_content():
    client = OfflineClient()
    engine = make_engine(client)
    handler = mod.install_premium_emoji_outgoing_injector(client, engine)

    premium = types.Message(12, types.PeerUser(123), date=NOW, out=True,
                            message='سلام 🔥',
                            entities=[types.MessageEntityCustomEmoji(0, 2, FIRE)])
    fwd = types.Message(13, types.PeerUser(123), date=NOW, out=True,
                        message='سلام 🔥', entities=None,
                        fwd_from=types.MessageFwdHeader(date=NOW))
    via_bot = types.Message(14, types.PeerUser(123), date=NOW, out=True,
                            message='سلام 🔥', entities=None, via_bot_id=999)
    for message in (premium, fwd, via_bot):
        run(handler(_outgoing_event(message)))
    assert not [r for r in client.committed
                if isinstance(r, functions.messages.EditMessageRequest)]


def test_outgoing_injector_respects_disabled_flag(monkeypatch):
    client = OfflineClient()
    engine = make_engine(client, flag=False)
    handler = mod.install_premium_emoji_outgoing_injector(client, engine)
    message = types.Message(15, types.PeerUser(123), date=NOW, out=True,
                            message='سلام 🔥', entities=None)
    run(handler(_outgoing_event(message)))
    assert not [r for r in client.committed
                if isinstance(r, functions.messages.EditMessageRequest)]


def test_outgoing_injector_rejection_sets_cooldown_and_logs_fallback(deliveries):
    client = OfflineClient()
    engine = make_engine(client)
    handler = mod.install_premium_emoji_outgoing_injector(client, engine)

    def fail_all(request, call_index):
        return errors.PremiumAccountRequiredError(request)

    client.fail = fail_all
    message = types.Message(16, types.PeerUser(123), date=NOW, out=True,
                            message='سلام 🔥', entities=None)
    message._input_chat = PEER
    run(handler(_outgoing_event(message)))
    assert engine.disabled_until > 0
    assert any('Premium Emoji Fallback' in d['text'] for d in deliveries)
    # بعد از cooldown، پیام بعدی تزریق نمی‌شود
    client.fail = None
    message2 = types.Message(17, types.PeerUser(123), date=NOW, out=True,
                             message='سلام 🔥', entities=None)
    run(handler(_outgoing_event(message2)))
    assert not [r for r in client.committed
                if isinstance(r, functions.messages.EditMessageRequest)]


def test_uninstall_outgoing_injector_removes_handler():
    client = OfflineClient()
    engine = make_engine(client)
    handler = mod.install_premium_emoji_outgoing_injector(client, engine)
    assert client._premium_emoji_outgoing_injector is handler
    mod.uninstall_premium_emoji_outgoing_injector(client)
    assert not hasattr(client, '_premium_emoji_outgoing_injector')
    registered = [callback for callback, _event in client.list_event_handlers()]
    assert all(callback is not handler for callback in registered)


# -------------------------------------------- runtime path audit consistency
def test_self_send_paths_all_wrapped():
    """همه متدهای ارسال/ویرایش کلاینت Self باید wrap شده باشند."""
    client = OfflineClient()
    make_engine(client)
    # wrap شدن یعنی تابع ساده روی instance؛ دیگر bound method کلاس نیست
    assert not inspect.ismethod(client.send_message)
    assert not inspect.ismethod(client.send_file)
    assert not inspect.ismethod(client.edit_message)
    # forward_messages طبق قانون هرگز wrap نمی‌شود
    assert inspect.ismethod(client.forward_messages)
