"""پیام عدم حضور — پاسخ خودکار خصوصی هنگام آفلاین بودن مالک (v0.09.14).

رفتار (مطابق spec جدید مالک):
- فقط چت خصوصی (Private)؛ گروه/کانال هرگز. بات‌ها و پیام‌های سرویس هرگز.
- محدودیت «per-chat» است نه global: بعد از هر آفلاین شدن، در هر چت فقط
  «یک بار» پیام عدم حضور ارسال می‌شود:
      Chat A: سلام  → یک پیام عدم حضور
      Chat A: خوبی؟ → هیچ پیامی ارسال نمی‌شود
      Chat B: سلام  → یک پیام عدم حضور (مدیریت جدا از Chat A)
- دیتابیس (ساختار درخواستی مالک):
      away_enabled / away_text / away_active_session / away_sent_chats
      away_sent_chats = { chat_id: timestamp }
- ریست لیست فقط و فقط با:
      * خاموش کردن دستی (.عدم_حضور خاموش / پنل)
      * روشن کردن دوباره (چرخه خاموش/روشن = شروع دوره عدم حضور تازه)
      * «پاک کردن لیست» دستی (.عدم_حضور ریست / پنل)
      * شروع یک Session جدید واقعی (restart سلف)
  ❌ هیچ پیام خروجی، send_message، event outgoing یا typing لیست را ریست
     نمی‌کند (این رفتار v0.09.13 باعث loop و اسپم می‌شد — حذف شد).
- کنترل: .عدم_حضور روشن/خاموش/ریست، .متن_عدم_حضور، .away و پنل (.پنل → 💤 پیام عدم حضور).
- پاسخ از client.send_message عبور می‌کند ولی «هرگز» وارد سیستم ایموجی
  ویژه نمی‌شود (AWAY_BYPASS_PREMIUM = True — اصلاح نهایی v0.09.14):
      ❌ بدون Custom Emoji Pipeline (کانورتر)
      ❌ بدون Resend (مدیر ارسال دوباره)
      ❌ بدون Delete/New Send
  و Presence Manager بعد از ارسال، اکانت را دوباره Offline می‌کند
  (ارسال پاسخ Away باعث Online ماندن نمی‌شود؛ وضعیت قبل ارسال و
  زمان بازگردانی Offline ثبت می‌شود).
- حلقه‌بندی غیرممکن است: هیچ مسیر ریستی به پیام‌های خروجی وصل نیست و
  به بات‌ها پاسخ نمی‌دهیم.

لاگ استاندارد فارسی:
    [پیام عدم حضور]
    شناسه چت: 123456789
    وضعیت: روشن
    نتیجه: ارسال شد
"""
from __future__ import annotations

import threading
import time
from uuid import uuid4

from telethon import events

import db
from services import away_bypass
from services import presence_manager as presence
from services import telegram_logger as tlog

# متن پیش‌فرض (همان db.DEFAULT_AWAY_TEXT؛ اینجا هم ارجاع داده می‌شود)
DEFAULT_AWAY_TEXT = db.DEFAULT_AWAY_TEXT
AWAY_TEXT_MAX_CHARS = 500

# «تغییر متن از پنل» — انتظار برای پیام متنی بعدی مالک (TTL دار)
_TEXT_CAPTURE_LOCK = threading.RLock()
_TEXT_CAPTURE: dict[int, float] = {}
_TEXT_CAPTURE_TTL_SECONDS = 300


# ================================================== تنظیمات (دیتابیس)
def get_settings(uid) -> dict:
    """تنظیمات Away یک حساب (همیشه کامل و نرمال‌شده از db)."""
    settings = db.get_user_settings(int(uid))
    return {
        'away_enabled': bool(settings.get('away_enabled', False)),
        'away_text': settings.get('away_text') or DEFAULT_AWAY_TEXT,
        'away_sent_chats': dict(settings.get('away_sent_chats') or {}),
        'away_active_session': str(settings.get('away_active_session') or ''),
    }


def set_enabled(uid, enabled: bool) -> None:
    """روشن/خاموش کردن Away.

    هر تغییر وضعیت = شروع یک دوره جدید عدم حضور → لیست چت‌های پاسخ داده
    شده پاک می‌شود (مطابق spec: خاموش/روشن کردن لیست را reset می‌کند).
    """
    uid = int(uid)
    cleared = reset_sent_chats(uid)
    db.update_user_settings(uid, {'away_enabled': bool(enabled)})
    state = 'روشن' if enabled else 'خاموش'
    _log('-', state, f'لیست ریست شد ({cleared} چت)', telegram=False)


def set_text(uid, text: str) -> str:
    """ذخیره متن جدید؛ خروجی متن پاک‌سازی‌شده است. خالی → خطا (ValueError)."""
    cleaned = (text or '').strip()
    if not cleaned:
        raise ValueError('متن پیام عدم حضور خالی است')
    if len(cleaned) > AWAY_TEXT_MAX_CHARS:
        raise ValueError(f'متن پیام عدم حضور حداکثر {AWAY_TEXT_MAX_CHARS} کاراکتر است')
    db.update_user_settings(int(uid), {'away_text': cleaned})
    return cleaned


def reset_sent_chats(uid) -> int:
    """پاک‌سازی «لیست چت‌های پاسخ داده شده»؛ تعداد پاک‌شده برمی‌گردد."""
    current = db.get_user_settings(int(uid)).get('away_sent_chats') or {}
    db.update_user_settings(int(uid), {'away_sent_chats': {}})
    return len(current)


