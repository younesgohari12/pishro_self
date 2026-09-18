"""Acceptance tests — Audit نهایی v0.09.15: [AWAY_TRACE] + [PREMIUM_TRACE].

هدف spec مالک (فقط Audit — بدون قابلیت جدید):
    اثبات اینکه Away هیچ وقت وارد Premium نمی‌شود + ثبت تصمیم‌های
    Premium Resend + اتصال واقعی دو سیستم از سه نقطه مستقل:

        1) services/away.py           → begin/finalize AWAY_TRACE
        2) converter/injector گارد    → note_pipeline_guard(...)
        3) emoji_resend_manager       → note_resend_away_skip + PREMIUM_TRACE

فرمت بلوک‌ها دقیقاً مطابق spec مالک است (نام فیلدها انگلیسی، مقادیر فارسی).
"""
import asyncio
from types import SimpleNamespace as NS

import pytest

import config
import db
from services import away as away_service
from services import away_bypass
from services import premium_emoji_converter as converter_mod
from services import telegram_logger as tlog
from services.emoji_resend_manager import EmojiResendManager
from services.presence_manager import install_presence_manager

UID = 8359698350
CHAT_A = 1001
CHAT_B = 1002
FIRE_ID = 111111


def run(coro):
    return asyncio.run(coro)


# ================================================== harness (مثل v0.09.15)
class PipelineClient:
    def __init__(self):
        self.handlers = []
        self.sends = []
        self.deleted = []
        self.fetched = []
        self.network = []
        self._sender = object()
        self._next_id = 700

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

    async def _call(self, sender, request, ordered=False,
                    flood_sleep_threshold=None):
        self.network.append((0.0, request))
        return NS(id=1)

    async def _parse_message_text(self, text, parse_mode):
        return text, []

    async def send_message(self, chat_id, message=None,
                           formatting_entities=None, parse_mode=(), **kwargs):
        await self._call(None, NS(id=1))
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
    idx = next(i for i, (c, t, _k, _e) in enumerate(client.sends)
               if c == chat_id and t == text)
    return NS(id=701 + idx, chat_id=chat_id, message=text, entities=[],
              action=None, fwd_from=None, via_bot_id=None, grouped_id=None,
              media=None, out=True)


def outgoing_event(message):
    return NS(message=message, chat_id=message.chat_id)


class ResendClient(PipelineClient):
    """کلاینت با نمای سرور (get_messages) و حذف int-safe برای جریان Resend."""

    def __init__(self):
        super().__init__()
        self.server = {}

    async def get_messages(self, entity, ids=None, **kwargs):
        self.fetched.append(ids)
        return self.server.get(ids)

    async def get_input_entity(self, chat_id):
        return NS(user_id=chat_id)

    async def delete_messages(self, entity, message_ids, **kwargs):
        if not isinstance(message_ids, (list, tuple)):
            message_ids = [message_ids]
        self.deleted.extend(list(message_ids))
        return NS(pts_count=len(message_ids))


def incoming_event(*, sender_id, text='سلام', msg_id=1):
    sender = NS(bot=False, is_self=False, id=sender_id)
    message = NS(action=None, sender_id=sender_id, id=msg_id,
                 chat_id=sender_id, message=text)

    async def get_sender():
        return sender

    return NS(message=message, chat_id=sender_id, is_private=True,
              raw_text=text, get_sender=get_sender)


def build_full_pipeline(client):
    account = NS(bot=False, premium=True, id=UID)
    engine = converter_mod.install_premium_emoji_converter(
        client, account=account, is_enabled=lambda: True)
    engine.mapping = {'🔥': (FIRE_ID,)}
    engine.selectors = {'🔥': converter_mod._Picker(
        converter_mod._sanitize_pool((FIRE_ID,), True))}
    from services.emoji_resend_manager import install_emoji_resend_manager
    from services.premium_emoji_converter import (
        install_premium_emoji_outgoing_injector,
    )
    install_emoji_resend_manager(client, engine, is_enabled=lambda: True,
                                 owner_id=UID)
    install_premium_emoji_outgoing_injector(client, engine)
    away_service.register_away_handlers(client, UID)
    install_presence_manager(client, UID, is_active=lambda: True,
                             offline_delay=0.05)
    return engine


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
    away_bypass.clear_traces()
    yield
    away_bypass.clear_registry()
    away_bypass.clear_traces()
    db.update_user_settings(UID, {'away_enabled': False,
                                  'away_sent_chats': {}})


