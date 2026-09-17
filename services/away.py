"""پیام عدم حضور — پاسخ خودکار خصوصی هنگام آفلاین بودن مالک.

رفتار (مطابق spec مالک):
- فقط چت خصوصی (Private)؛ گروه/کانال هرگز. بات‌ها و پیام‌های سرویس هرگز.
- برای هر کاربر فقط «یک بار» پیام می‌رود تا زمانی که:
  * مالک دوباره آنلاین شود (هر پیام خروجی مالک = علامت برگشت) → لیست ریست، یا
  * ریست دستی (.away reset / پنل)، یا
  * سقف زمانی AWAY_RESET_HOURS (اختیاری؛ 0 = غیرفعال) گذشته باشد.
- متن و روشن/خاموش در دیتابیس هر حساب ذخیره می‌شود:
  away_enabled / away_text / away_sent_users
- کنترل: دستورهای .عدم_حضور روشن/خاموش، .متن_عدم_حضور، .away و پنل (.پنل → 💤 پیام عدم حضور).
- پاسخ از client.send_message عبور می‌کند؛ یعنی Unified Pipeline و
  Premium Emoji Resend روی آن فعال‌اند و هیچ ارسال مستقیمی وجود ندارد.
- حلقه‌بندی غیرممکن است: پاسخ‌های Away خودشان در لیست چشم‌پوشی می‌روند و
  هرگز فعال‌ساز ریست نمی‌شوند؛ به بات‌ها پاسخ نمی‌دهیم.

لاگ استاندارد:
    [AWAY]
    user: 123456789
    sent: True
    reason: first_message
"""
from __future__ import annotations

import threading
import time

from telethon import events

import config
import db
from services import telegram_logger as tlog

# متن پیش‌فرض (همان db.DEFAULT_AWAY_TEXT؛ اینجا هم ارجاع داده می‌شود)
DEFAULT_AWAY_TEXT = db.DEFAULT_AWAY_TEXT
AWAY_TEXT_MAX_CHARS = 500

# «تغییر متن از پنل» — انتظار برای پیام متنی بعدی مالک (TTL دار)
_TEXT_CAPTURE_LOCK = threading.RLock()
_TEXT_CAPTURE: dict[int, float] = {}
_TEXT_CAPTURE_TTL_SECONDS = 300

# کلیدهای چشم‌پوشی پاسخ‌های خود Away: (chat_id, message_id)
IGNORE_LIMIT = 512


# ================================================== تنظیمات (دیتابیس)
def get_settings(uid) -> dict:
    """تنظیمات Away یک حساب (همیشه کامل و نرمال‌شده از db)."""
    settings = db.get_user_settings(int(uid))
    return {
        'away_enabled': bool(settings.get('away_enabled', False)),
        'away_text': settings.get('away_text') or DEFAULT_AWAY_TEXT,
        'away_sent_users': dict(settings.get('away_sent_users') or {}),
    }


def set_enabled(uid, enabled: bool) -> None:
    db.update_user_settings(int(uid), {'away_enabled': bool(enabled)})


def set_text(uid, text: str) -> str:
    """ذخیره متن جدید؛ خروجی متن پاک‌سازی‌شده است. خالی → خطا (ValueError)."""
    cleaned = (text or '').strip()
    if not cleaned:
        raise ValueError('متن پیام عدم حضور خالی است')
    if len(cleaned) > AWAY_TEXT_MAX_CHARS:
        raise ValueError(f'متن پیام عدم حضور حداکثر {AWAY_TEXT_MAX_CHARS} کاراکتر است')
    db.update_user_settings(int(uid), {'away_text': cleaned})
    return cleaned


def reset_sent_users(uid) -> int:
    """پاک‌سازی لیست «پیام گرفته‌ها»؛ تعداد پاک‌شده برمی‌گردد."""
    current = db.get_user_settings(int(uid)).get('away_sent_users') or {}
    db.update_user_settings(int(uid), {'away_sent_users': {}})
    return len(current)


