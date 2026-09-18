# -*- coding: utf-8 -*-
"""تست‌های API پایدار زمان تهران + فونت‌های ساعت شناور + نما سرویس Resend — v0.09.17.

پیش‌زمینه (باگ مالک):
    TypeError: get_tehran_time() takes 0 positional arguments but 1 was given
ریشه: تعریف بدون پارامتر در config.py در حالی که ۶ فراخوانی (inline.py و
self.py) رشته فرمت strftime می‌فرستند. راه‌حل منتخب مالک: آرگومان اختیاری
و backward compatible — هیچ فراخوانی‌ای تغییر نمی‌کند.
"""
import re
from datetime import datetime, time as time_type
from zoneinfo import ZoneInfo

import pytest

import config
from config import CLOCK_FONTS, get_tehran_datetime, get_tehran_time


# ---------------------------------------------------------- get_tehran_time
def test_no_arg_returns_time_object():
    """سبک قدیمی (db.py / bot/core.py): خروجی datetime.time بماند."""
    value = get_tehran_time()
    assert isinstance(value, time_type)


def test_format_string_returns_formatted_text():
    """سبک پنل (inline.py:427): get_tehran_time('%H:%M:%S') رشته فرمت‌شده."""
    value = get_tehran_time('%H:%M:%S')
    assert isinstance(value, str)
    assert re.fullmatch(r'\d{2}:\d{2}:\d{2}', value)


@pytest.mark.parametrize('fmt,pattern', [
    ('%H:%M:%S', r'\d{2}:\d{2}:\d{2}'),
    ('%H:%M', r'\d{2}:\d{2}'),
])
def test_all_repo_caller_formats(fmt, pattern):
    """دقیقاً همان فرمت‌هایی که در ریپو صدا زده می‌شوند."""
    assert re.fullmatch(pattern, get_tehran_time(fmt))


def test_invalid_fmt_raises_clear_typeerror():
    """آرگومان نامعتبر: پیام واضح به‌جای خطای مبهم «۰ آرگومان»."""
    with pytest.raises(TypeError) as exc:
        get_tehran_time(123)
    assert 'strftime' in str(exc.value)


def test_tehran_timezone_offset_is_0330():
    """هر دو API در منطقه Asia/Tehran (UTC+03:30) باشند."""
    probe = datetime.now(ZoneInfo('Asia/Tehran'))
    assert probe.utcoffset().total_seconds() == 3.5 * 3600
    assert get_tehran_datetime().utcoffset() == probe.utcoffset()


def test_get_tehran_datetime_unchanged():
    """گارد رگرسیون: get_tehran_datetime همان رفتار قبلی."""
    value = get_tehran_datetime()
    assert isinstance(value, datetime)
    assert value.tzinfo is not None


# ------------------------------------------------------------- CLOCK_FONTS
def test_clock_fonts_restored_not_empty():
    """فونت‌های ساعت نباید خالی باشند (KeyError منو/clock_updater)."""
    assert isinstance(CLOCK_FONTS, dict)
    assert len(CLOCK_FONTS) >= 2


def test_clock_fonts_default_key_one_present():
    """db.py مقادیر نامعتبر را به کلید ۱ برمی‌گرداند؛ پس کلید ۱ الزامی است."""
    assert 1 in CLOCK_FONTS


def test_clock_fonts_structure():
    """هر عضو: کلید عددی مثبت و تاپل (نام نمایشی، تابع تبدیل)."""
    for key, item in CLOCK_FONTS.items():
        assert isinstance(key, int) and key >= 1
        assert isinstance(item, tuple) and len(item) == 2
        name, func = item
        assert isinstance(name, str) and name.strip()
        assert callable(func)


def test_clock_font_functions_transform_hhmm():
    """ورودی واقعی clock_updater رشته «HH:MM» است؛ خروجی غیرخالی."""
    for _key, (_name, func) in CLOCK_FONTS.items():
        out = func('12:34')
        assert isinstance(out, str) and out.strip()


def test_clock_font_persian_digits():
    """فونت فارسی باید ارقام فارسی بدهد."""
    _name, func = CLOCK_FONTS[2]
    assert func('12:34') == '۱۲:۳۴'


def test_clock_fonts_protected_from_persistent_override():
    """CLOCK_FONTS کد است؛ نباید از فایل پایدار بازنویسی شود."""
    assert 'CLOCK_FONTS' in config._NON_PERSISTENT_KEYS


# ---------------------------------------- نما سرویس ارسال دوباره (spec مالک)
def test_premium_resend_service_facade():
    """نقطه ورود رسمی services/premium_resend_service.py بدون منطق تکراری."""
    from services import premium_resend_service as svc
    from services.emoji_resend_manager import EmojiResendManager
    assert svc.PremiumResendService is EmojiResendManager
    assert callable(svc.install_premium_resend_service)
    assert callable(svc.uninstall_premium_resend_service)
    assert callable(svc.get_service)


def test_premium_resend_service_reexports_constants():
    from services import premium_resend_service as svc
    assert svc.SEND_ATTEMPTS >= 1
    assert svc.RECENT_SENT_LIMIT >= 1
    assert svc.CUSTOM_ENTITY_NAME == 'MessageEntityCustomEmoji'
