"""AWAY_BYPASS_PREMIUM — نگهبان استقلال «پیام عدم حضور» از «ایموجی ویژه».

هدف spec مالک (اصلاح نهایی v0.09.14): وقتی پاسخ عدم حضور (Away Reply)
ارسال می‌شود، این پاسخ هرگز نباید وارد سیستم ایموجی ویژه شود:

    ❌ بدون Custom Emoji Pipeline  (کانورترِ قبل از ارسال)
    ❌ بدون Resend                 (مدیر ارسال دوباره)
    ❌ بدون Delete/New Send

دو مکانیزم مکمل لازم است (رویداد outgoingِ پیام، از task دیگری از سرور
می‌رسد و ContextVar به آن task منتقل نمی‌شود):

  1) Context guard (قبل از ارسال): ``away_send_guard()`` → کانورتر
     (services/premium_emoji_converter.py) و injector
     (services/premium_emoji_injector.py) پیام را «دست‌نخورده» عبور
     می‌دهند؛ هیچ تبدیل/Entity ای انجام نمی‌شود.
  2) Message registry (بعد از ارسال): ``mark_away_reply(sent_message)``
     شناسه پیام پاسخ عدم حضور را ثبت می‌کند و مدیر ارسال دوباره
     (services/emoji_resend_manager.py → handle_outgoing) این پیام‌ها
     را «نادیده» می‌گیرد؛ یعنی هیچ بررسی سرور، حذف یا ارسال جدیدی
     برای آن‌ها رخ نمی‌دهد.

کلید config: ``AWAY_BYPASS_PREMIUM`` (پیش‌فرض: True).

استقلال کامل: این ماژول عمداً هیچ وابستگی به services/away.py یا
ماژول‌های پریمیوم ندارد؛ فقط یک پل خنثی بین دو سیستم است تا
«Away و Premium Emoji کاملاً مستقل» بمانند.
"""
from __future__ import annotations

import contextlib
import time
from collections import OrderedDict
from contextvars import ContextVar

import config

# عمر registry: رویداد outgoing معمولاً کمتر از چند ثانیه بعد از ارسال
# به کلاینت می‌رسد؛ ۲۰ ثانیه حاشیه امن کامل است و فراموشی خودکار دارد.
AWAY_REPLY_TTL_SECONDS = 20.0
_REGISTRY_LIMIT = 512

# Context guard — فقط در همان task ارسال معتبر است (قبل از ارسال)
_ACTIVE = ContextVar('away_bypass_premium_active', default=False)

# Message registry — برای رویداد outgoing که از task دیگر می‌آید
_REPLIES: "OrderedDict[tuple[int, int], float]" = OrderedDict()


def bypass_enabled() -> bool:
    """کلید اصلی AWAY_BYPASS_PREMIUM (پیش‌فرض True — امن‌ترین حالت)."""
    return bool(getattr(config, 'AWAY_BYPASS_PREMIUM', True))


def active() -> bool:
    """آیا اکنون داخل «ارسال پاسخ عدم حضور» هستیم؟ (context guard)"""
    return bool(_ACTIVE.get())


@contextlib.asynccontextmanager
async def away_send_guard(enabled=None):
    """Context ارسال بدون سیستم ایموجی ویژه.

    ``enabled=None`` یعنی از کلید config بخوان؛ مقدار True/False صریح
    برای تست‌ها و فراخوان‌های خاص است. وقتی کلید خاموش باشد، guard
    شفاف است (هیچ پرچمی ست نمی‌شود) و رفتار عادی pipeline برمی‌گردد.
    """
    if not (bypass_enabled() if enabled is None else bool(enabled)):
        yield False
        return
    token = _ACTIVE.set(True)
    try:
        yield True
    finally:
        _ACTIVE.reset(token)


