"""Acceptance tests — پیام عدم حضور (Away) v0.09.14 — منطق per-chat.

سناریوهای الزامی spec مالک، همه آفلاین و بدون شبکه:
    1) Away روشن: Chat A «سلام» → یک پیام؛ Chat A «خوبی؟» → هیچ؛
       Chat B «سلام» → یک پیام (مدیریت جدا per-chat)
    2) خاموش/روشن کردن Away → لیست ریست شود
    3) شروع Session جدید واقعی → لیست ریست شود
    4) چند پیام پشت سر هم → فقط یک پاسخ
    5) هیچ پیام خروجی / event outgoing لیست را ریست نمی‌کند (بدون loop)
    6) فقط چت خصوصی؛ گروه/کانال/بات هرگز
    7) تغییر متن (.متن_عدم_حضور / capture پنل)
    8) لاگ فارسی [پیام عدم حضور]
"""
import asyncio
from types import SimpleNamespace as NS

import pytest
from telethon import events

import db
from services import away as away_service
from services import telegram_logger as tlog

UID = 8359698350
CHAT_A = 1001
CHAT_B = 1002


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


def make_event(*, sender_id=CHAT_A, chat_id=None, private=True,
               text='سلام', msg_id=1, bot=False, muted=False,
               action=False):
    chat_id = sender_id if chat_id is None else chat_id  # چت خصوصی ۱:۱
    sender = NS(bot=bot, is_self=False, id=sender_id)
    message = NS(action=NS() if action else None, sender_id=sender_id,
                 id=msg_id, chat_id=chat_id, message=text)
    return NS(message=message, chat_id=chat_id, is_private=private,
              raw_text=text, get_sender=None, _sender=sender)


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
    db.update_user_settings(UID, {'away_enabled': False,
                                  'away_text': db.DEFAULT_AWAY_TEXT,
                                  'away_sent_chats': {},
                                  'away_active_session': ''})
    client = AwayClient()
    away_service.register_away_handlers(client, UID)
    return client


# ================================================== 1) ماتریس اجباری per-chat
def test_matrix_chat_a_once_then_nothing_chat_b_once(away_setup):
    """سناریوی اجباری ۱: A یک بار، پیام دوم A هیچ، B یک بار."""
    away_service.set_enabled(UID, True)
    handler = away_setup.handler_of('_away_incoming_handler')
    # Chat A: سلام → یک پیام عدم حضور
    run(handler(incoming_event(sender_id=CHAT_A, msg_id=1)))
    assert len(away_setup.sent) == 1
    chat_id, text, kwargs = away_setup.sent[0]
    assert chat_id == CHAT_A
    assert text == db.DEFAULT_AWAY_TEXT       # متن پیش‌فرض
    assert kwargs.get('parse_mode') is None   # متن ساده؛ Pipeline روی آن فعال است
    # Chat A: خوبی؟ → هیچ پیام دیگری ارسال نشود
    run(handler(incoming_event(sender_id=CHAT_A, msg_id=2, text='خوبی؟')))
    assert len(away_setup.sent) == 1
    # Chat B: سلام → یک پیام عدم حضور (مدیریت جدا per-chat)
    run(handler(incoming_event(sender_id=CHAT_B, msg_id=3)))
    assert len(away_setup.sent) == 2
    assert away_setup.sent[1][0] == CHAT_B
    # ساختار دیتابیس درخواستی مالک
    assert str(CHAT_A) in db.get_user_settings(UID)['away_sent_chats']
    assert str(CHAT_B) in db.get_user_settings(UID)['away_sent_chats']


def test_disabled_sends_nothing(away_setup):
    away_service.set_enabled(UID, False)
    run(away_setup.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=CHAT_A)))
    assert away_setup.sent == []


# ================================================== 2) چند پیام پشت سر هم
def test_multiple_messages_in_a_row_get_one_response(away_setup):
    """سناریوی اجباری ۴: چند پیام پشت سر هم → فقط یک پاسخ."""
    away_service.set_enabled(UID, True)
    handler = away_setup.handler_of('_away_incoming_handler')
    for i in range(1, 6):  # پنج پیام پشت سر هم از یک چت
        run(handler(incoming_event(sender_id=CHAT_A, msg_id=i)))
    assert len(away_setup.sent) == 1          # فقط بار اول