# سازگاری نام قدیمی (v0.09.13) — پنل/دستورها به نام جدید مهاجرت کردند
def reset_sent_users(uid) -> int:
    return reset_sent_chats(uid)


def start_session(uid) -> int:
    """شروع یک Session جدید واقعی → لیست چت‌های پاسخ داده شده پاک می‌شود.

    شناسه سشن در ``away_active_session`` ذخیره می‌شود. تعداد چت‌های پاک‌شده
    برمی‌گردد. این تابع هرگز از پیام‌ها فراخوانی نمی‌شود؛ فقط از نصب هندلرها
    (restart سلف = سشن واقعی جدید).
    """
    uid = int(uid)
    settings = db.get_user_settings(uid)
    previous = str(settings.get('away_active_session') or '')
    sent_chats = dict(settings.get('away_sent_chats') or {})
    token = uuid4().hex
    db.update_user_settings(uid, {'away_active_session': token,
                                  'away_sent_chats': {}})
    if sent_chats or previous:
        _log('-', 'خاموش', f'سشن جدید شروع شد؛ لیست ریست شد '
                            f'({len(sent_chats)} چت)', telegram=False)
    return len(sent_chats)


# ================================================== لاگ فارسی
def _log(chat_id, status, result, *, telegram=True):
    try:
        block = tlog.format_away_debug(chat_id=chat_id, status=status,
                                       result=result)
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


# ================================================== منطق اصلی (per-chat)
def _already_notified(settings, chat_id) -> bool:
    """آیا این «چت» بعد از آخرین آفلاین شدن، پیام عدم حضور گرفته است؟"""
    return str(int(chat_id)) in settings['away_sent_chats']


def _mark_notified(uid, chat_id) -> None:
    settings = db.get_user_settings(int(uid))
    sent_chats = dict(settings.get('away_sent_chats') or {})
    sent_chats[str(int(chat_id))] = time.time()
    db.update_user_settings(int(uid), {'away_sent_chats': sent_chats})


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
        return  # Away خاموش: هیچ بررسی/لاگی لازم نیست
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
    if _already_notified(settings, chat_id):
        _log(chat_id, 'روشن', 'قبلاً ارسال شده', telegram=False)
        return  # در این چت فقط یک بار؛ پیام‌های بعدی هیچ پاسخی نمی‌گیرند
    # 🛡 ثبت وضعیت اکانت قبل از ارسال پاسخ عدم حضور (spec مالک):
    #    «قبل ارسال Away وضعیت چیست» → بلوک [مدیریت وضعیت]
    presence.report_pre_away_status(client)
    # 🔍 [AWAY_TRACE] (Audit نهایی v0.09.15) — شروع Trace این ارسال:
    #    کانورتر/اینجکتور (گارد) و مدیر ارسال دوباره (registry skip) هر کدام
    #    از سمت خودشان این Trace را تکمیل/تأیید می‌کنند.
    trigger = (f'پیام خصوصی ورودی (فرستنده={sender_id}, '
               f'پیام={getattr(message, "id", None)})')
    trace = away_bypass.begin_away_trace(chat_id, trigger)
    try:
        # 🚫 AWAY_BYPASS_PREMIUM = True — پاسخ عدم حضور هرگز وارد سیستم
        #    ایموجی ویژه نمی‌شود: بدون Custom Emoji Pipeline، بدون Resend،
        #    بدون Delete/New Send (استقلال کامل — spec مالک).
        async with away_bypass.away_send_guard() as bypass_engaged:
            sent = await client.send_message(
                chat_id, settings['away_text'], parse_mode=None)
    except Exception as exc:  # noqa: BLE001 - شکست پاسخ هرگز crash نیست
        away_bypass.fail_away_trace(trace, type(exc).__name__)
        _log(chat_id, 'روشن', f'ارسال ناموفق ({type(exc).__name__})')
        return
    trace['bypass_active'] = bool(bypass_engaged)
    # 🔒 ثبت پیام در registry داخلی: رویداد outgoing بعدیِ این پیام هرگز
    #    به مدیر ارسال دوباره (Premium Emoji Resend) نمی‌رسد.
    away_bypass.mark_away_reply(sent)
    _mark_notified(uid, chat_id)
    _log(chat_id, 'روشن', 'ارسال شد')
    # 🔍 پایان Trace: فرستنده نهایی «client.send_message خام» بود؛ بعد از
    #    این، PREMIUM_TRACE (is_away=بله) از سمت مدیر ارسال دوباره می‌آید.
    away_bypass.finalize_away_trace(
        trace, sent, 'client.send_message (بدون تبدیل — متن خام)')
    try:
        tlog.send_away_trace(tlog.format_away_trace(trace), chat_id=chat_id)
    except Exception:  # noqa: BLE001 - ثبت هرگز مسیر را نمی‌شکند
        pass


# ================================================== نصب هندلرها
def register_away_handlers(client, uid) -> None:
    """نصب هندلرهای Away فقط روی کلاینت Self (هرگز کلاینت بات).

    هر نصب = شروع یک سشن واقعی جدید → لیست چت‌های پاسخ داده شده پاک می‌شود
    (ریست با پیام خروجی مالک وجود ندارد؛ این تنها ریست خودکار مجاز است).
    """
    if getattr(client, '_away_handlers_installed', False):
        return
    uid = int(uid)
    start_session(uid)

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
            try:
                await event.delete()
            except Exception:  # noqa: BLE001
                pass
            confirm = await client.send_message(
                event.chat_id,
                '✅ متن پیام عدم حضور ذخیره شد.\n\n💤 ' + saved_text,
                parse_mode=None)
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

    client._away_handlers_installed = True
