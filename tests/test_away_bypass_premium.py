"""Acceptance tests — اصلاح نهایی v0.09.14: AWAY_BYPASS_PREMIUM + Presence.

سناریوهای اجباری spec مالک (همه آفلاین و بدون شبکه):

    1) پیام عدم حضور نباید وارد Premium Emoji Resend شود:
       - بدون Custom Emoji Pipeline (کانورتر قبل از ارسال)
       - بدون Resend
       - بدون Delete/New Send
       کلید: AWAY_BYPASS_PREMIUM = True (پیش‌فرض)

    2) تست Presence Manager:
       - ثبت «قبل ارسال Away وضعیت چیست» → بلوک [مدیریت وضعیت]
       - ثبت «بعد ارسال چه زمانی Offline Restore می‌شود» → بلوک با زمان

    3) تست واقعی:
       Chat A: پیام اول → Away | پیام دوم → هیچ پاسخ
       Chat B: پیام اول → Away

    4) گزارش زمان Online Restore (زمان‌سنجی واقعی در تست)

هدف: Away و Premium Emoji کاملاً مستقل باشند.
"""
import asyncio
import time
from types import SimpleNamespace as NS

import pytest
from telethon.tl.functions.account import UpdateStatusRequest
from telethon.tl.functions.messages import (
    DeleteMessagesRequest,
    GetMessagesRequest,
    SendMessageRequest,
)

import config
import db
from services import away as away_service
from services import away_bypass
from services import premium_emoji_converter as converter_mod
from services import telegram_logger as tlog
from services.emoji_resend_manager import (
    EmojiResendManager,
    install_emoji_resend_manager,
    uninstall_emoji_resend_manager,
)
from services.presence_manager import (
    describe_status,
    install_presence_manager,
    report_pre_away_status,
    uninstall_presence_manager,
)

UID = 8359698350
CHAT_A = 1001
CHAT_B = 1002
FIRE_ID = 111111
PEER = None  # classification فقط isinstance چک می‌کند


def run(coro):
    return asyncio.run(coro)


# ================================================== کلاینت یکپارچه تقلبی
class PipelineClient:
    """کلاینت تقلبی Self: هندلرها + ارسال + حذف + واکشی + TL + Presence.

    - ``send_message`` ابتدا TL SendMessageRequest را از ``_call`` عبور
      می‌دهد (تا Presence Manager آن را ببیند) و بعد «ارسال» ثبت می‌شود.
    - ``_parse_message_text`` الگوی Telethon با parse_mode=None.
    """

    def __init__(self):
        self.handlers = []
        self.sends = []      # (chat_id, text, kwargs, entities)
        self.deleted = []    # پیام‌های حذف‌شده
        self.fetched = []    # get_messages calls
        self.network = []    # (time, request) — همه درخواست‌های TL
        self._sender = object()   # معادل client._sender پس از اتصال
        self._next_id = 700

    # --- Telethon events
    def on(self, *args, **kwargs):
        def decorator(fn):
            self.handlers.append(fn)
            return fn
        return decorator

    def handler_of(self, name):
        for fn in self.handlers:
            if fn.__name__ == name:
                return fn
        raise AssertionError(f'handler {name} not registered')

    # --- Telethon internals
    async def _call(self, sender, request, ordered=False,
                    flood_sleep_threshold=None):
        self.network.append((time.monotonic(), request))
        return NS(id=1)

    async def _parse_message_text(self, text, parse_mode):
        return text, []

    # --- Telethon send/delete/fetch
    async def send_message(self, chat_id, message=None,
                           formatting_entities=None, parse_mode=(), **kwargs):
        # نام پارامترها دقیقاً مثل Telethon است تا کانورتر
        # (بررسی ``field in inspect.signature``) آن را wrap کند و
        # ``formatting_entities`` تزریق‌شده هم در فراخوانی باقی بماند.
        await self._call(None, SendMessageRequest(
            peer=NS(), message=message or '', random_id=self._next_id))
        self._next_id += 1
        self.sends.append((chat_id, message, kwargs, formatting_entities))
        return NS(id=self._next_id, chat_id=chat_id, message=message,
                  entities=formatting_entities)

    async def delete_messages(self, entity, message_ids, **kwargs):
        self.deleted.extend(list(message_ids))
        return NS(pts_count=len(message_ids))

    async def get_messages(self, entity, ids=None, **kwargs):
        self.fetched.append(ids)
        return []


