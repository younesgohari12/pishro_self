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
