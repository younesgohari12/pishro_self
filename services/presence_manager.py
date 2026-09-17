"""مدیریت وضعیت آنلاین/آفلاین — Presence Manager (v0.09.14).

هدف spec مالک: وقتی «پیام عدم حضور» (Away) فعال است، اکانت باید Offline بقیه
بماند؛ هیچ مسیری نباید بدون اجازه اکانت را Online کند:

    ❌ send_message      → باعث Online شدن می‌شود (سرور تلگرام)
    ❌ send_file / media → باعث Online شدن می‌شود
    ❌ typing action     → SetTypingRequest — ممنوع در حالت Away
    ❌ read history      → ReadHistoryRequest — ممنوع در حالت Away
    ❌ update status     → UpdateStatusRequest(offline=False) — ممنوع
    ✅ get_messages      → فقط خواندن؛ هیچ تغییری در وضعیت نمی‌دهد (عبور آزاد)

معماری:
- روی ``client._call`` (نقطه ورود همه درخواست‌های Telethon) یک wrapper نصب
  می‌شود؛ هر درخواستی قبل از ارسال classification می‌شود:
    * SetTypingRequest        → «مسدود»  (هیچ typing action ارسال نمی‌شود)
    * UpdateStatusRequest(offline=False) → «مسدود» (وضعیت آنلاین دستی ندارد)
    * خانواده Read*           → «مسدود» (read acknowledgement غیرضروری ندارد)
    * خانواده Send*/Forward   → «اجازه» + زمان‌بندی بازگردانی آفلاین
- بازگردانی آفلاین: بعد از آخرین درخواستِ باعث Online (با debounce کوتاه)
  یک‌بار ``UpdateStatusRequest(offline=True)`` از مسیر اصلی ارسال می‌شود تا
  اکانت در حداقل زمان ممکن دوباره Offline نمایش داده شود.
- خودِ UpdateStatusRequest(offline=True) مجاز است (همان بازگردانی آفلاین است)
  و هرگز دوباره زمان‌بندی نمی‌کند → حلقه غیرممکن است.
- وقتی Away خاموش است wrapper شفاف است: هیچ درخواستی مسدود یا تغییر نمی‌کند
  و هیچ درخواست اضافه‌ای ارسال نمی‌شود.

لاگ استاندارد فارسی:
    [مدیریت وضعیت]
    Online Trigger: send_message
    منبع: send_message
    اقدام: اجازه شد؛ بازگردانی آفلاین زمان‌بندی شد
"""
from __future__ import annotations

import asyncio
import time

import db
from services import telegram_logger as tlog

# فاصله بازگردانی آفلاین (ثانیه) — burst ارسال‌ها با یک‌بار آفلاین‌سازی پوشش
# داده می‌شود؛ مقدار کوتاه است تا بازه «Online» نمایش داده‌شده حداقلی باشد.
DEFAULT_OFFLINE_DELAY = 1.2

# کش پرچم Away برای هر کلاینت — خواندن دیتابیس روی «هر درخواست» گران است؛
# الگوی همان پروژه (converter/resend): کش ۲ ثانیه‌ای.
_ACTIVE_CACHE_TTL = 2.0


# ================================================== classification
def _tl_classes():
    """کلاس‌های TL لازم؛ import دفاعی برای سازگاری نسخه‌های Telethon."""
    from telethon.tl import functions

    def _pick(module, names):
        found = []
        mod = getattr(functions, module, None)
        if mod is None:
            return found
        for name in names:
            cls = getattr(mod, name, None)
            if cls is not None:
                found.append(cls)
        return tuple(found)

    typing = _pick('messages', ('SetTypingRequest', 'SetEncryptedTypingRequest'))
    read = _pick('messages', (
        'ReadHistoryRequest', 'ReadMentionsRequest', 'ReadDiscussionRequest',
        'ReadReactionsRequest', 'ReadMessageContentsRequest',
        'ReadEncryptedHistoryRequest',
    ))
    send = _pick('messages', (
        'SendMessageRequest', 'SendMediaRequest', 'SendMultiMediaRequest',
        'SendInlineBotResultRequest', 'SendScreenshotNotificationRequest',
        'SendVoteRequest', 'SendReactionRequest', 'ForwardMessagesRequest',
        'SendWebViewDataRequest', 'SendWebViewResultRequest',
    ))
    status = _pick('account', ('UpdateStatusRequest',))
    return typing, read, send, status