# ================================================== 1) فرمت دقیق بلوک‌ها
def test_away_trace_format_exact_spec():
    """[AWAY_TRACE] دقیقاً ۶ فیلد spec مالک، بعد از عنوان و خط خالی."""
    record = {
        'chat_id': 123456789,
        'trigger': 'پیام خصوصی ورودی (فرستنده=42, پیام=7)',
        'bypass_active': True,
        'guard_hits': ['converter'],
        'premium_pipeline_entered': False,
        'resend_entered': False,
        'resend_note': 'مدیر ارسال دوباره این پیام را دید و '
                       'بدون هیچ اقدامی رد کرد (registry)',
        'final_sender': 'client.send_message (بدون تبدیل — متن خام)',
        'send_error': None,
    }
    block = tlog.format_away_trace(record)
    lines = block.split('\n')
    assert lines[0] == '[AWAY_TRACE]'
    assert lines[1] == ''                              # خط خالی spec
    fields = [line.split(':')[0] for line in lines[2:]]
    assert fields == ['chat_id', 'trigger', 'bypass_active',
                      'premium_pipeline_entered', 'resend_entered',
                      'final_sender']
    assert 'chat_id: 123456789' in block
    assert 'bypass_active: بله (AWAY_BYPASS_PREMIUM=True)' in block
    assert 'premium_pipeline_entered: خیر — هرگز' in block
    assert '(گارد تایید کرد: converter)' in block
    assert 'resend_entered: خیر — هرگز' in block
    assert 'final_sender: client.send_message (بدون تبدیل — متن خام)' in block
    # نسخه ناموفق: ارسال شکست → خطای ثبت‌شده در final_sender
    fail = dict(record, final_sender=None, send_error='FloodWaitError')
    assert 'final_sender: ارسال ناموفق (FloodWaitError)' in \
        tlog.format_away_trace(fail)


def test_premium_trace_format_exact_spec():
    """[PREMIUM_TRACE] دقیقاً ۵ فیلد spec مالک، بعد از عنوان و خط خالی."""
    block = tlog.format_premium_trace(
        message_id=123, is_away=False,
        entity_check='انجام شد (نمای سرور)',
        delete_called=True, new_send_called=True)
    lines = block.split('\n')
    assert lines[0] == '[PREMIUM_TRACE]'
    assert lines[1] == ''                              # خط خالی spec
    fields = [line.split(':')[0] for line in lines[2:]]
    assert fields == ['message_id', 'is_away', 'entity_check',
                      'delete_called', 'new_send_called']
    assert 'is_away: خیر' in block
    assert 'delete_called: بله' in block
    assert 'new_send_called: بله' in block
    away_block = tlog.format_premium_trace(
        message_id=124, is_away=True,
        entity_check='انجام نشد (پاسخ عدم حضور — بدون بررسی سرور)',
        delete_called=False, new_send_called=False)
    assert 'is_away: بله' in away_block
    assert 'delete_called: خیر' in away_block
    assert 'new_send_called: خیر' in away_block


# ================================================== 2) AWAY_TRACE واقعی
def test_away_trace_emitted_with_guard_proof(clean_env):
    """هر ارسال Away یک [AWAY_TRACE] می‌دهد؛ گارد کانورتر آن را تأیید می‌کند."""
    away_blocks = []
    monkey_trace = tlog.send_away_trace
    monkeypatch_attr = None  # noqa - استفاده از monkeypatch فیکسچر زیر
    client = PipelineClient()
    build_full_pipeline(client)
    away_service.set_enabled(UID, True)
    away_service.set_text(UID, 'الان نیستم 🔥')

    import services.telegram_logger as tlog_mod
    original = tlog_mod.send_away_trace
    tlog_mod.send_away_trace = (
        lambda block, **kw: away_blocks.append(block) or True)
    try:
        run(client.handler_of('_away_incoming_handler')(
            incoming_event(sender_id=CHAT_A, msg_id=5)))
    finally:
        tlog_mod.send_away_trace = original

    assert len(away_blocks) == 1                       # یک Trace برای یک ارسال
    block = away_blocks[0]
    assert block.startswith('[AWAY_TRACE]\n\n')
    assert f'chat_id: {CHAT_A}' in block
    assert 'trigger: پیام خصوصی ورودی (فرستنده=1001, پیام=5)' in block
    assert 'bypass_active: بله (AWAY_BYPASS_PREMIUM=True)' in block
    # اثبات: Pipeline هرگز وارد نشد + گارد واقعاً برخورد کرد
    assert 'premium_pipeline_entered: خیر — هرگز' in block
    assert '(گارد تایید کرد: converter)' in block
    assert 'resend_entered: خیر — هرگز' in block
    assert 'final_sender: client.send_message' in block
    # خود پیام هم دست‌نخورده ارسال شد
    assert client.sends[0][1] == 'الان نیستم 🔥'
    assert not client.sends[0][3]                      # بدون entity
    # Trace در registry با کلید پیام ثبت شد (برای تأیید سمت Resend)
    assert away_bypass.trace_registry_size() == 1