def test_multiple_chats_each_get_exactly_one(away_setup):
    away_service.set_enabled(UID, True)
    handler = away_setup.handler_of('_away_incoming_handler')
    for i in range(3):
        run(handler(incoming_event(sender_id=CHAT_A, msg_id=10 + i)))
        run(handler(incoming_event(sender_id=CHAT_B, msg_id=20 + i)))
    assert len(away_setup.sent) == 2
    assert {chat_id for chat_id, _, _ in away_setup.sent} == {CHAT_A, CHAT_B}


# ================================================== 3) خاموش/روشن = ریست
def test_toggle_off_then_on_resets_list(away_setup):
    """سناریوی اجباری ۲: خاموش/روشن کردن Away لیست را ریست می‌کند."""
    away_service.set_enabled(UID, True)
    handler = away_setup.handler_of('_away_incoming_handler')
    run(handler(incoming_event(sender_id=CHAT_A, msg_id=1)))
    assert len(away_setup.sent) == 1
    # خاموش → لیست پاک می‌شود
    away_service.set_enabled(UID, False)
    assert db.get_user_settings(UID)['away_sent_chats'] == {}
    # روشن دوباره → دوره تازه؛ همان چت دوباره یک پیام می‌گیرد
    away_service.set_enabled(UID, True)
    run(handler(incoming_event(sender_id=CHAT_A, msg_id=2)))
    assert len(away_setup.sent) == 2


def test_disabled_after_sent_sends_nothing_more(away_setup):
    away_service.set_enabled(UID, True)
    handler = away_setup.handler_of('_away_incoming_handler')
    run(handler(incoming_event(sender_id=CHAT_A, msg_id=1)))
    assert len(away_setup.sent) == 1
    away_service.set_enabled(UID, False)
    run(handler(incoming_event(sender_id=CHAT_B, msg_id=2)))
    assert len(away_setup.sent) == 1          # خاموش → چیزی برای B نرفت


# ================================================== 4) سشن جدید = ریست
def test_new_session_resets_list(away_setup):
    """سناریوی اجباری ریست: Session جدید واقعی → لیست پاک می‌شود."""
    away_service.set_enabled(UID, True)
    handler = away_setup.handler_of('_away_incoming_handler')
    run(handler(incoming_event(sender_id=CHAT_A, msg_id=1)))
    assert len(away_setup.sent) == 1
    old_token = db.get_user_settings(UID)['away_active_session']
    cleared = away_service.start_session(UID)  # restart سلف
    assert cleared == 1
    new_token = db.get_user_settings(UID)['away_active_session']
    assert new_token and new_token != old_token
    assert db.get_user_settings(UID)['away_sent_chats'] == {}
    run(handler(incoming_event(sender_id=CHAT_A, msg_id=2)))
    assert len(away_setup.sent) == 2          # دوره تازه شروع شد


def test_register_is_new_session(away_setup):
    """هر نصب هندلر (restart واقعی سلف) = سشن جدید → لیست خالی."""
    away_service.set_enabled(UID, True)
    db.update_user_settings(UID, {'away_sent_chats': {'999': 123.0}})
    client2 = AwayClient()
    away_service.register_away_handlers(client2, UID)
    assert db.get_user_settings(UID)['away_sent_chats'] == {}


# ================================================== 5) بدون loop/بدون ریست خروجی
def test_handlers_registered_once(away_setup):
    away_service.register_away_handlers(away_setup, UID)
    names = [fn.__name__ for fn in away_setup.handlers]
    assert names.count('_away_incoming_handler') == 1
    assert names.count('_away_text_input_handler') == 1
    # ❌ هیچ هندلر outgoing ریست وجود ندارد — ریست با پیام خروجی ممنوع است
    assert names.count('_away_outgoing_handler') == 0


