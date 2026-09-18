"""سرویس تبدیل صوت به متن (STT) — زنجیره‌ی تأمین‌کنندگان با fallback خودکار.

ریشه‌ای که این سرویس حل می‌کند: تا نسخه 0.09.18 تبدیل صوت فقط به «یک» سرویس
(AvalAI) وابسته بود؛ با اتمام اعتبار آن، قابلیت کاملاً از کار می‌افتاد و خطای
خام انگلیسی به کاربر نشان داده می‌شد.

معماری جدید (بر اساس فرمان مالک: ریشه، نه پچ):
- زنجیره‌ی تأمین‌کنندگان: ابتدا AvalAI (رفتار فعلی)، سپس تأمین‌کنندگان
  پشتیبانِ OpenAI-compatible از کانفیگ پایدار ``STT_FALLBACK_PROVIDERS``.
- شکست هر تأمین‌کننده طبقه‌بندی و به فارسی گزارش می‌شود (services.
  ai_error_classifier) و زنجیره ادامه می‌یابد.
- مدارشکن: تأمین‌کننده‌ای که خطای اعتبار/احراز هویت داده، ۱۰ دقیقه کنار گذاشته
  می‌شود تا هر پیام صوتی منتظر شکستِ قطعیِ آن نماند (مگر تنها گزینه باشد).
- کلاینت تک‌تأمین‌کننده (avalai_audio.transcribe_audio) دست‌نخورده ماند؛ این
  سرویس فقط ترتیب/انتخاب/گزارش را مدیریت می‌کند.
"""
from __future__ import annotations

import ast
import json
import time
from urllib.parse import urlparse

from avalai_audio import AvalAIError, transcribe_audio
from services.ai_error_classifier import classify_provider_error, status_suffix

# مدارشکن — مدت کنار گذاشتن تأمین‌کننده پس از خطای اعتبار/احراز هویت
STT_PROVIDER_COOLDOWN_SECONDS = 600
_PROVIDER_COOLDOWN = {}  # label -> epoch seconds تا پایان مهلت

_KIND_MARK = {
    'credit': '💳',
    'auth': '🔑',
    'rate': '⏳',
    'server': '🛠',
    'timeout': '⏱',
    'network': '🌐',
    'skipped': '⏭',
    'other': '❓',
}

STT_CREDIT_HELP_FA = (
    '💡 راه حل:\n'
    '۱) اعتبار سرویس اصلی را شارژ کن (panel.avalai.ir) — به‌صورت خودکار دوباره فعال می‌شود؛\n'
    '۲) یا یک تأمین‌کننده پشتیبان رایگان/ارزان (مثل Groq) را در STT_FALLBACK_PROVIDERS\n'
    '    داخل /root/PishroSelfData/config.py اضافه کن و سرویس را ری‌استارت کن.\n'
    '    نمونه و راهنمای کامل: فایل STT_FALLBACK_SETUP_FA.txt داخل پوشه برنامه.'
)


class SttFallbackError(AvalAIError):
    """خطای نهایی تبدیل صوت پس از امتحان کل زنجیره (پیام فارسی آماده نمایش)."""


def _label_from_url(url):
    try:
        target = url if '://' in url else 'https://' + url
        host = urlparse(target).netloc
    except Exception:
        host = ''
    return host or (url[:40] if url else '') or 'provider'