def _ttl_seconds() -> float:
    try:
        hours = float(getattr(config, 'AWAY_RESET_HOURS', 0) or 0)
    except (TypeError, ValueError):
        hours = 0.0
    return hours * 3600.0 if hours > 0 else 0.0


# ================================================== لاگ
def _log(user, sent, reason, *, telegram=True):
    try:
        block = tlog.format_away_debug(user=user, sent=sent, reason=reason)
        tlog.send_away_debug(block, telegram=telegram)
    except Exception:  # noqa: BLE001 - لاگ هرگز مسیر را نمی‌شکند
        pass


# ================================================== capture متن از پنل
def begin_text_capture(uid) -> None:
    with _TEXT_CAPTURE_LOCK:
        _TEXT_CAPTURE[int(uid)] = time.time() + _TEXT_CAPTURE_TTL_SECONDS


def cancel_text_capture(uid) -> None:
    with _TEXT_CAPTURE_LOCK:
        _TEXT_CAPTURE.pop(int(uid), None)


def has_text_capture(uid) -> bool:
    with _TEXT_CAPTURE_LOCK:
        expires = _TEXT_CAPTURE.get(int(uid))
        return bool(expires and expires > time.time())


def consume_text_capture(uid) -> bool:
    uid = int(uid)
    with _TEXT_CAPTURE_LOCK:
        expires = _TEXT_CAPTURE.get(uid)
        if not expires:
            return False
        if expires < time.time():
            _TEXT_CAPTURE.pop(uid, None)
            return False
        _TEXT_CAPTURE.pop(uid, None)
        return True


# ================================================== منطق اصلی
def _already_notified(settings, user_id) -> bool:
    sent_users = settings['away_sent_users']
    last = sent_users.get(str(int(user_id)))
    if last is None:
        return False
    ttl = _ttl_seconds()
    if ttl and (time.time() - float(last)) > ttl:
        return False  # سقف زمانی گذشته؛ اجازه ارسال دوباره
    return True


def _mark_notified(uid, user_id) -> None:
    settings = db.get_user_settings(int(uid))
    sent_users = dict(settings.get('away_sent_users') or {})
    sent_users[str(int(user_id))] = time.time()
    db.update_user_settings(int(uid), {'away_sent_users': sent_users})


def _is_private_incoming(event, message) -> bool:
    if getattr(message, 'action', None) is not None:
        return False  # پیام سرویس
    if not event.is_private:
        return False
    # چت با خودِ حساب (Saved) پیام ورودی نیست؛ این هندلر فقط incoming است.
    return True


async def _handle_incoming(client, uid, event) -> None:
    message = getattr(event, 'message', None)
    if message is None or not _is_private_incoming(event, message):
        return
    settings = get_settings(uid)
    if not settings['away_enabled']:
        return
    sender_id = getattr(message, 'sender_id', None)
    if sender_id is None:
        return
    try:
        sender = await event.get_sender()
    except Exception:  # noqa: BLE001 - بدون sender هم ادامه امن است
        sender = None
    if getattr(sender, 'bot', False) or getattr(sender, 'is_self', False):
        return
    user_settings = db.get_user_settings(uid)
    chat_id = event.chat_id
    if chat_id in user_settings.get('muted_chats', []) or \
            chat_id in user_settings.get('enemy_chats', []):
        return  # چت‌های ساکت/دشمن: هیچ پاسخ خودکاری ندارند
    if _already_notified(settings, sender_id):
        _log(sender_id, False, 'already_notified', telegram=False)
        return
    expired = False
    if _ttl_seconds() and str(int(sender_id)) in settings['away_sent_users']:
        expired = True  # فقط وقتی TTL گذاشته شده این مسیر معنا دارد
    try:
        sent = await client.send_message(
            chat_id, settings['away_text'], parse_mode=None)
    except Exception as exc:  # noqa: BLE001 - شکست پاسخ هرگز crash نیست
        _log(sender_id, False, f'send_failed ({type(exc).__name__})')
        return
    # پاسخ Away هرگز نباید خودش نشانه «برگشت مالک» شود → چشم‌پوشی
    _ignore_message(client, chat_id, getattr(sent, 'id', None))
    _mark_notified(uid, sender_id)
    _log(sender_id, True, 'ttl_expired_resend' if expired else 'first_message')