def test_no_outgoing_reset_handler_means_no_loop(away_setup):
    """پیام خروجی مالک هیچ اثری روی لیست ندارد (رفع رفتار معیوب v0.09.13)."""
    away_service.set_enabled(UID, True)
    handler = away_setup.handler_of('_away_incoming_handler')
    run(handler(incoming_event(sender_id=CHAT_A, msg_id=1)))
    assert str(CHAT_A) in db.get_user_settings(UID)['away_sent_chats']
    # مالک پیام می‌فرستد — چون هندلر outgoing وجود ندارد، لیست دست‌نخورده است
    names = [fn.__name__ for fn in away_setup.handlers]
    assert '_away_outgoing_handler' not in names
    assert str(CHAT_A) in db.get_user_settings(UID)['away_sent_chats']
    # پس پیام دوم همان چت همچنان هیچ پاسخی نمی‌گیرد (بدون اسپم)
    run(handler(incoming_event(sender_id=CHAT_A, msg_id=2)))
    assert len(away_setup.sent) == 1


def test_manual_reset(away_setup):
    away_service.set_enabled(UID, True)
    handler = away_setup.handler_of('_away_incoming_handler')
    run(handler(incoming_event(sender_id=CHAT_A, msg_id=1)))
    count = away_service.reset_sent_chats(UID)
    assert count == 1
    assert db.get_user_settings(UID)['away_sent_chats'] == {}
    # نام سازگاری قدیمی هم کار می‌کند
    assert away_service.reset_sent_users(UID) == 0


# ================================================== 6) فقط خصوصی/بدون بات
def test_group_and_channel_never_get_away(away_setup):
    away_service.set_enabled(UID, True)
    run(away_setup.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=CHAT_A, chat_id=-777, private=False)))
    assert away_setup.sent == []


def test_bot_senders_are_ignored(away_setup):
    away_service.set_enabled(UID, True)
    run(away_setup.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=700, bot=True)))
    assert away_setup.sent == []


def test_service_messages_are_ignored(away_setup):
    away_service.set_enabled(UID, True)
    run(away_setup.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=CHAT_A, action=True)))
    assert away_setup.sent == []


def test_muted_chat_never_gets_away(away_setup):
    away_service.set_enabled(UID, True)
    db.update_user_settings(UID, {'muted_chats': [CHAT_A]})
    run(away_setup.handler_of('_away_incoming_handler')(
        incoming_event(sender_id=CHAT_A)))
    assert away_setup.sent == []
    db.update_user_settings(UID, {'muted_chats': []})


# ================================================== 7) متن
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
        incoming_event(sender_id=CHAT_A)))
    assert away_setup.sent[0][1] == 'الان نیستم 🔥'


# ================================================== 8) capture متن از پنل
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
    event = outgoing_event(sender_id=UID, chat_id=UID, text='.عدم_حضور خاموش')
    run(away_setup.handler_of('_away_text_input_handler')(event))
    # دستور: capture باطل ولی متن ذخیره نشده
    assert not away_service.has_text_capture(UID)
    assert db.get_user_settings(UID)['away_text'] == db.DEFAULT_AWAY_TEXT
    assert away_setup.sent == []


def test_panel_text_capture_cancel(away_setup):
    away_service.begin_text_capture(UID)
    away_service.cancel_text_capture(UID)
    assert not away_service.has_text_capture(UID)


# ================================================== 9) لاگ فارسی
def test_away_log_block_format():
    block = tlog.format_away_debug(chat_id=CHAT_A, status='روشن',
                                   result='ارسال شد')
    assert block.split('\n') == ['[پیام عدم حضور]', f'شناسه چت: {CHAT_A}',
                                 'وضعیت: روشن', 'نتیجه: ارسال شد']


def test_away_log_block_already_sent_format():
    block = tlog.format_away_debug(chat_id=CHAT_B, status='روشن',
                                   result='قبلاً ارسال شده')
    lines = block.split('\n')
    assert lines[0] == '[پیام عدم حضور]'
    assert lines[-1] == 'نتیجه: قبلاً ارسال شده'