def make_sent_message(client, chat_id, text):
    """شیء پیام ارسال‌شده برای رویداد outgoing (مطابق Telethon)."""
    idx = next(i for i, (c, t, _k, _e) in enumerate(client.sends)
               if c == chat_id and t == text)
    return NS(id=701 + idx, chat_id=chat_id, message=text, entities=[],
              action=None, fwd_from=None, via_bot_id=None, grouped_id=None,
              media=None, out=True)


def outgoing_event(message):
    return NS(message=message, chat_id=message.chat_id)


def incoming_event(*, sender_id, text='سلام', msg_id=1):
    sender = NS(bot=False, is_self=False, id=sender_id)
    message = NS(action=None, sender_id=sender_id, id=msg_id,
                 chat_id=sender_id, message=text)

    async def get_sender():
        return sender

    return NS(message=message, chat_id=sender_id, is_private=True,
              raw_text=text, get_sender=get_sender)


def make_engine(**kw):
    """موتور کانورتر واقعی با نگاشت فقط برای 🔥 (آفلاین و قطعی)."""
    return converter_mod.PremiumEmojiConverter(
        premium=True, is_enabled=lambda: True,
        mapping={'🔥': (FIRE_ID,)}, **kw)


@pytest.fixture()
def clean_env(monkeypatch):
    monkeypatch.setattr(tlog, 'log_token', lambda: '')
    monkeypatch.setattr(config, 'AWAY_BYPASS_PREMIUM', True)
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_OUTGOING_FIX', True)
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)
    monkeypatch.setattr(config, 'CUSTOM_EMOJI_DEBUG', False)
    monkeypatch.setattr(converter_mod, 'REJECTION_COOLDOWN_SECONDS', 0.0)
    db.update_user_settings(UID, {'away_enabled': False,
                                  'away_text': db.DEFAULT_AWAY_TEXT,
                                  'away_sent_chats': {},
                                  'away_active_session': ''})
    away_bypass.clear_registry()
    yield
    away_bypass.clear_registry()
    db.update_user_settings(UID, {'away_enabled': False,
                                  'away_sent_chats': {}})


def build_full_pipeline(client, *, presence_delay=0.05):
    """نصب کامل مثل self.py: کانورتر → Resend → outgoing → Away → Presence."""
    account = NS(bot=False, premium=True, id=UID)
    engine = converter_mod.install_premium_emoji_converter(
        client, account=account, is_enabled=lambda: True)
    # نگاشت قطعی تست: فقط 🔥 با شناسه ثابت FIRE_ID
    engine.mapping = {'🔥': (FIRE_ID,)}
    engine.selectors = {'🔥': converter_mod._Picker(
        converter_mod._sanitize_pool((FIRE_ID,), True))}
    install_emoji_resend_manager(client, engine, is_enabled=lambda: True,
                                 owner_id=UID)
    from services.premium_emoji_converter import (
        install_premium_emoji_outgoing_injector,
    )
    install_premium_emoji_outgoing_injector(client, engine)
    away_service.register_away_handlers(client, UID)
    install_presence_manager(client, UID, is_active=lambda: True,
                             offline_delay=presence_delay)
    return engine


# ================================================== 1) AWAY_BYPASS_PREMIUM
def test_bypass_flag_defaults_to_true(monkeypatch):
    """کلید AWAY_BYPASS_PREMIUM پیش‌فرض True است (حتی بدون config)."""
    monkeypatch.delattr(config, 'AWAY_BYPASS_PREMIUM', raising=False)
    assert away_bypass.bypass_enabled() is True
    monkeypatch.setattr(config, 'AWAY_BYPASS_PREMIUM', False, raising=False)
    assert away_bypass.bypass_enabled() is False


