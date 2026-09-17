"""Acceptance tests — Presence Manager (مدیریت وضعیت آنلاین/آفلاین) v0.09.14.

سناریوهای اجباری spec مالک (همه آفلاین و بدون شبکه):
    3) ارسال پیام عدم حضور نباید باعث Online شدن اکانت شود:
       - SendMessageRequest عبور می‌کند ولی بعدش UpdateStatusRequest(offline=True)
         خودکار ارسال می‌شود (بازگردانی آفلاین)
       - SetTypingRequest هرگز ارسال نمی‌شود
       - UpdateStatusRequest(offline=False) هرگز ارسال نمی‌شود
       - ReadHistoryRequest هرگز ارسال نمی‌شود (read acknowledgement غیرضروری)
    1/4) پشتیبانی از منطق per-chat خود Away (بدون loop)
    + وقتی Away خاموش است wrapper شفاف است؛ get_messages آزاد عبور می‌کند.
"""
import asyncio
import time
from types import SimpleNamespace as NS

import pytest
from telethon.tl.functions.account import UpdateStatusRequest
from telethon.tl.functions.messages import (
    ReadHistoryRequest,
    SendMessageRequest,
    SetTypingRequest,
)
from telethon.tl.types import (
    InputPeerEmpty,
    SendMessageTypingAction,
)

from services import telegram_logger as tlog
from services.presence_manager import (
    classify_request,
    install_presence_manager,
    uninstall_presence_manager,
)

UID = 8359698350
PEER = InputPeerEmpty()
FAKE_SENDER = object()   # معادل client._sender در Telethon


def run(coro):
    return asyncio.run(coro)


class PresenceClient:
    """کلاینت تقلبی با ``_call`` — الگوی Telethon (self._call(sender, request))."""

    def __init__(self):
        self.network = []   # درخواست‌هایی که واقعاً به «شبکه» رفتند

    async def _call(self, sender, request, ordered=False,
                    flood_sleep_threshold=None):
        self.network.append(request)
        return NS(id=1)

    def reached(self, cls):
        return [r for r in self.network if isinstance(r, cls)]


def send_text_request(text='سلام'):
    return SendMessageRequest(peer=PEER, message=text, random_id=1)


def typing_request():
    return SetTypingRequest(peer=PEER, action=SendMessageTypingAction())


def read_request():
    return ReadHistoryRequest(peer=PEER, max_id=10)


@pytest.fixture()
def clean_db():
    from db import update_user_settings
    update_user_settings(UID, {'away_enabled': False})
    yield
    update_user_settings(UID, {'away_enabled': False})


def make_installed(active=True, offline_delay=0.05):
    """کلاینت + Presence Manager نصب‌شده با پرچم ثابت."""
    client = PresenceClient()
    install_presence_manager(client, UID, is_active=lambda: active,
                             offline_delay=offline_delay)
    return client


# ================================================== classification
def test_classify_request_kinds():
    assert classify_request(send_text_request()) == 'send'
    assert classify_request(typing_request()) == 'typing'
    assert classify_request(read_request()) == 'read'
    assert classify_request(UpdateStatusRequest(offline=False)) == 'status'
    assert classify_request(UpdateStatusRequest(offline=True)) == 'status'


# ================================================== ممنوعیت‌ها در حالت Away
def test_typing_blocked_while_away():
    client = make_installed(active=True)
    result = run(client._call(None, typing_request()))
    assert result is True                # نتیجه بی‌ضرر
    assert client.reached(SetTypingRequest) == []   # ❌ هرگز به شبکه نرفت


def test_status_online_blocked_while_away():
    client = make_installed(active=True)
    run(client._call(None, UpdateStatusRequest(offline=False)))
    assert client.reached(UpdateStatusRequest) == []  # ❌ آنلاین دستی ندارد
    # آفلاین کردن دستی مجاز است (همان هدف ماست)
    run(client._call(None, UpdateStatusRequest(offline=True)))
    assert len(client.reached(UpdateStatusRequest)) == 1


def test_read_acknowledge_blocked_while_away():
    client = make_installed(active=True)
    run(client._call(None, read_request()))
    assert client.reached(ReadHistoryRequest) == []  # ❌ read ack ارسال نشد