def parse_stt_fallback_providers(raw):
    """``STT_FALLBACK_PROVIDERS`` را به لیست تأمین‌کننده تبدیل می‌کند.

    ورودی‌های پذیرفته: رشته خالی/None، JSON، و نمایش لیست پایتون (ast).
    ورودی خراب هرگز crash نمی‌کند → [] (رفتار = بدون پشتیبان، مثل قبل).
    هر ورودی باید حداقل base_url و api_key داشته باشد.
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        entries = list(raw)
    else:
        text = str(raw).strip()
        if not text:
            return []
        entries = None
        try:
            data = json.loads(text)
            if isinstance(data, list):
                entries = data
        except Exception:
            entries = None
        if entries is None:
            try:
                data = ast.literal_eval(text)
                if isinstance(data, (list, tuple)):
                    entries = list(data)
            except Exception:
                return []
    providers = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        base_url = str(entry.get('base_url') or '').strip()
        api_key = str(entry.get('api_key') or '').strip()
        if not base_url or not api_key:
            continue
        model = str(entry.get('model') or 'whisper-1').strip() or 'whisper-1'
        label = str(entry.get('label') or '').strip() or _label_from_url(base_url)
        providers.append(
            {'base_url': base_url, 'api_key': api_key, 'model': model, 'label': label}
        )
    return providers


def build_stt_chain(api_key, base_url, model, fallback_raw):
    """زنجیره‌ی نهایی: AvalAI (اگر کلید دارد) + پشتیبان‌های بدون تکرار."""
    chain = []
    primary_key = str(api_key or '').strip()
    if primary_key:
        chain.append(
            {
                'base_url': str(base_url or '').strip(),
                'api_key': primary_key,
                'model': str(model or '').strip() or 'whisper-1',
                'label': 'AvalAI',
            }
        )
    for provider in parse_stt_fallback_providers(fallback_raw):
        signature = (provider['base_url'].rstrip('/').lower(), provider['model'].lower())
        if any(
            (p['base_url'].rstrip('/').lower(), p['model'].lower()) == signature
            for p in chain
        ):
            continue
        chain.append(provider)
    return chain


def _cooldown_active(label):
    until = _PROVIDER_COOLDOWN.get(label)
    if not until:
        return False
    if time.time() >= until:
        _PROVIDER_COOLDOWN.pop(label, None)
        return False
    return True


def _mark_provider_failed(label, kind):
    if kind in ('credit', 'auth'):
        _PROVIDER_COOLDOWN[label] = time.time() + STT_PROVIDER_COOLDOWN_SECONDS


def clear_provider_cooldowns():
    """پاک‌کردن حافظه مدارشکن (برای تست‌ها و ری‌ست دستی)."""
    _PROVIDER_COOLDOWN.clear()


def format_stt_failure(attempts):
    """خلاصه فارسی تلاش‌ها + راهنمای عملی (برای پیام نهایی کاربر)."""
    lines = []
    saw_credit = False
    for label, kind, detail in attempts:
        mark = _KIND_MARK.get(kind, '❓')
        lines.append(f'{mark} {label}: {detail}')
        if kind == 'credit':
            saw_credit = True
    body = '\n'.join(lines)
    if saw_credit:
        body += '\n\n' + STT_CREDIT_HELP_FA
    return body


async def transcribe_with_fallback(
    file_path,
    *,
    chain,
    timeout_seconds,
    mime_type=None,
):
    """روی زنجیره امتحان می‌کند؛ اولین موفقیت برمی‌گردد: (متن، برچسب سرویس).

    شکست همه → SttFallbackError با خلاصه فارسی هر تلاش (+ راهنمای اعتبار).
    """
    if not chain:
        raise SttFallbackError(
            'هیچ سرویس تبدیل صوتی پیکربندی نشده است؛ AVALAI_API_KEY را در '
            'کانفیگ وارد کن یا تأمین‌کننده پشتیبان را در STT_FALLBACK_PROVIDERS بگذار.'
        )
    attempts = []
    last_status = None
    headline_status = None  # اگر خطای اعتبار در زنجیره بود، کد همان نماینده باشد
    for provider in chain:
        label = provider['label']
        others_alive = any(
            other is not provider and not _cooldown_active(other['label'])
            for other in chain
        )
        if _cooldown_active(label) and others_alive:
            attempts.append(
                (label, 'skipped', 'به‌دلیل خطای قبلی موقتاً کنار گذاشته شد (مدارشکن)')
            )
            continue
        try:
            text = await transcribe_audio(
                file_path,
                api_key=provider['api_key'],
                base_url=provider['base_url'],
                model=provider['model'],
                timeout_seconds=timeout_seconds,
                mime_type=mime_type,
            )
            _PROVIDER_COOLDOWN.pop(label, None)
            return text, label
        except AvalAIError as exc:
            status = getattr(exc, 'status_code', None)
            kind, fa = classify_provider_error(str(exc), status)
            _mark_provider_failed(label, kind)
            attempts.append((label, kind, fa + status_suffix(status)))
            if kind == 'credit' and headline_status is None:
                headline_status = status
            last_status = status if status is not None else last_status
    final_status = headline_status if headline_status is not None else last_status
    raise SttFallbackError(format_stt_failure(attempts), status_code=final_status)