def test_away_trace_persists_for_resend_confirmation(clean_env):
    """بعد از رویداد outgoing، Trace با یادداشت «دیدم و رد کردم» به‌روز می‌شود."""
    client = PipelineClient()
    build_full_pipeline(client)
    away_service.set_enabled(UID, True)
    away_service.set_text(UID, 'الان نیستم 🔥')
    run(client.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=CHAT_A, msg_id=6)))
    sent = make_sent_message(client, CHAT_A, 'الان نیستم 🔥')
    run(client.handler_of('_outgoing_premium_fix')(outgoing_event(sent)))
    assert away_bypass.trace_registry_size() == 1
    record = next(iter(away_bypass._TRACES.values()))
    assert record['premium_pipeline_entered'] is False
    assert 'converter' in record['guard_hits']
    assert record['resend_entered'] is False
    assert 'بدون هیچ اقدامی رد کرد' in (record['resend_note'] or '')


# ================================================== 3) PREMIUM_TRACE واقعی
def test_premium_trace_away_skip_is_unconditional(clean_env, monkeypatch):
    """مدیر Resend برای پیام Away همیشه [PREMIUM_TRACE] (is_away=بله) می‌دهد."""
    premium_blocks = []
    monkeypatch.setattr(tlog, 'send_premium_trace',
                        lambda block, **kw:
                        premium_blocks.append(block) or True)
    away_service.set_enabled(UID, True)
    msg = NS(id=55, chat_id=CHAT_A, message='الان نیستم 🔥', entities=[],
             action=None, fwd_from=None, via_bot_id=None, grouped_id=None,
             media=None, out=True)
    away_bypass.mark_away_reply(msg)
    manager = EmojiResendManager(PipelineClient(), None,
                                 is_enabled=lambda: False, owner_id=UID)
    assert run(manager.handle_outgoing(msg)) == 'skipped'
    assert len(premium_blocks) == 1                    # حتی با Resend خاموش
    block = premium_blocks[0]
    assert block.startswith('[PREMIUM_TRACE]\n\n')
    assert 'message_id: 55' in block
    assert 'is_away: بله' in block
    assert 'entity_check: انجام نشد' in block
    assert 'delete_called: خیر' in block
    assert 'new_send_called: خیر' in block


def test_premium_trace_full_resend_actions(clean_env, monkeypatch):
    """جریان کامل Resend: entity_check انجام شد، Delete بله، New Send بله."""
    monkeypatch.setattr(
        'services.premium_resend_service.VERIFY_DELAY_SECONDS', 0.0)
    premium_blocks = []
    monkeypatch.setattr(tlog, 'send_premium_trace',
                        lambda block, **kw:
                        premium_blocks.append(block) or True)

    client = ResendClient()
    build_full_pipeline(client)
    # پیام عادی با 🔥 ارسال می‌شود → کانورتر تبدیل می‌کند
    run(client.send_message(CHAT_B, 'موفق شد 🔥'))
    sent_view = NS(id=701, chat_id=CHAT_B, message='موفق شد 🔥',
                   entities=[], media=None, reply_to=None, silent=False)
    client.server[701] = sent_view                     # سرور: بدون entity
    manager = client._premium_resend_manager
    message = NS(id=701, chat_id=CHAT_B, message='موفق شد 🔥', entities=[],
                 action=None, fwd_from=None, via_bot_id=None,
                 grouped_id=None, media=None, out=True)
    result = run(manager.handle_outgoing(message))
    assert result == 'handled'
    # اقدام واقعی: حذف + ارسال جدید
    assert client.deleted == [701]
    resend_sends = [s for s in client.sends if s[1] == 'موفق شد 🔥'][1:]
    assert resend_sends and resend_sends[0][3]         # با entity ارسال شد
    # Trace نهایی: هر دو اقدام ثبت شده‌اند
    assert len(premium_blocks) == 1
    block = premium_blocks[0]
    assert 'message_id: 701' in block
    assert 'is_away: خیر' in block
    assert 'entity_check: انجام شد (نمای سرور)' in block
    assert 'delete_called: بله' in block
    assert 'new_send_called: بله' in block


