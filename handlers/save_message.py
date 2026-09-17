from __future__ import annotations

import threading
import time

import ui
from database import models

_PENDING_LOCK = threading.RLock()
_PENDING_DESTINATION: dict[int, float] = {}
_PENDING_TTL_SECONDS = 300


def begin_destination_capture(user_id: int) -> None:
    with _PENDING_LOCK:
        _PENDING_DESTINATION[int(user_id)] = time.time() + _PENDING_TTL_SECONDS


def cancel_destination_capture(user_id: int) -> None:
    with _PENDING_LOCK:
        _PENDING_DESTINATION.pop(int(user_id), None)


def consume_destination_capture(user_id: int) -> bool:
    uid = int(user_id)
    now = time.time()
    with _PENDING_LOCK:
        expires_at = _PENDING_DESTINATION.get(uid)
        if not expires_at:
            return False
        if expires_at < now:
            _PENDING_DESTINATION.pop(uid, None)
            return False
        _PENDING_DESTINATION.pop(uid, None)
        return True


def has_destination_capture(user_id: int) -> bool:
    uid = int(user_id)
    now = time.time()
    with _PENDING_LOCK:
        expires_at = _PENDING_DESTINATION.get(uid)
        if not expires_at:
            return False
        if expires_at < now:
            _PENDING_DESTINATION.pop(uid, None)
            return False
        return True


def render_save_menu(user_id: int):
    settings = models.get_save_settings(user_id)
    enabled = bool(settings.get('status'))
    destination = settings.get('destination') or 'تعیین نشده'
    status_text = '🟢 فعال' if enabled else '🔴 غیر فعال'

    text = (
        '💾 **مدیریت سیو پیام**\n\n'
        f'وضعیت: **{status_text}**\n'
        f'📍 مقصد: `{destination}`\n\n'
        'هنگام فعال بودن، پیام‌هایی که این حساب دریافت کرده در کش محلی ثبت می‌شوند و اگر حذف شوند، '
        'هدر اطلاعات + محتوای اصلی به مقصد ذخیره ارسال می‌شود.'
    )
    buttons = [
        [ui.inline_button('✅ فعال کردن', b'save_enable', 'success')],
        [ui.inline_button('❌ خاموش کردن', b'save_disable', 'danger')],
        [ui.inline_button('📍 تعیین محل ذخیره', b'save_destination', 'primary')],
        [ui.inline_button('📊 آمار پیام‌های ذخیره شده', b'save_stats', 'primary')],
        [ui.inline_button('↩️ بازگشت', b'back_main', 'secondary')],
    ]
    return text, buttons


def render_destination_prompt():
    text = (
        '📍 **تعیین محل ذخیره**\n\n'
        'آیدی عددی، یوزرنیم، گروه یا کانال مقصد را همین حالا به صورت یک پیام ارسال کنید.\n\n'
        'مثال:\n'
        '`123456789`\n'
        '`-1001234567890`\n'
        '`@mychannel`\n'
        '`@mygroup`\n\n'
        '⏳ این درخواست تا ۵ دقیقه معتبر است.'
    )
    buttons = [
        [ui.inline_button('❌ لغو تعیین مقصد', b'save_destination_cancel', 'danger')],
        [ui.inline_button('↩️ مدیریت سیو پیام', b'save_menu', 'secondary')],
    ]
    return text, buttons


def render_save_stats(user_id: int):
    stats = models.get_deleted_message_stats(user_id)
    last_saved = stats.get('last_saved_at') or '—'
    text = (
        '📊 **آمار پیام‌های ذخیره شده**\n\n'
        f"📦 کل پیام‌های حذف‌شده: **{stats.get('total', 0)}**\n"
        f"📝 متن: **{stats.get('text', 0)}**\n"
        f"🖼 عکس: **{stats.get('photo', 0)}**\n"
        f"🎬 ویدیو/GIF: **{stats.get('video', 0) + stats.get('gif', 0)}**\n"
        f"🎙 ویس/موزیک: **{stats.get('voice', 0) + stats.get('audio', 0)}**\n"
        f"📁 فایل/استیکر/سایر: **{stats.get('file', 0) + stats.get('sticker', 0) + stats.get('other', 0)}**\n"
        f"📍 لوکیشن/مخاطب: **{stats.get('location', 0) + stats.get('contact', 0)}**\n"
        f"🕒 آخرین ذخیره: `{last_saved}`"
    )
    buttons = [[ui.inline_button('↩️ مدیریت سیو پیام', b'save_menu', 'secondary')]]
    return text, buttons
