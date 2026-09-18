"""طبقه‌بند مشترک خطاهای سرویس‌های بیرونی (STT / TTS / ترجمه هوشمند).

ریشه‌ای که این ماژول حل می‌کند: پیش از این، هر سه قابلیتِ وابسته به سرویس
بیرونی (AvalAI) متن خام انگلیسیِ خطا — مثل «Your account credit has been
exhausted...» — را مستقیم به کاربر نشان می‌دادند و هیچ راهنمایی برای رفع
مشکل وجود نداشت.

قرارداد سازگاری مهم (backward-compatible):
- انواع **شناخته‌شده** (اعتبار/احراز هویت/محدودیت/سرور/زمان/شبکه) به پیام
  فارسی کوتاه نگاشت می‌شوند.
- متن‌های **ناشناخته** دست‌نخورده برمی‌گردند (kind='other') تا رفتار فعلی
  هیچ قابلیتی تغییر نکند.
"""
from __future__ import annotations

CREDIT_PATTERNS = (
    'credit', 'exhausted', 'billing', 'top up', 'insufficient',
    'quota', 'balance', 'payment',
)
AUTH_PATTERNS = (
    'unauthorized', 'invalid api key', 'invalid_api_key', 'authentication',
    'forbidden', 'incorrect api key', 'api key not valid', 'permission',
)
RATE_PATTERNS = ('rate limit', 'too many requests', 'ratelimit')
TIMEOUT_PATTERNS = ('timeout', 'timed out', 'deadline exceeded')
NETWORK_PATTERNS = (
    'connection', 'getaddrinfo', 'name or service not known',
    'client connector', 'ssl:', 'network',
)

FA_BY_KIND = {
    'credit': 'اعتبار سرویس تمام شده است (نیاز به شارژ یا تأمین‌کننده پشتیبان).',
    'auth': 'کلید API نامعتبر است یا دسترسی ندارد.',
    'rate': 'محدودیت تعداد درخواست؛ کمی بعد دوباره تلاش کن.',
    'server': 'خطا در سمت سرویس؛ بعداً دوباره تلاش کن.',
    'timeout': 'زمان پاسخ سرویس تمام شد؛ دوباره تلاش کن.',
    'network': 'خطای اتصال به سرویس؛ شبکه سرور را بررسی کن.',
}


def classify_provider_error(message, status_code=None):
    """متن/کد خطا را به (kind, پیام فارسی) نگاشت می‌کند.

    - اولویت با status_code است (402/401/403/429/5xx).
    - متن ناشناخته → ('other', همان متن) تا رفتار قبلی حفظ شود.
    """
    status = None
    try:
        status = int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        status = None

    text = (message or '').strip()
    low = text.lower()

    if status == 402:
        return 'credit', FA_BY_KIND['credit']
    if status in (401, 403):
        return 'auth', FA_BY_KIND['auth']
    if status == 429:
        return 'rate', FA_BY_KIND['rate']
    if status is not None and status >= 500:
        return 'server', FA_BY_KIND['server']

    if any(p in low for p in CREDIT_PATTERNS):
        return 'credit', FA_BY_KIND['credit']
    if any(p in low for p in AUTH_PATTERNS):
        return 'auth', FA_BY_KIND['auth']
    if any(p in low for p in RATE_PATTERNS):
        return 'rate', FA_BY_KIND['rate']
    if any(p in low for p in TIMEOUT_PATTERNS):
        return 'timeout', FA_BY_KIND['timeout']
    if any(p in low for p in NETWORK_PATTERNS):
        return 'network', FA_BY_KIND['network']

    return 'other', text[:700] if text else FA_BY_KIND['server']


def status_suffix(status_code):
    """« (402)» برای نمایش در خلاصه خطا؛ بدون کد → رشته خالی."""
    try:
        status = int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        return ''
    return f' ({status})' if status else ''
