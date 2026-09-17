"""Shared Telegram UI helpers."""
import inspect

from telethon import Button


ENABLE_NATIVE_COLORS = True
STYLE_ALIASES = {
    'primary': 'primary', 'success': 'success', 'danger': 'danger',
    'info': 'primary', 'warning': None, 'secondary': None, 'default': None,
}
FALLBACK_STYLE_ICONS = {
    'primary': '🔵', 'success': '🟢', 'danger': '🔴', 'info': '🔵',
    'warning': '🟠', 'secondary': '⚪️',
}


def _supports_style(method):
    if not ENABLE_NATIVE_COLORS:
        return False
    try:
        return 'style' in inspect.signature(method).parameters
    except Exception:
        return False


def _label(text, style, icon, method):
    if icon and not _supports_style(method):
        marker = FALLBACK_STYLE_ICONS.get(style)
        if marker:
            return f"{marker} {text}"
    return text


def inline_button(text, data, style='primary', icon=True):
    if isinstance(data, str):
        data = data.encode('utf-8')
    label = _label(text, style, icon, Button.inline)
    native_style = STYLE_ALIASES.get(style, style)
    if native_style and _supports_style(Button.inline):
        return Button.inline(label, data=data, style=native_style)
    return Button.inline(label, data=data)


def url_button(text, url, style='primary', icon=True):
    label = _label(text, style, icon, Button.url)
    native_style = STYLE_ALIASES.get(style, style)
    if native_style and _supports_style(Button.url):
        return Button.url(label, url, style=native_style)
    return Button.url(label, url)


def text_button(text, style='secondary', icon=True, **keyboard_options):
    label = _label(text, style, icon, Button.text)
    native_style = STYLE_ALIASES.get(style, style)
    if native_style and _supports_style(Button.text):
        return Button.text(label, style=native_style, **keyboard_options)
    return Button.text(label, **keyboard_options)


def request_phone_button(text, style='primary', icon=True, **keyboard_options):
    label = _label(text, style, icon, Button.request_phone)
    native_style = STYLE_ALIASES.get(style, style)
    if native_style and _supports_style(Button.request_phone):
        return Button.request_phone(label, style=native_style, **keyboard_options)
    return Button.request_phone(label, **keyboard_options)


BOT_MAIN_MENU = (
    "✨ **پنل اصلی**\n\n"
    "از اینجا حساب تلگرام را متصل کن و سلف‌بات را مدیریت کن.\n\n"
    "🔐 سشن فقط روی سرور خودت ذخیره می‌شود.\n"
    "⚡ بعد از اتصال، پنل سلف با دستور `.پنل` در دسترس است."
)
INSTALL_PHONE_PROMPT = (
    "🔐 **اتصال حساب تلگرام**\n\n"
    "📲 دکمه «ارسال شماره من» را بزنید.\n"
    "فقط اتصال حسابی که با آن در ربات هستید مجاز است.\n"
    "شماره دستی و مخاطب دیگر پذیرفته نمی‌شود."
)
LOGIN_CANCELLED = (
    "❌ **اتصال لغو شد**\n\n"
    "هیچ تغییری روی حساب انجام نشد. هر زمان خواستی دوباره از منوی اصلی شروع کن."
)
CODE_SENT_TEXT = (
    "📩 **کد تأیید ارسال شد**\n\n"
    "کد ۵ رقمی تلگرام را با صفحه‌کلید زیر وارد کن.\n"
    "⌫ حذف رقم  •  ✅ تأیید کد"
)
WAIT_2FA_TEXT = (
    "🔒 **تأیید دو مرحله‌ای**\n\n"
    "برای تکمیل اتصال، رمز دوم حساب تلگرام را ارسال کن."
)
LOGIN_SUCCESS_TEXT = (
    "✅ **اتصال با موفقیت انجام شد**\n\n"
    "سشن ذخیره و سرویس سلف فعال شد.\n"
    "برای باز کردن کنترل پنل، در هر چت `.پنل` را بفرست."
)
SELF_STARTED_TEXT = (
    "✅ **سلف‌بات فعال شد**\n\n"
    "🧭 `.پنل` — کنترل پنل\n"
    "📊 `.info` — وضعیت حساب\n"
    "📡 `.ping` — تست اتصال\n"
    "📨 `.اسپم 10 پیام` — ارسال تکراری\n"
    "🗑 `.حذف 10` — پاک‌سازی پیام‌ها\n"
    "🎙 `.تبدیل صوت به متن` — ریپلای روی ویس/صوت\n"
    "🔊 `.تبدیل متن به صوت` — ریپلای روی متن\n"
    "🔊 `.تبدیل متن به صوت سلام` — ساخت صوت از متن مستقیم\n"
    "🎙 بعدش یکی از صداها را بفرست: مرد / زن / جوان / پیر / رسمی / خودمونی\n"
    "⛔ `.کنسل` — توقف عملیات فعال"
)
