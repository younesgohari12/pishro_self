"""Acceptance tests — Away Message (پیام خودکار آفلاین) v0.09.13.

سناریوهای الزامی spec مالک، همه آفلاین و بدون شبکه:
    1) پیام اول کاربر → Away ارسال می‌شود
    2) پیام دوم همان کاربر → چیزی ارسال نمی‌شود
    3) چند کاربر → هر کدام یک بار
    4) خاموش/روشن (.away off/on)
    5) فقط چت خصوصی؛ گروه/کانال/بات هرگز
    6) ریست با فعالیت مالک (برگشت آنلاین) و ریست دستی
    7) تغییر متن با .away text و از پنل (capture)
    8) TTL اختیاری
"""
import asyncio
from types import SimpleNamespace as NS

import pytest
from telethon import events

import config
import db
from services import away as away_service
from services import telegram_logger as tlog

UID = 8359698350
USER_A = 1001
USER_B = 1002


def run(coro):
    return asyncio.run(coro)


class AwayClient:
    """کلاینت تقلبی با ثبت هندلرها و ارسال قابل‌بازرسی."""

    def __init__(self):
        self.handlers = []
        self.sent = []      # (chat_id, text, kwargs)

    def on(self, *args, **kwargs):
        def decorator(fn):
            self.handlers.append(fn)
            return fn
        return decorator

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))
        return NS(id=500 + len(self.sent), chat_id=chat_id)

    def handler_of(self, name):
        for fn in self.handlers:
            if fn.__name__ == name:
                return fn
        raise AssertionError(f'handler {name} not registered')


def make_event(*, sender_id=USER_A, chat_id=None, private=True,
               text='سلام', msg_id=1, bot=False, muted=False,
               action=False):
    chat_id = sender_id if chat_id is None else chat_id  # چت خصوصی ۱:۱
    sender = NS(bot=bot, is_self=False, id=sender_id)
    message = NS(action=NS() if action else None, sender_id=sender_id,
                 id=msg_id, chat_id=chat_id, message=text)
    return NS(message=message, chat_id=chat_id, is_private=private,
              raw_text=text, get_sender=None, _sender=sender)


async def _get_sender(self=None):
    pass


def incoming_event(**kwargs):
    event = make_event(**kwargs)
    sender = event._sender
    async def get_sender():
        return sender
    event.get_sender = get_sender
    return event


def outgoing_event(**kwargs):
    return make_event(**kwargs)


@pytest.fixture()
def away_setup(monkeypatch):
    monkeypatch.setattr(tlog, 'log_token', lambda: '')
    monkeypatch.setattr(config, 'AWAY_RESET_HOURS', 0)
    db.update_user_settings(UID, {'away_enabled': False,
                                  'away_text': db.DEFAULT_AWAY_TEXT,
                                  'away_sent_users': {}})
    client = AwayClient()
    away_service.register_away_handlers(client, UID)
    return client


# ================================================== 1) پیام اول / دوم
def test_first_message_gets_away_reply(away_setup):
    away_service.set_enabled(UID, True)
    incoming = incoming_event(sender_id=USER_A)
    run(away_setup.handler_of('_away_incoming_handler')(incoming))
    assert len(away_setup.sent) == 1
    chat_id, text, kwargs = away_setup.sent[0]
    assert chat_id == USER_A
    assert text == db.DEFAULT_AWAY_TEXT       # متن پیش‌فرض
    assert kwargs.get('parse_mode') is None   # متن ساده؛ Pipeline روی آن فعال است
    # ثبت شده تا پیام دوم چیزی نگیرد
    assert str(USER_A) in db.get_user_settings(UID)['away_sent_users']


def test_second_message_same_user_gets_nothing(away_setup):
    away_service.set_enabled(UID, True)
    run(away_setup.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=USER_A, msg_id=1)))
    run(away_setup.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=USER_A, msg_id=2)))
    run(away_setup.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=USER_A, msg_id=3)))
    assert len(away_setup.sent) == 1          # فقط بار اول