def test_away_reply_skips_converter_pipeline(clean_env):
    """پاسخ عدم حضور بدون Custom Emoji Pipeline ارسال می‌شود (متن خام)."""
    client = PipelineClient()
    engine = build_full_pipeline(client)
    assert engine.mapping.get('🔥')  # نگاشت آماده است (پایپ‌لاین فعال)
    away_service.set_enabled(UID, True)
    away_service.set_text(UID, 'الان نیستم 🔥')

    run(client.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=CHAT_A)))

    assert len(client.sends) == 1
    chat_id, text, _kwargs, entities = client.sends[0]
    assert chat_id == CHAT_A
    assert text == 'الان نیستم 🔥'                      # متن دست‌نخورده
    assert not entities                                 # ❌ هیچ Entity ویژه‌ای
    # ❌ هیچ حذف/واکشی (Resend) برای پاسخ عدم حضور رخ نداده است
    assert client.deleted == []
    assert client.fetched == []


def test_converter_converts_normal_messages_but_not_away(clean_env):
    """کنترل: پیام عادی 🔥 → تبدیل؛ پاسخ عدم حضور 🔥 → بدون تبدیل."""
    client = PipelineClient()
    build_full_pipeline(client)
    # پیام عادی (Away خاموش) → پایپ‌لاین تبدیل انجام می‌دهد
    run(client.send_message(CHAT_B, 'موفق شد 🔥'))
    ents = client.sends[0][3]
    assert ents and ents[0].document_id == FIRE_ID      # ✅ تبدیل عادی
    # پاسخ عدم حضور → هیچ تبدیلی
    away_service.set_enabled(UID, True)
    away_service.set_text(UID, 'برگشت نیستم 🔥')
    run(client.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=CHAT_A)))
    away_ents = client.sends[1][3]
    assert not away_ents                                # ❌ بدون Pipeline


def test_away_reply_never_enters_resend_manager(clean_env):
    """مدیر ارسال دوباره (Resend) پاسخ عدم حضور را نادیده می‌گیرد."""
    client = PipelineClient()
    build_full_pipeline(client)
    away_service.set_enabled(UID, True)
    away_service.set_text(UID, 'الان نیستم 🔥')
    run(client.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=CHAT_A)))

    # رویداد outgoing پیام پاسخ عدم حضور (مثل سرور واقعی) → هندلر پریمیوم
    manager = client._premium_resend_manager
    called = []
    monkey_single = EmojiResendManager._handle_single
    async def spy_single(self, message, *, allow_resend):
        called.append(message)
        return await monkey_single(self, message, allow_resend=allow_resend)
    EmojiResendManager._handle_single = spy_single
    try:
        sent = make_sent_message(client, CHAT_A, 'الان نیستم 🔥')
        result = run(client.handler_of('_outgoing_premium_fix')(
            outgoing_event(sent)))
        assert result is None  # هندلر هیچ استثنایی ندارد
    finally:
        EmojiResendManager._handle_single = monkey_single
    assert called == []            # ❌ هیچ پردازش Resend انجام نشد
    assert client.deleted == []    # ❌ بدون Delete
    assert len(client.sends) == 1  # ❌ بدون New Send


def test_away_reply_skips_album_resend_path(clean_env):
    """حتی اگر پیام عدم حضور گروهی (آلبوم) فرض شود، Resend ورود نمی‌کند."""
    away_service.set_enabled(UID, True)
    msg = NS(id=55, chat_id=CHAT_A, message='الان نیستم', entities=[],
             action=None, fwd_from=None, via_bot_id=None, grouped_id=9,
             media=None, out=True)
    away_bypass.mark_away_reply(msg)
    manager = EmojiResendManager(PipelineClient(), make_engine(),
                                 is_enabled=lambda: True, owner_id=UID)
    result = run(manager.handle_outgoing(msg))
    assert result == 'skipped'   # قبل از صف آلبوم، بای‌پس می‌شود


def test_away_bypass_registry_and_guard_units():
    """یونیت: registry پیام‌ها + context guard + TTL."""
    async def flow():
        assert away_bypass.active() is False
        async with away_bypass.away_send_guard():
            assert away_bypass.active() is True
        assert away_bypass.active() is False
        # guard خاموش (کلید False) → شفاف
        async with away_bypass.away_send_guard(enabled=False):
            assert away_bypass.active() is False
    run(flow())
    msg = NS(id=9, chat_id=CHAT_A, message='x')
    assert away_bypass.mark_away_reply(msg) is True
    assert away_bypass.is_away_reply(msg) is True
    other = NS(id=10, chat_id=CHAT_A, message='x')
    assert away_bypass.is_away_reply(other) is False
    away_bypass.clear_registry()
    assert away_bypass.is_away_reply(msg) is False