def test_get_messages_and_other_requests_pass_through():
    """get_messages و درخواست‌های فقط‌خواندنی وضعیت را تغییر نمی‌دهند و آزادند."""
    client = make_installed(active=True)
    run(client._call(None, NS()))  # هر شیء ناشناخته = عبور
    assert len(client.network) == 1


# ================================================== سناریوی اجباری ۳
def test_away_reply_send_does_not_stay_online():
    """ارسال پیام عدم حضور → عبور + بازگردانی خودکار آفلاین."""
    client = make_installed(active=True, offline_delay=0.05)

    async def scenario():
        result = await client._call(
            FAKE_SENDER, send_text_request('سلام، فعلاً آفلاین هستم.'))
        assert result is not None
        await asyncio.sleep(0.2)   # فرصت debounce برای بازگردانی آفلاین

    run(scenario())
    assert len(client.reached(SendMessageRequest)) == 1   # پیام ارسال شد
    offline_calls = [r for r in client.reached(UpdateStatusRequest)
                     if r.offline is True]
    assert len(offline_calls) == 1                        # ✅ بازگردانی آفلاین
    online_calls = [r for r in client.reached(UpdateStatusRequest)
                    if r.offline is False]
    assert online_calls == []                             # ❌ هیچ آنلاین‌سازی


def test_burst_of_sends_gets_single_offline_reassert():
    """چند ارسال پشت سر هم (آلبوم/اسپم) → فقط یک بازگردانی آفلاین (debounce)."""
    client = make_installed(active=True, offline_delay=0.05)

    async def burst():
        for i in range(4):
            await client._call(FAKE_SENDER, send_text_request(f'msg {i}'))
            await asyncio.sleep(0.02)   # فاصله کمتر از debounce
        await asyncio.sleep(0.2)

    run(burst())
    assert len(client.reached(SendMessageRequest)) == 4
    offline_calls = [r for r in client.reached(UpdateStatusRequest)
                     if r.offline is True]
    assert len(offline_calls) == 1      # یک‌بار در پایان موج، نه چهار بار


# ================================================== Away خاموش = شفاف
def test_transparent_when_away_off(clean_db):
    client = PresenceClient()
    install_presence_manager(client, UID,
                             is_active=lambda: False, offline_delay=0.02)
    run(client._call(None, typing_request()))
    run(client._call(None, UpdateStatusRequest(offline=False)))
    run(client._call(None, send_text_request()))
    run(asyncio.sleep(0.1))
    # هیچ چیز مسدود یا اضافه نشد — wrapper شفاف
    assert len(client.reached(SetTypingRequest)) == 1
    assert len(client.reached(UpdateStatusRequest)) == 1
    assert len(client.reached(SendMessageRequest)) == 1
    assert client.reached(UpdateStatusRequest)[0].offline is False  # دست‌نخورده


# ================================================== نصب/برداشتن
def test_uninstall_restores_original_behavior():
    client = make_installed(active=True)
    assert uninstall_presence_manager(client) is None
    run(client._call(None, typing_request()))
    assert len(client.reached(SetTypingRequest)) == 1   # بعد از برداشتن، عبور آزاد
    # نصب دوباره ممکن است
    assert install_presence_manager(client, UID,
                                    is_active=lambda: True) is True
    run(client._call(None, typing_request()))
    assert len(client.reached(SetTypingRequest)) == 1   # دوباره مسدود


def test_install_without_call_is_safe():
    class NoCall:
        pass
    assert install_presence_manager(NoCall(), UID) is False


# ================================================== لاگ فارسی
def test_presence_log_block_format():
    block = tlog.format_presence_debug(
        trigger='send_message', source='send_message',
        action='اجازه شد؛ بازگردانی آفلاین زمان‌بندی شد')
    assert block.split('\n') == [
        '[مدیریت وضعیت]',
        'Online Trigger: send_message',
        'منبع: send_message',
        'اقدام: اجازه شد؛ بازگردانی آفلاین زمان‌بندی شد',
    ]


def test_presence_log_blocked_format():
    block = tlog.format_presence_debug(
        trigger='typing', source='typing',
        action='مسدود شد — هیچ typing action ارسال نشد')
    lines = block.split('\n')
    assert lines[0] == '[مدیریت وضعیت]'
    assert lines[1] == 'Online Trigger: typing'
    assert lines[2] == 'منبع: typing'
    assert 'مسدود شد' in lines[3]
