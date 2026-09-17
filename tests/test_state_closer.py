"""Acceptance tests — دستور .بستن (بستن عملیات‌های در انتظار) v0.09.13.

سناریوهای الزامی spec مالک، همه آفلاین و بدون شبکه:
    1) پنل تنظیمات (پیام‌های via_bot)     2) wizard ایموجی
    3) ورودی متن (تغییر متن Away)          4) حالت انتظار فایل/مقصد
    5) انتخاب صدا TTS                       6) تسک‌های نیمه‌کاره
    7) تأیید عملیات پنل ادمین               8) حالت خالی (هیچ چیزی برای بستن)

پس از اجرا هیچ state باقی نمی‌ماند و بلوک [STATE] صادر می‌شود.
"""
import asyncio
from types import SimpleNamespace as NS

import pytest

import db
import handlers.admin
from handlers import save_message
from services import away as away_service
from services import copy_protected
from services import state_closer
from services import telegram_logger as tlog
from database import models as tabchi_models

UID = 8359698350
CHAT_ID = -100555


class CloseClient:
    """کلاینت تقلبی با رابط‌های موردنیاز state_closer."""

    def __init__(self, panel_ids=()):
        self.deleted = []
        self._tts_pending = {}
        self._panel_ids = list(panel_ids)

    async def get_entity(self, username):
        return NS(id=99, username=username)

    async def iter_messages(self, chat_id, limit=None):
        for mid in self._panel_ids:
            yield NS(id=mid, out=True, via_bot_id=99)

    async def delete_messages(self, chat_id, ids, **kwargs):
        if not isinstance(ids, (list, tuple)):
            ids = [ids]
        self.deleted.append((chat_id, list(ids)))


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def clean_environment(monkeypatch):
    monkeypatch.setattr(tlog, 'log_token', lambda: '')
    handlers.admin._active_controllers.clear()
    yield
    handlers.admin._active_controllers.clear()


def test_close_everything_pending(clean_environment):
    tabchi_models.init_custom_emojis_db()
    client = CloseClient(panel_ids=[11, 12])
    client._tts_pending[CHAT_ID] = {'selector_message_id': 30, 'text': 'سلام'}
    client._tts_pending[123] = {'selector_message_id': None}

    # state های فعال
    tabchi_models.set_custom_emoji_flow(UID, 'self', 'extract')
    save_message.begin_destination_capture(UID)
    copy_protected.begin_destination_capture(UID, payload={'link': 'x'})
    away_service.begin_text_capture(UID)
    db.save_admin_state(UID, {'mode': 'confirm'})
    controller = NS(states={UID: {'mode': 'confirm'}})
    handlers.admin._active_controllers.append(controller)
    spam_task_ref = {}

    async def long_task():
        await asyncio.sleep(60)

    cleanup_tasks = {}

    async def scenario():
        task = asyncio.ensure_future(long_task())
        spam_task_ref['task'] = task
        spam_tasks = {(UID, CHAT_ID): task}
        return await state_closer.close_all_pending(
            client, UID, CHAT_ID, spam_tasks=spam_tasks,
            cleanup_tasks=cleanup_tasks)

    result = run(scenario())

    # همه چیز بسته شده باشد
    assert 'wizard' in result['closed']
    assert 'waiting_input' in result['closed']
    assert 'tts_voice_selector' in result['closed']
    assert 'running_task' in result['closed']
    assert 'confirmation' in result['closed']
    assert 'panel' in result['closed']
    assert 'admin_state' in result['old_state']
    assert 'extract' in result['old_state']  # wizard step پیش از پاک‌شدن

    # هیچ state باقی نماند
    assert tabchi_models.get_custom_emoji_flow(UID, 'self') is None
    assert not save_message.has_destination_capture(UID)
    assert not copy_protected.has_destination_capture(UID)
    assert not away_service.has_text_capture(UID)
    assert client._tts_pending == {}
    task_ref = spam_task_ref.get('task')
    assert task_ref is None or task_ref.done() or task_ref.cancelled()
    assert db.load_admin_states().get(UID) is None
    assert UID not in controller.states

    # پنل‌ها حذف شدند (پیام‌های via_bot) و پیام انتخابگر TTS هم
    deleted_ids = [mid for _, mids in client.deleted for mid in mids]
    assert 11 in deleted_ids and 12 in deleted_ids
    assert 30 in deleted_ids


def test_close_with_nothing_pending(clean_environment):
    client = CloseClient()
    result = run(state_closer.close_all_pending(client, UID, CHAT_ID))
    assert result['closed'] == []
    assert result['old_state'] == 'none'
    assert client.deleted == []


def test_state_log_block_format(clean_environment):
    client = CloseClient(panel_ids=[5])
    tabchi_models.init_custom_emojis_db()
    tabchi_models.set_custom_emoji_flow(UID, 'self', 'test')
    result = run(state_closer.close_all_pending(client, UID, CHAT_ID))
    block = state_closer.build_state_block(CHAT_ID, result)
    lines = block.split('\n')
    assert lines[0] == '[STATE]'
    assert f'chat: {CHAT_ID}' in lines
    assert 'old_state: test, panel@1' in lines
    assert 'closed: wizard, panel' in lines
    assert 'closed: wizard,panel' not in lines


def test_close_survives_inline_bot_unavailable(clean_environment, monkeypatch):
    """حتی اگر ربات اینلاین در دسترس نباشد، بقیه state ها بسته می‌شوند."""
    client = CloseClient()

    async def no_entity(username):
        raise RuntimeError('bot offline')

    client.get_entity = no_entity
    tabchi_models.init_custom_emojis_db()
    tabchi_models.set_custom_emoji_flow(UID, 'self', 'extract')
    result = run(state_closer.close_all_pending(client, UID, CHAT_ID))
    assert 'wizard' in result['closed']
    assert tabchi_models.get_custom_emoji_flow(UID, 'self') is None


def test_close_cancels_spam_and_cleanup_tasks(clean_environment):
    client = CloseClient()
    stores = {'spam': {}, 'cleanup': {}}

    async def long_task():
        await asyncio.sleep(60)

    async def scenario():
        stores['spam'][(UID, -1)] = asyncio.ensure_future(long_task())
        stores['cleanup'][(UID, -2)] = asyncio.ensure_future(long_task())
        await state_closer.close_all_pending(client, UID, CHAT_ID,
                                             spam_tasks=stores['spam'],
                                             cleanup_tasks=stores['cleanup'])

    run(scenario())
    assert stores['spam'] == {} and stores['cleanup'] == {}