# ================================================== message registry
def _prune(now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    expired = [key for key, stamp in _REPLIES.items()
               if now - stamp > AWAY_REPLY_TTL_SECONDS]
    for key in expired:
        _REPLIES.pop(key, None)
    while len(_REPLIES) > _REGISTRY_LIMIT:
        _REPLIES.popitem(last=False)


def _message_key(message) -> tuple[int, int] | None:
    try:
        chat_id = getattr(message, 'chat_id', None)
        message_id = getattr(message, 'id', None)
        if chat_id is None or message_id is None:
            return None
        return int(chat_id), int(message_id)
    except (TypeError, ValueError):
        return None


def mark_away_reply(message) -> bool:
    """ثبت پیام «پاسخ عدم حضور» — بعد از این، Resend هرگز به آن نمی‌رسد.

    وقتی AWAY_BYPASS_PREMIUM خاموش باشد، هیچ ثبتی انجام نمی‌شود (False).
    """
    if not bypass_enabled():
        return False
    key = _message_key(message)
    if key is None:
        return False
    _REPLIES[key] = time.monotonic()
    _prune()
    return True


def is_away_reply(message) -> bool:
    """آیا این پیام یکی از پاسخ‌های عدم حضور است؟ (registry با TTL)"""
    if not bypass_enabled():
        return False
    if not _REPLIES:
        return False
    key = _message_key(message)
    if key is None:
        return False
    stamp = _REPLIES.get(key)
    if stamp is None:
        return False
    if time.monotonic() - stamp > AWAY_REPLY_TTL_SECONDS:
        _REPLIES.pop(key, None)
        return False
    return True


def registry_size() -> int:
    """تعداد پیام‌های ثبت‌شده (برای تست/دیاگ)."""
    return len(_REPLIES)


def clear_registry() -> None:
    """پاک‌سازی کامل registry — فقط برای تست و ریست سشن."""
    _REPLIES.clear()


# ================================================== [AWAY_TRACE] (Audit)
# Audit نهایی v0.09.15 — ردیابی هر «ارسال پیام عدم حضور» برای اثبات این
# که پاسخ Away هرگز وارد سیستم ایموجی ویژه نمی‌شود. این بخش فقط «ثبت»
# است؛ هیچ رفتار ارسالی را تغییر نمی‌دهد (هیچ قابلیت جدیدی نیست).
#
# اتصال واقعی سیستم‌ها (سه نقطه مستقل گزارش می‌دهند):
#   1) services/away.py            → begin/finalize (ارسال واقعی)
#   2) premium_emoji_converter.py  → note_pipeline_guard('converter')
#   3) premium_emoji_injector.py   → note_pipeline_guard('injector')
#   4) emoji_resend_manager.py     → note_resend_away_skip(message)
AWAY_TRACE_TTL_SECONDS = 20.0
_TRACE_LIMIT = 128

# Trace جاری فقط در همان task ارسال معتبر است (کانورتر/اینجکتور داخل
# همان task اجرا می‌شوند)؛ رویداد outgoing از task دیگر می‌آید و از
# _TRACES (کلید chat_id/message_id) پیدا می‌شود.
_CURRENT_TRACE: ContextVar = ContextVar('away_trace_current', default=None)
_TRACES: "OrderedDict[tuple[int, int], dict]" = OrderedDict()


def _prune_traces(now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    expired = [key for key, record in _TRACES.items()
               if now - record['_stamp'] > AWAY_TRACE_TTL_SECONDS]
    for key in expired:
        _TRACES.pop(key, None)
    while len(_TRACES) > _TRACE_LIMIT:
        _TRACES.popitem(last=False)


def begin_away_trace(chat_id, trigger) -> dict:
    """شروع Trace برای یک ارسال پاسخ عدم حضور (قبل از away_send_guard).

    خروجی همان record ای است که finalize/fail باید با آن صدا زده شود.
    """
    record = {
        'chat_id': chat_id,
        'trigger': trigger,
        'bypass_active': False,
        'guard_hits': [],
        'premium_pipeline_entered': False,
        'resend_entered': False,
        'resend_note': None,
        'final_sender': None,
        'send_error': None,
        '_stamp': time.monotonic(),
    }
    _CURRENT_TRACE.set(record)
    return record


def note_pipeline_guard(system: str) -> None:
    """ثبت این که گارد AWAY_BYPASS در یک سیستم پریمیوم «برخورد کرد».

    converter/injector در اولین خط wrap خود، وقتی ``away_bypass.active()``
    برقرار است صدا می‌زنند؛ یعنی پیام پاسخ عدم حضور «دست‌نخورده» از آن
    سیستم عبور کرد (هیچ تبدیل/Entity ای انجام نشد). هرگز خطا نمی‌دهد.
    """
    try:
        record = _CURRENT_TRACE.get()
        if record is not None and system not in record['guard_hits']:
            record['guard_hits'].append(str(system))
    except Exception:  # noqa: BLE001 - ثبت هرگز مسیر ارسال را نمی‌شکند
        pass


def finalize_away_trace(record, sent_message, final_sender: str) -> dict:
    """پایان Trace موفق: ثبت فرستنده نهایی و اتصال record به کلید پیام.

    بعد از این مرحله، ``note_resend_away_skip`` (از task رویداد outgoing)
    می‌تواند همین record را با کلید (chat_id, message_id) پیدا کند.
    """
    record['final_sender'] = final_sender
    record['_stamp'] = time.monotonic()
    key = _message_key(sent_message)
    if key is not None:
        _TRACES[key] = record
        _prune_traces()
    _CURRENT_TRACE.set(None)
    return record


def fail_away_trace(record, error: str) -> dict:
    """پایان Trace ناموفق (ارسال پاسخ شکست خورد) — بدون ثبت پیام."""
    record['send_error'] = str(error)
    _CURRENT_TRACE.set(None)
    return record


def note_resend_away_skip(message) -> bool:
    """اثبات از سمت Premium: مدیر ارسال دوباره پیام Away را «دید و رد کرد».

    services/emoji_resend_manager.py در اولین بررسی handle_outgoing و
    قبل از هر اقدامی (بررسی سرور/حذف/ارسال جدید) این را صدا می‌زند.
    خروجی: آیا Trace متناظر پیدا و به‌روزرسانی شد؟
    """
    try:
        if not _TRACES:
            return False
        key = _message_key(message)
        if key is None:
            return False
        record = _TRACES.get(key)
        if record is None:
            return False
        record['resend_note'] = ('مدیر ارسال دوباره این پیام را دید و '
                                 'بدون هیچ اقدامی رد کرد (registry)')
        return True
    except Exception:  # noqa: BLE001 - ثبت هرگز مسیر را نمی‌شکند
        return False


def trace_registry_size() -> int:
    """تعداد Trace های فعال (برای تست/دیاگ)."""
    return len(_TRACES)


def clear_traces() -> None:
    """پاک‌سازی کامل Trace ها — فقط برای تست."""
    _TRACES.clear()