def _ignore_message(client, chat_id, message_id) -> None:
    if message_id is None:
        return
    ignore = getattr(client, '_away_ignore', None)
    if ignore is None:
        ignore = set()
        client._away_ignore = ignore
    key = (chat_id, int(message_id))
    ignore.add(key)
    while len(ignore) > IGNORE_LIMIT:
        ignore.pop()


def _is_ignored(client, chat_id, message_id) -> bool:
    ignore = getattr(client, '_away_ignore', None)
    if not ignore:
        return False
    key = (chat_id, int(message_id))
    if key in ignore:
        ignore.discard(key)  # یک‌بار مصرف؛ حافظه تمیز می‌ماند
        return True
    return False


async def _handle_outgoing(client, uid, event) -> None:
    """هر پیام خروجی مالک = برگشت آنلاین → ریست لیست (اگر چیزی برای ریست)."""
    message = getattr(event, 'message', None)
    if message is None or getattr(message, 'action', None) is not None:
        return
    if _is_ignored(client, event.chat_id, getattr(message, 'id', None)):
        return  # پاسخ خود Away؛ نشانه آنلاین بودن نیست
    settings = db.get_user_settings(int(uid))
    if not settings.get('away_enabled'):
        return
    sent_users = settings.get('away_sent_users') or {}
    if not sent_users:
        return  # چیزی برای ریست نیست؛ بدون دوباره‌نویسی دیتابیس
    db.update_user_settings(int(uid), {'away_sent_users': {}})
    _log('*', False, f'owner_online_reset ({len(sent_users)} users cleared)',
         telegram=False)


# ================================================== نصب هندلرها
def register_away_handlers(client, uid) -> None:
    """نصب هندلرهای Away فقط روی کلاینت Self (هرگز کلاینت بات)."""
    if getattr(client, '_away_handlers_installed', False):
        return
    uid = int(uid)

    @client.on(events.NewMessage(outgoing=True))
    async def _away_text_input_handler(event):
        """ورودی «تغییر متن Away» از پنل — متن بعدی مالک ثبت می‌شود."""
        try:
            if not has_text_capture(uid):
                return
            raw = (getattr(event, 'raw_text', '') or '').strip()
            if raw.startswith('.'):
                # دستور سلف: capture باطل می‌شود؛ خود دستور پردازش عادی می‌شود.
                cancel_text_capture(uid)
                return
            if not raw:
                return
            consume_text_capture(uid)
            try:
                saved_text = set_text(uid, raw)
            except ValueError as exc:
                await client.send_message(
                    event.chat_id, f'❌ {exc}', parse_mode=None)
                return
            _ignore_message(client, event.chat_id,
                            getattr(event, 'id', None))
            try:
                await event.delete()
            except Exception:  # noqa: BLE001
                pass
            confirm = await client.send_message(
                event.chat_id,
                '✅ متن پیام عدم حضور ذخیره شد.\n\n💤 ' + saved_text,
                parse_mode=None)
            _ignore_message(client, event.chat_id,
                            getattr(confirm, 'id', None))
            raise events.StopPropagation
        except events.StopPropagation:
            raise
        except Exception:  # noqa: BLE001 - هیچ خطایی پیام‌رسانی را نمی‌شکند
            pass

    @client.on(events.NewMessage(incoming=True))
    async def _away_incoming_handler(event):
        try:
            await _handle_incoming(client, uid, event)
        except Exception:  # noqa: BLE001 - هیچ خطایی پیام‌رسانی را نمی‌شکند
            pass

    @client.on(events.NewMessage(outgoing=True))
    async def _away_outgoing_handler(event):
        try:
            await _handle_outgoing(client, uid, event)
        except Exception:  # noqa: BLE001
            pass

    client._away_handlers_installed = True