def test_multiple_users_each_get_one_message(away_setup):
    away_service.set_enabled(UID, True)
    run(away_setup.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=USER_A, msg_id=1)))
    run(away_setup.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=USER_B, msg_id=2)))
    run(away_setup.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=USER_A, msg_id=3)))
    run(away_setup.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=USER_B, msg_id=4)))
    assert len(away_setup.sent) == 2
    assert {chat_id for chat_id, _, _ in away_setup.sent} == {USER_A, USER_B}


# ================================================== 2) خاموش/روشن
def test_disabled_sends_nothing(away_setup):
    away_service.set_enabled(UID, False)
    run(away_setup.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=USER_A)))
    assert away_setup.sent == []


def test_enable_then_disable(away_setup):
    away_service.set_enabled(UID, True)
    run(away_setup.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=USER_A)))
    assert len(away_setup.sent) == 1
    away_service.set_enabled(UID, False)
    run(away_setup.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=USER_B)))
    assert len(away_setup.sent) == 1          # خاموش → چیزی برای B نرفت


# ================================================== 3) فقط خصوصی/بدون بات
def test_group_and_channel_never_get_away(away_setup):
    away_service.set_enabled(UID, True)
    run(away_setup.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=USER_A, chat_id=-777, private=False)))
    assert away_setup.sent == []


def test_bot_senders_are_ignored(away_setup):
    away_service.set_enabled(UID, True)
    run(away_setup.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=700, bot=True)))
    assert away_setup.sent == []


def test_service_messages_are_ignored(away_setup):
    away_service.set_enabled(UID, True)
    run(away_setup.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=USER_A, action=True)))
    assert away_setup.sent == []


def test_muted_chat_never_gets_away(away_setup):
    away_service.set_enabled(UID, True)
    db.update_user_settings(UID, {'muted_chats': [USER_A]})
    run(away_setup.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=USER_A)))
    assert away_setup.sent == []
    db.update_user_settings(UID, {'muted_chats': []})


# ================================================== 4) ریست
def test_owner_outgoing_activity_resets_list(away_setup):
    away_service.set_enabled(UID, True)
    run(away_setup.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=USER_A)))
    assert len(away_setup.sent) == 1
    # مالک پیامی می‌فرستد (برگشت آنلاین) → لیست ریست می‌شود
    run(away_setup.handler_of('_away_outgoing_handler')(
        outgoing_event(sender_id=UID, chat_id=USER_A, msg_id=900)))
    assert db.get_user_settings(UID)['away_sent_users'] == {}
    # حالا همان کاربر دوباره پیام بدهد → یک پیام جدید می‌گیرد
    run(away_setup.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=USER_A, msg_id=2)))
    assert len(away_setup.sent) == 2


def test_away_reply_itself_never_resets_list(away_setup):
    away_service.set_enabled(UID, True)
    run(away_setup.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=USER_A, msg_id=1)))
    reply_id = 500 + len(away_setup.sent)     # پیام Away ارسال‌شده
    run(away_setup.handler_of('_away_outgoing_handler')(
        outgoing_event(sender_id=UID, chat_id=USER_A, msg_id=reply_id)))
    # پاسخ Away نشانه آنلاین بودن نیست → لیست دست‌نخورده
    assert str(USER_A) in db.get_user_settings(UID)['away_sent_users']
    run(away_setup.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=USER_A, msg_id=2)))
    assert len(away_setup.sent) == 1          # پاسخ دوباره نرفت


def test_manual_reset(away_setup):
    away_service.set_enabled(UID, True)
    run(away_setup.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=USER_A)))
    count = away_service.reset_sent_users(UID)
    assert count == 1
    assert db.get_user_settings(UID)['away_sent_users'] == {}