_TYPING_CLASSES, _READ_CLASSES, _SEND_CLASSES, _STATUS_CLASSES = _tl_classes()


def _iter_requests(request):
    """``client(...)`` هم تک‌درخواست می‌گیرد هم لیست؛ یکسان‌سازی."""
    if isinstance(request, (list, tuple)):
        return list(request)
    return [request]


def classify_request(request):
    """نوع اثر یک درخواست TL روی وضعیت آنلاین.

    خروجی یکی از:
      'typing'   — action نمایش تایپ کردن (باعث Online می‌شود)
      'read'     — read acknowledgement (باعث Online می‌شود)
      'send'     — ارسال پیام/رسانه/فوروارد/واکنش (باعث Online می‌شود)
      'status'   — تغییر دستی وضعیت آنلاین/آفلاین
      'other'    — بدون اثر شناخته‌شده (مثل get_messages فقط‌خواندنی)
    """
    for cls in _TYPING_CLASSES:
        if isinstance(request, cls):
            return 'typing'
    for cls in _READ_CLASSES:
        if isinstance(request, cls):
            return 'read'
    for cls in _SEND_CLASSES:
        if isinstance(request, cls):
            return 'send'
    for cls in _STATUS_CLASSES:
        if isinstance(request, cls):
            return 'status'
    return 'other'


# ================================================== لاگ فارسی
def _log(trigger, source, action, *, telegram=True):
    try:
        block = tlog.format_presence_debug(
            trigger=trigger, source=source, action=action)
        tlog.send_presence_debug(block, telegram=telegram)
    except Exception:  # noqa: BLE001 - لاگ هرگز مسیر ارسال را نمی‌شکند
        pass


# ================================================== نصب روی کلاینت
def _make_default_active(uid, ttl=_ACTIVE_CACHE_TTL):
    """پرچم Away از دیتابیس با کش کوتاه (الگوی converter/resend پروژه)."""
    uid = int(uid)
    cache = {'value': False, 'at': 0.0}

    def active():
        now = time.monotonic()
        if now - cache['at'] > ttl:
            try:
                cache['value'] = bool(
                    db.get_user_settings(uid).get('away_enabled'))
                cache['at'] = now
            except Exception:  # noqa: BLE001 - دیتابیس موقتاً در دسترس نیست
                if cache['at'] == 0.0:
                    cache['at'] = now  # اولین خواندن شکست خورد → پیش‌فرض خاموش
            # در غیر این صورت آخرین مقدار معتبر حفظ می‌شود
        return cache['value']

    return active