def test_bypass_disabled_flag_restores_normal_resend(clean_env, monkeypatch):
    """AWAY_BYPASS_PREMIUM=False → Resend مثل قبل پیام‌های عدم حضور را می‌بیند."""
    monkeypatch.setattr(config, 'AWAY_BYPASS_PREMIUM', False)
    msg = NS(id=61, chat_id=CHAT_A, message='الان نیستم 🔥', entities=[],
             action=None, fwd_from=None, via_bot_id=None, grouped_id=None,
             media=None, out=True)
    assert away_bypass.mark_away_reply(msg) is False   # ثبت نمی‌شود
    manager = EmojiResendManager(PipelineClient(), make_engine(),
                                 is_enabled=lambda: True, owner_id=UID)
    seen = []
    async def fake_single(message, *, allow_resend):
        seen.append(message)
        return 'skipped'
    manager._handle_single = fake_single
    assert run(manager.handle_outgoing(msg)) == 'skipped'
    assert seen == [msg]           # ✅ مسیر عادی Resend فعال است


# ================================================== 3) تست واقعی Chat A / B
def test_real_matrix_chat_a_once_then_nothing_chat_b_once(clean_env):
    """سناریوی واقعی اجباری: A اول → Away، A دوم → هیچ، B اول → Away.

    همه با پایپ‌لاین کامل: کانورتر + Resend + outgoing + Presence.
    """
    client = PipelineClient()
    build_full_pipeline(client)
    away_service.set_enabled(UID, True)
    away_service.set_text(UID, 'الان نیستم 🔥')
    away_handler = client.handler_of('_away_incoming_handler')
    outgoing_handler = client.handler_of('_outgoing_premium_fix')

    # --- Chat A پیام اول → Away
    run(away_handler(incoming_event(sender_id=CHAT_A, msg_id=1)))
    assert len(client.sends) == 1
    assert client.sends[0][0] == CHAT_A
    assert client.sends[0][1] == 'الان نیستم 🔥'
    # رویداد outgoing پیام پاسخ → Resend هیچ کاری نمی‌کند
    run(outgoing_handler(outgoing_event(
        make_sent_message(client, CHAT_A, 'الان نیستم 🔥'))))
    assert client.deleted == []          # ❌ بدون Delete
    assert len(client.sends) == 1        # ❌ بدون New Send

    # --- Chat A پیام دوم → هیچ پاسخ
    run(away_handler(incoming_event(sender_id=CHAT_A, msg_id=2, text='خوبی؟')))
    assert len(client.sends) == 1

    # --- Chat B پیام اول → Away (مدیریت جدا per-chat)
    run(away_handler(incoming_event(sender_id=CHAT_B, msg_id=3)))
    assert len(client.sends) == 2
    assert client.sends[1][0] == CHAT_B
    run(outgoing_handler(outgoing_event(
        make_sent_message(client, CHAT_B, 'الان نیستم 🔥'))))
    assert client.deleted == []
    assert len(client.sends) == 2

    # ساختار دیتابیس درخواستی مالک
    sent_chats = db.get_user_settings(UID)['away_sent_chats']
    assert set(sent_chats) == {str(CHAT_A), str(CHAT_B)}
    # بدون هیچ درخواست حذف در کل سناریو
    assert not [r for _t, r in client.network
                if isinstance(r, DeleteMessagesRequest)]
    assert not [r for _t, r in client.network
                if isinstance(r, GetMessagesRequest)]


# ================================================== 2 + 4) Presence Manager
class PresenceClient:
    def __init__(self):
        self.network = []
        self.times = []
        self._sender = object()   # معادل client._sender پس از اتصال

    async def _call(self, sender, request, ordered=False,
                    flood_sleep_threshold=None):
        self.times.append(time.monotonic())
        self.network.append(request)
        return NS(id=1)

    def reached(self, cls):
        return [r for r in self.network if isinstance(r, cls)]


