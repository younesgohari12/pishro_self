"""Self-only translation command handler."""
from __future__ import annotations

import re

from services.feature_flags import enabled, TRANSLATE_DISABLED
from database import models
from services.ai_assistant import RateLimitExceeded, request_limiter
from services.avalai_ai import AvalAIChatError
from services.logging_service import log_api_error
from services.translator import normalize_language, parse_translate_command, translate_text


_TRANSLATE_RE = re.compile(r'^\s*\.ترجمه(?:\s+.*)?$', re.IGNORECASE | re.DOTALL)


def is_translate_command(text: str) -> bool:
    return bool(_TRANSLATE_RE.match(text or ''))


def translate_help_text() -> str:
    return (
        '🌐 ترجمه\n\n'
        'نمونه‌ها:\n'
        '.ترجمه فارسی به انگلیسی سلام\n'
        '.ترجمه انگلیسی به فارسی hello\n'
        'روی متن ریپلای کن و بنویس: .ترجمه فارسی\n'
        'برای زبان‌های دیگر: .ترجمه آلمانی یا .ترجمه ترکی استانبولی\n'
        '.ترجمه hello\n\n'
        'اگر زبان مبدا را ننویسی، خودکار تشخیص داده می‌شود و متن به زبان پیش‌فرض شما ترجمه می‌شود.'
    )


async def execute_translate_text(user_id: int, raw_text: str, *, reply_text: str | None = None) -> str:
    if not enabled('self_translate'):
        return TRANSLATE_DISABLED
    settings = models.get_ai_user_settings(int(user_id))
    parsed = parse_translate_command(
        raw_text,
        default_language=settings.get('default_language') or 'Persian',
        reply_text=reply_text,
    )
    if not parsed['text']:
        return translate_help_text()

    try:
        request_limiter.check(int(user_id))
        translated = await translate_text(
            parsed['text'],
            source_language=parsed['source'],
            target_language=parsed['target'],
        )
        return translated
    except RateLimitExceeded as exc:
        return f'⏳ تعداد درخواست‌ها زیاد است. حدود {exc.retry_after} ثانیه دیگر دوباره تلاش کن.'
    except AvalAIChatError as exc:
        log_api_error('translate_request', exc, user_id=int(user_id))
        return '⚠️ سرویس ترجمه پاسخ قابل استفاده‌ای نداد؛ کمی بعد دوباره تلاش کن.'
    except ValueError as exc:
        return f'⚠️ {exc}\n\n{translate_help_text()}'
    except Exception as exc:
        log_api_error('translate_request_unexpected', exc, user_id=int(user_id))
        return '⚠️ ترجمه موقتاً در دسترس نیست.'