def test_premium_trace_no_action_when_server_entity_exists(clean_env,
                                                           monkeypatch):
    """Entity سالم روی سرور → فقط بررسی؛ Delete/New Send هرگز خیر."""
    from telethon import types as telethon_types
    monkeypatch.setattr(
        'services.premium_resend_service.VERIFY_DELAY_SECONDS', 0.0)
    premium_blocks = []
    monkeypatch.setattr(tlog, 'send_premium_trace',
                        lambda block, **kw:
                        premium_blocks.append(block) or True)

    client = ResendClient()
    build_full_pipeline(client)
    entity = telethon_types.MessageEntityCustomEmoji(offset=0, length=2,
                                                     document_id=FIRE_ID)
    client.server[702] = NS(id=702, chat_id=CHAT_A, message='🔥 سلام',
                            entities=[entity], media=None, reply_to=None,
                            silent=False)
    manager = client._premium_resend_manager
    # نکته: event بدون entity می‌رسد (مثل پیام تازه ارسال‌شده)؛ فقط «نمای
    # سرور» entity دارد → مسیر بررسی سرور طی می‌شود.
    message = NS(id=702, chat_id=CHAT_A, message='🔥 سلام', entities=[],
                 action=None, fwd_from=None, via_bot_id=None,
                 grouped_id=None, media=None, out=True)
    assert run(manager.handle_outgoing(message)) == 'skipped'
    assert client.deleted == []
    assert len(premium_blocks) == 1
    block = premium_blocks[0]
    assert 'entity_check: انجام شد (نمای سرور) — Entity موجود' in block
    assert 'delete_called: خیر' in block
    assert 'new_send_called: خیر' in block
    assert 'is_away: خیر' in block


# ================================================== 4) ماتریس واقعی Chat A/B
def test_chat_matrix_one_trace_per_away_send(clean_env):
    """Chat A اول → یک Trace | A دوم → Trace جدید نیست | B اول → Trace خودش."""
    away_blocks = []
    client = PipelineClient()
    build_full_pipeline(client)
    away_service.set_enabled(UID, True)
    away_service.set_text(UID, 'الان نیستم 🔥')

    import services.telegram_logger as tlog_mod
    original = tlog_mod.send_away_trace
    tlog_mod.send_away_trace = (
        lambda block, **kw: away_blocks.append(block) or True)
    try:
        away_handler = client.handler_of('_away_incoming_handler')
        # Chat A پیام اول
        run(away_handler(incoming_event(sender_id=CHAT_A, msg_id=1)))
        assert len(away_blocks) == 1
        assert f'chat_id: {CHAT_A}' in away_blocks[0]
        # Chat A پیام دوم → هیچ پاسخ، هیچ Trace جدید
        run(away_handler(incoming_event(sender_id=CHAT_A, msg_id=2,
                                        text='خوبی؟')))
        assert len(away_blocks) == 1
        assert len(client.sends) == 1
        # Chat B پیام اول → Trace مستقل خودش
        run(away_handler(incoming_event(sender_id=CHAT_B, msg_id=3)))
        assert len(away_blocks) == 2
        assert len(client.sends) == 2
        assert f'chat_id: {CHAT_B}' in away_blocks[1]
    finally:
        tlog_mod.send_away_trace = original


def test_away_trace_independent_from_premium(clean_env):
    """حذف کامل پریمیوم → AWAY_TRACE همچنان صادر می‌شود (استقلال کامل)."""
    away_blocks = []
    client = PipelineClient()
    build_full_pipeline(client)
    from services.emoji_resend_manager import uninstall_emoji_resend_manager
    uninstall_emoji_resend_manager(client)
    away_service.set_enabled(UID, True)
    away_service.set_text(UID, 'الان نیستم 🔥')

    import services.telegram_logger as tlog_mod
    original = tlog_mod.send_away_trace
    tlog_mod.send_away_trace = (
        lambda block, **kw: away_blocks.append(block) or True)
    try:
        run(client.handler_of('_away_incoming_handler')(
            incoming_event(sender_id=CHAT_A, msg_id=9)))
    finally:
        tlog_mod.send_away_trace = original

    assert len(away_blocks) == 1
    block = away_blocks[0]
    assert 'bypass_active: بله (AWAY_BYPASS_PREMIUM=True)' in block
    assert 'premium_pipeline_entered: خیر — هرگز' in block
    assert 'final_sender: client.send_message' in block
    # بدون کانورتر فعال هم پیام خام است (هیچ تبدیلی ممکن نبوده)
    assert client.sends[0][1] == 'الان نیستم 🔥'