def test_reset_without_list_writes_nothing_but_is_safe(away_setup):
    assert away_service.reset_sent_users(UID) == 0


# ================================================== 5) متن
def test_set_text_validation(away_setup):
    saved = away_service.set_text(UID, '  برگشت ساعت ۱۰  ')
    assert saved == 'برگشت ساعت ۱۰'
    assert db.get_user_settings(UID)['away_text'] == 'برگشت ساعت ۱۰'
    with pytest.raises(ValueError):
        away_service.set_text(UID, '   ')
    with pytest.raises(ValueError):
        away_service.set_text(UID, 'x' * 501)


def test_custom_text_is_what_gets_sent(away_setup):
    away_service.set_enabled(UID, True)
    away_service.set_text(UID, 'الان نیستم 🔥')
    run(away_setup.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=USER_A)))
    assert away_setup.sent[0][1] == 'الان نیستم 🔥'


# ================================================== 6) capture متن از پنل
def test_panel_text_capture_flow(away_setup):
    away_service.begin_text_capture(UID)
    assert away_service.has_text_capture(UID)
    # کاربر متن جدید می‌فرستد (outgoing) → StopPropagation مثل اجرای واقعی
    event = outgoing_event(sender_id=UID, chat_id=UID, text='متن جدید از پنل')
    with pytest.raises(events.StopPropagation):
        run(away_setup.handler_of('_away_text_input_handler')(event))
    assert db.get_user_settings(UID)['away_text'] == 'متن جدید از پنل'
    assert not away_service.has_text_capture(UID)
    # پیام تأیید ارسال شده
    assert any('متن جدید از پنل' in text for _, text, _ in away_setup.sent)


def test_panel_text_capture_ignores_commands(away_setup):
    away_service.begin_text_capture(UID)
    event = outgoing_event(sender_id=UID, chat_id=UID, text='.away off')
    run(away_setup.handler_of('_away_text_input_handler')(event))
    # دستور: capture باطل ولی متن ذخیره نشده
    assert not away_service.has_text_capture(UID)
    assert db.get_user_settings(UID)['away_text'] == db.DEFAULT_AWAY_TEXT
    assert away_setup.sent == []


def test_panel_text_capture_cancel(away_setup):
    away_service.begin_text_capture(UID)
    away_service.cancel_text_capture(UID)
    assert not away_service.has_text_capture(UID)


# ================================================== 7) TTL
def test_ttl_expiry_allows_second_message(away_setup, monkeypatch):
    monkeypatch.setattr(config, 'AWAY_RESET_HOURS', 1)
    away_service.set_enabled(UID, True)
    run(away_setup.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=USER_A, msg_id=1)))
    assert len(away_setup.sent) == 1
    # تازگی کمتر از TTL → هنوز ارسال نمی‌شود
    run(away_setup.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=USER_A, msg_id=2)))
    assert len(away_setup.sent) == 1
    # پیام قدیمی‌تر از TTL → دوباره ارسال می‌شود
    settings = db.get_user_settings(UID)
    settings['away_sent_users'][str(USER_A)] -= 7200
    db.update_user_settings(UID, {'away_sent_users':
                                  settings['away_sent_users']})
    run(away_setup.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=USER_A, msg_id=3)))
    assert len(away_setup.sent) == 2


# ================================================== 8) نصب و لاگ
def test_handlers_registered_once(away_setup):
    away_service.register_away_handlers(away_setup, UID)
    names = [fn.__name__ for fn in away_setup.handlers]
    assert names.count('_away_incoming_handler') == 1
    assert names.count('_away_outgoing_handler') == 1
    assert names.count('_away_text_input_handler') == 1


def test_away_log_block_format():
    block = tlog.format_away_debug(user=USER_A, sent=True,
                                   reason='first_message')
    assert block.split('\n') == ['[AWAY]', f'user: {USER_A}',
                                 'sent: True', 'reason: first_message']
