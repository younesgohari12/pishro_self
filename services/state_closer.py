"""State Closer — بستن همه عملیات‌های در انتظار با یک دستور (.بستن).

هر چیزی که در انتظار پاسخ یا عملیات نیمه‌کاره است، در همان لحظه بسته می‌شود:

- پنل اینلاین (پیام‌های via_bot پنل در همین چت حذف می‌شوند)
- Wizard ایموجی (استخراج/تست Custom Emoji)
- ورودی مقصد «سیو پیام» و ورودی مقصد «کپی محتوا»
- ورودی متن Away (تغییر متن از پنل)
- انتخاب صدا TTS (همه چت‌ها + حذف پیام انتخابگر)
- تسک‌های نیمه‌کاره اسپم/پاک‌سازی (همه چت‌ها)
- state پنل ادمین (تأیید عملیات‌ها) در حافظه و دیتابیس

بعد از اجرا هیچ state باقی نمی‌ماند و خروجی با بلوک [STATE] گزارش می‌شود:

    [STATE]
    closed: wizard, tts_voice_selector, panel_message
    chat: -1001234567890
    old_state: cem_extract, tts@2 chats, admin_state
"""
from __future__ import annotations

import asyncio

import config
from database import models as tabchi_models
from handlers import save_message
from handlers.admin import clear_pending_state as clear_admin_state
from services import away as away_service
from services import copy_protected
from services import telegram_logger as tlog

PANEL_SCAN_LIMIT = 20  # چند پیام آخر برای پیدا کردن پیام‌های پنل اسکن شود


async def _cancel_store_tasks(store) -> int:
    """لغو همه تسک‌های فعال یک store؛ تعداد لغو واقعی برمی‌گردد."""
    cancelled = 0
    for key, task in list((store or {}).items()):
        store.pop(key, None)
        if task is None or task.done():
            continue
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            cancelled += 1
        except Exception:  # noqa: BLE001 - لغو نباید بقیه را متوقف کند
            cancelled += 1
    return cancelled


async def _inline_bot_id(client):
    """شناسه ربات اینلاین (کش روی کلاینت) تا فقط پیام‌های پنل حذف شوند."""
    cached = getattr(client, '_inline_bot_id_cached', None)
    if cached:
        return cached
    username = getattr(config, 'INLINE_USERNAME', '') or ''
    if not username:
        return None
    try:
        entity = await client.get_entity(username.lstrip('@'))
        bot_id = getattr(entity, 'id', None)
        if bot_id:
            client._inline_bot_id_cached = bot_id
        return bot_id
    except Exception:  # noqa: BLE001 - نبودن ربات هرگز بستن را نمی‌شکند
        return None


async def _close_panel_messages(client, chat_id) -> tuple[int, list[int]]:
    """حذف پیام‌های پنل اینلاین (via_bot) در این چت؛ (تعداد, شناسه‌ها)."""
    bot_id = await _inline_bot_id(client)
    if bot_id is None:
        return 0, []
    panel_ids = []
    try:
        async for message in client.iter_messages(chat_id, limit=PANEL_SCAN_LIMIT):
            if (getattr(message, 'out', False)
                    and getattr(message, 'via_bot_id', None) == bot_id):
                panel_ids.append(message.id)
    except Exception:  # noqa: BLE001 - اسکن نباید بستن state را بشکند
        return 0, []
    if panel_ids:
        try:
            await client.delete_messages(chat_id, panel_ids)
        except Exception:  # noqa: BLE001
            return 0, []
    return len(panel_ids), panel_ids


async def close_all_pending(client, uid, chat_id, *,
                            spam_tasks=None, cleanup_tasks=None) -> dict:
    """بستن همه عملیات‌های در انتظار این حساب؛ بدون هیچ state باقی‌مانده.

    خروجی: {'closed': [...], 'old_state': '...'} برای بلوک [STATE] و پاسخ.
    """
    uid = int(uid)
    closed: list[str] = []
    old_state: list[str] = []

    # 1) Wizard ایموجی (استخراج/تست)
    try:
        flow = tabchi_models.get_custom_emoji_flow(uid, 'self')
        if flow:
            old_state.append(str(flow.get('step') or 'flow'))
            tabchi_models.clear_custom_emoji_flow(uid, 'self')
            closed.append('wizard')
    except Exception:  # noqa: BLE001
        pass

    # 2) ورودی مقصد سیو پیام
    try:
        if save_message.has_destination_capture(uid):
            save_message.cancel_destination_capture(uid)
            old_state.append('save_destination_input')
            closed.append('waiting_input')
    except Exception:  # noqa: BLE001
        pass

    # 3) ورودی مقصد کپی محتوا
    try:
        if copy_protected.has_destination_capture(uid):
            copy_protected.cancel_destination_capture(uid)
            old_state.append('copy_destination_input')
            closed.append('waiting_input')
    except Exception:  # noqa: BLE001
        pass

    # 4) ورودی متن Away (پنل)
    try:
        if away_service.has_text_capture(uid):
            away_service.cancel_text_capture(uid)
            old_state.append('away_text_input')
            closed.append('waiting_input')
    except Exception:  # noqa: BLE001
        pass

    # 5) TTS: انتظار انتخاب صدا در همه چت‌ها + حذف پیام انتخابگر
    try:
        pending = getattr(client, '_tts_pending', None)
        if isinstance(pending, dict) and pending:
            chats = list(pending.keys())
            for key, item in list(pending.items()):
                selector_id = (item or {}).get('selector_message_id')
                if selector_id:
                    try:
                        await client.delete_messages(key, selector_id)
                    except Exception:  # noqa: BLE001
                        pass
            pending.clear()
            old_state.append(f'tts@{len(chats)} chats')
            closed.append('tts_voice_selector')
    except Exception:  # noqa: BLE001
        pass

    # 6) تسک‌های نیمه‌کاره اسپم/پاک‌سازی (همه چت‌ها)
    try:
        spam_count = await _cancel_store_tasks(spam_tasks)
        if spam_count:
            old_state.append(f'spam@{spam_count}')
            closed.append('running_task')
    except Exception:  # noqa: BLE001
        pass
    try:
        cleanup_count = await _cancel_store_tasks(cleanup_tasks)
        if cleanup_count:
            old_state.append(f'cleanup@{cleanup_count}')
            closed.append('running_task')
    except Exception:  # noqa: BLE001
        pass

    # 7) state پنل ادمین (تأیید عملیات‌ها)
    try:
        if clear_admin_state(uid):
            old_state.append('admin_state')
            closed.append('confirmation')
    except Exception:  # noqa: BLE001
        pass

    # 8) پیام‌های پنل اینلاین در همین چت
    try:
        panel_count, _ = await _close_panel_messages(client, chat_id)
        if panel_count:
            old_state.append(f'panel@{panel_count}')
            closed.append('panel')
    except Exception:  # noqa: BLE001
        pass

    return {'closed': closed, 'old_state': ', '.join(old_state) or 'none'}


def build_state_block(chat_id, result) -> str:
    """بلوک استاندارد [STATE] از نتیجه close_all_pending."""
    return tlog.format_state_debug(
        closed=result.get('closed') or [],
        chat=chat_id,
        old_state=result.get('old_state') or 'none')


def log_state(chat_id, result, *, telegram=True) -> None:
    try:
        tlog.send_state_debug(build_state_block(chat_id, result),
                              telegram=telegram)
    except Exception:  # noqa: BLE001
        pass