def install_presence_manager(client, uid, *, is_active=None,
                             offline_delay=DEFAULT_OFFLINE_DELAY):
    """نصب Presence Manager روی کلاینت Self (هرگز کلاینت بات).

    - ``is_active``: تابع بدون آرگومان که True/False وضعیت Away را می‌دهد؛
      پیش‌فرض از دیتابیس با کش ۲ ثانیه‌ای می‌خواند.
    - ``offline_delay``: فاصله debounce بازگردانی آفلاین (برای تست کوتاه می‌شود).
    """
    if getattr(client, '_presence_installed', False):
        return False
    original_call = getattr(client, '_call', None)
    if original_call is None or not callable(original_call):
        return False
    active = is_active if is_active is not None else _make_default_active(uid)

    state = {
        'original_call': original_call,   # مسیر اصلی (بدون wrapper)
        'sender': None,                   # آخرین sender دیده‌شده
        'offline_due': 0.0,               # موعد بازگردانی آفلاین
        'worker': None,                   # تسک debounce جاری
    }

    async def _send_offline_now():
        """یک‌بار UpdateStatusRequest(offline=True) از مسیر اصلی ارسال می‌کند."""
        request_cls = _STATUS_CLASSES[0] if _STATUS_CLASSES else None
        if request_cls is None:
            return
        sender = state['sender'] or getattr(client, '_sender', None)
        if sender is None:
            return
        try:
            await state['original_call'](sender, request_cls(offline=True))
            _log('send_message', 'send_message',
                 'اکانت دوباره Offline شد (UpdateStatus offline=True)',
                 telegram=False)
        except Exception:  # noqa: BLE001 - بازگردانی آفلاین هرگز crash نیست
            pass

    async def _offline_worker():
        """debounce انتهای موج: بعد از آخرین trigger آفلاین می‌کند."""
        while True:
            remaining = state['offline_due'] - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(remaining, 0.5))
        state['worker'] = None
        await _send_offline_now()

    def _schedule_offline():
        state['offline_due'] = time.monotonic() + max(0.0, float(offline_delay))
        if state['worker'] is None or state['worker'].done():
            try:
                state['worker'] = asyncio.get_running_loop().create_task(
                    _offline_worker())
            except RuntimeError:
                # رویداد لوپ در حال اجرا نیست؛ بازگردانی بعد از اتصال انجام می‌شود
                state['worker'] = None

    async def presence_call(sender, request, ordered=False,
                            flood_sleep_threshold=None):
        """جایگزین ``client._call`` — بازرسی وضعیت قبل از هر درخواست."""
        try:
            state['sender'] = sender or getattr(client, '_sender', None)
            if active():
                blocked_action = None
                trigger = source = None
                for single in _iter_requests(request):
                    kind = classify_request(single)
                    if kind == 'typing':
                        blocked_action = ('typing', 'typing',
                                          'مسدود شد — هیچ typing action ارسال نشد')
                    elif kind == 'read':
                        blocked_action = ('read', 'read',
                                          'مسدود شد — read acknowledgement ارسال نشد')
                    elif kind == 'status':
                        offline = bool(getattr(single, 'offline', True))
                        if not offline:  # فقط درخواست «Online شدن» ممنوع است
                            blocked_action = ('status_online', 'manual',
                                              'مسدود شد — وضعیت آنلاین دستی ارسال نشد')
                    elif kind == 'send':
                        trigger = source = 'send_message'
                        _log('send_message', 'send_message',
                             'اجازه شد؛ بازگردانی آفلاین زمان‌بندی شد')
                        _schedule_offline()
                    if blocked_action:
                        break
                if blocked_action:
                    _log(*blocked_action)
                    return True  # نتیجه بی‌ضرر؛ درخواست اصلاً ارسال نمی‌شود
        except Exception:  # noqa: BLE001 - Presence هرگز مسیر ارسال را نمی‌شکند
            pass
        return await state['original_call'](
            sender, request, ordered=ordered,
            flood_sleep_threshold=flood_sleep_threshold)

    client._call = presence_call  # noqa: B010 - نقطه ورود واحد درخواست‌ها
    client._presence_state = state
    client._presence_installed = True
    return True


def uninstall_presence_manager(client) -> None:
    """برداشتن wrapper و لغو تسک debounce (پاک‌سازی کامل در shutdown)."""
    state = getattr(client, '_presence_state', None)
    worker = (state or {}).get('worker')
    if worker is not None and not worker.done():
        worker.cancel()
    if getattr(client, '_presence_installed', False):
        try:
            del client._call   # برگشت به متد کلاس (بدون wrapper)
        except AttributeError:
            pass
        client._presence_installed = False
    if state is not None:
        try:
            del client._presence_state
        except AttributeError:
            pass


def assert_offline(client):
    """آفلاین‌سازی فوری (بدون debounce) — برای لحظه روشن کردن Away."""
    state = getattr(client, '_presence_state', None)
    if not state:
        return False
    request_cls = _STATUS_CLASSES[0] if _STATUS_CLASSES else None
    if request_cls is None:
        return False
    sender = state.get('sender') or getattr(client, '_sender', None)
    if sender is None:
        return False

    async def _job():
        try:
            await state['original_call'](sender, request_cls(offline=True))
        except Exception:  # noqa: BLE001
            pass

    try:
        task = asyncio.get_running_loop().create_task(_job())
        state['worker'] = state.get('worker') or task
        return True
    except RuntimeError:
        return False