def test_presence_pre_send_status_and_offline_restore(clean_env, monkeypatch):
    """تست Presence: ثبت وضعیت قبل ارسال + زمان Offline Restore بعد ارسال."""
    presence_blocks = []
    monkeypatch.setattr(tlog, 'send_presence_debug',
                        lambda block, **kw: presence_blocks.append(block)
                        or True)
    client = PresenceClient()
    assert install_presence_manager(client, UID, is_active=lambda: True,
                                    offline_delay=0.05) is True
    # — قبل ارسال: وضعیت اولیه «نامشخص» ثبت می‌شود
    label_before = report_pre_away_status(client)
    assert label_before == 'نامشخص'
    assert any('قبل ارسال پاسخ عدم حضور: نامشخص' in b
               for b in presence_blocks)

    async def scenario():
        send_at = time.monotonic()
        await client._call(None, SendMessageRequest(
            peer=NS(), message='الان نیستم 🔥', random_id=1))
        await asyncio.sleep(0.3)     # فرصت debounce بازگردانی آفلاین
        return send_at

    send_at = run(scenario())
    # — بعد ارسال: Offline Restore خودکار
    offline = [r for r in client.reached(UpdateStatusRequest)
               if r.offline is True]
    assert len(offline) == 1                    # ✅ آفلاین دوباره اعمال شد
    assert not [r for r in client.reached(UpdateStatusRequest)
                if r.offline is False]          # ❌ هیچ آنلاین‌سازی
    assert describe_status(client) == 'Offline'
    # — زمان‌سنجی: بازگردانی ≈ offline_delay بعد از ارسال (گزارش spec مالک)
    offline_at = client.times[-1]
    restore_delay = offline_at - send_at
    assert 0.04 <= restore_delay <= 0.5
    # — بلوک گزارش Offline Restore با زمان
    assert any('Offline Restore' in b and 'بعد از ارسال' in b
               for b in presence_blocks)
    uninstall_presence_manager(client)


def test_presence_pre_status_known_offline_when_asserted(clean_env):
    """وقتی آفلاین‌سازی فوری انجام شده باشد، وضعیت قبل ارسال = Offline."""
    client = PresenceClient()
    install_presence_manager(client, UID, is_active=lambda: True,
                             offline_delay=0.02)
    from services.presence_manager import assert_offline

    async def scenario():
        ok = assert_offline(client)   # داخل رویداد لوپ واقعی
        await asyncio.sleep(0.15)     # فرصت اجرای تسک آفلاین‌سازی
        return ok

    assert run(scenario()) is True
    assert describe_status(client) == 'Offline'
    assert report_pre_away_status(client) == 'Offline'


def test_presence_report_safe_without_manager():
    """بدون Presence Manager نصب‌شده، گزارش وضعیت «نصب نیست» و بدون خطا."""
    assert describe_status(PipelineClient()) == 'نصب نیست'
    assert report_pre_away_status(PipelineClient()) == 'نصب نیست'


# ================================================== استقلال کامل دو سیستم
def test_away_and_premium_fully_independent(clean_env):
    """حذف کامل پریمیوم نباید روی Away اثر بگذارد (و برعکس)."""
    client = PipelineClient()
    build_full_pipeline(client)
    away_service.set_enabled(UID, True)
    away_service.set_text(UID, 'الان نیستم 🔥')
    # کل سیستم پریمیوم را بردار (مثل خاموشی کامل)
    uninstall_emoji_resend_manager(client)
    away_handler = client.handler_of('_away_incoming_handler')

    async def scenario():
        # پاسخ عدم حضور همچنان بدون خطا و بدون تبدیل ارسال می‌شود
        await away_handler(incoming_event(sender_id=CHAT_A))
        # و Away دوباره آفلاین می‌شود (Presence مستقل از پریمیوم) —
        # debounce باید در همان رویداد لوپ فرصت اجرا داشته باشد.
        await asyncio.sleep(0.2)

    run(scenario())
    assert len(client.sends) == 1
    assert client.sends[0][1] == 'الان نیستم 🔥'
    offline = [r for _t, r in client.network
               if isinstance(r, UpdateStatusRequest) and r.offline is True]
    assert len(offline) >= 1