# ================================================== 5) تست runtime نهایی
def test_runtime_contract_premium_and_away(clean_env, monkeypatch):
    """تست runtime نهایی (دستور مالک — بدون هیچ قابلیت جدید):

    Premium: ارسال پیام → بررسی:
        delete=True / new_send=True / edit=False
    Away: ارسال پیام عدم حضور → بررسی:
        premium_entered=False / resend_entered=False
    """
    from telethon import functions as telethon_functions

    monkeypatch.setattr(
        'services.premium_resend_service.VERIFY_DELAY_SECONDS', 0.0)
    premium_blocks = []
    monkeypatch.setattr(tlog, 'send_premium_trace',
                        lambda block, **kw:
                        premium_blocks.append(block) or True)
    away_blocks = []
    monkeypatch.setattr(tlog, 'send_away_trace',
                        lambda block, **kw:
                        away_blocks.append(block) or True)

    client = ResendClient()
    build_full_pipeline(client)

    # ثبت‌کننده runtime برای edit: اگر هر مرحله‌ای edit_message را صدا
    # بزند اینجا ثبت می‌شود؛ قرارداد نهایی یعنی این لیست باید خالی بماند.
    edit_calls = []

    async def _edit_recorder(*a, **k):
        edit_calls.append((a, k))
        return NS(id=0)

    client.edit_message = _edit_recorder

    # ---------------------------------------------- Premium: ارسال پیام
    run(client.send_message(CHAT_B, 'موفق شد 🔥'))
    sent_view = NS(id=701, chat_id=CHAT_B, message='موفق شد 🔥',
                   entities=[], media=None, reply_to=None, silent=False)
    client.server[701] = sent_view                     # سرور: بدون entity
    sent = make_sent_message(client, CHAT_B, 'موفق شد 🔥')
    run(client.handler_of('_outgoing_premium_fix')(outgoing_event(sent)))

    # delete=True — حذف واقعی پیام اصلی انجام شد
    assert client.deleted == [701]
    # new_send=True — پیام «جدید» با Custom Emoji Entity ارسال شد
    resend_sends = [s for s in client.sends if s[1] == 'موفق شد 🔥'][1:]
    assert resend_sends and resend_sends[0][3]
    # edit=False — نه client.edit_message و نه EditMessageRequest در
    # جریان Premium Resend صادر نشد
    assert edit_calls == []
    assert not [r for _t, r in client.network
                if isinstance(r,
                              telethon_functions.messages.EditMessageRequest)]
    # Trace هم همان قرارداد را تأیید می‌کند
    assert len(premium_blocks) == 1
    pblock = premium_blocks[0]
    assert 'is_away: خیر' in pblock
    assert 'delete_called: بله' in pblock
    assert 'new_send_called: بله' in pblock

    # ---------------------------------------------- Away: پیام عدم حضور
    away_service.set_enabled(UID, True)
    away_service.set_text(UID, 'الان نیستم 🔥')
    run(client.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=CHAT_A, msg_id=10)))

    assert len(away_blocks) == 1
    ablock = away_blocks[0]
    assert ablock.startswith('[AWAY_TRACE]\n\n')
    # premium_entered=False / resend_entered=False — هم در بلوک Trace
    assert 'premium_pipeline_entered: خیر — هرگز' in ablock
    assert 'resend_entered: خیر — هرگز' in ablock
    # هم در record runtime (حالت بولی واقعی، نه فقط متن)
    record = next(iter(away_bypass._TRACES.values()))
    assert record['premium_pipeline_entered'] is False
    assert record['resend_entered'] is False
    # پیام عدم حضور خام ارسال شد — بدون هیچ Entity (گارد کانورتر)
    away_sends = [s for s in client.sends if s[1] == 'الان نیستم 🔥']
    assert away_sends and not away_sends[0][3]

    # رویداد outgoing پیام Away هم به Resend می‌رسد — و بدون اقدام رد می‌شود
    away_sent = make_sent_message(client, CHAT_A, 'الان نیستم 🔥')
    run(client.handler_of('_outgoing_premium_fix')(outgoing_event(away_sent)))
    assert record['resend_entered'] is False
    assert 'بدون هیچ اقدامی رد کرد' in (record['resend_note'] or '')
    # از سمت Premium هم ثبت شد: is_away=بله، بدون Delete و بدون New Send
    assert len(premium_blocks) == 2
    assert 'is_away: بله' in premium_blocks[1]
    assert 'delete_called: خیر' in premium_blocks[1]
    assert 'new_send_called: خیر' in premium_blocks[1]
    # هیچ حذف/ارسال جدیدی برای پیام Away رخ نداد
    assert client.deleted == [701]                     # فقط حذفِ Premium
    assert len(away_sends) == 1                        # ارسال جدیدی نیست
    # edit در کل سناریو (Premium + Away) صفر ماند
    assert edit_calls == []
