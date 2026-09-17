"""گزارش ادمین برای Premium Emoji Converter — فقط با متغیر محیطی.

قانون امنیتی مالک: هیچ توکنی داخل کد ذخیره نمی‌شود. توکن ربات گزارش فقط از
ENV خوانده می‌شود:

    PREMIUM_REPORT_BOT_TOKEN=123:abc...

مقصد گزارش ثابت است: ADMIN_REPORT_ID. اگر متغیر محیطی تنظیم نشده باشد، تمام
توابع no-op می‌شوند و هیچ درخواست شبکه‌ای زده نمی‌شود (پروژه بدون این قابلیت
همان رفتار قبل را دارد).
"""
from __future__ import annotations

import logging
import os

import aiohttp

logger = logging.getLogger(__name__)

# شناسه عددی مالک پروژه — راز نیست، مقصد گزارش.
ADMIN_REPORT_ID = 8359698350

TELEGRAM_API_BASE = 'https://api.telegram.org'
REPORT_TIMEOUT = 30


def report_token():
    """توکن فقط از ENV؛ هرگز از فایل/کد خوانده نمی‌شود."""
    return (os.environ.get('PREMIUM_REPORT_BOT_TOKEN') or '').strip()


def report_enabled():
    return bool(report_token())


async def send_report_text(text, *, chat_id=ADMIN_REPORT_ID):
    """ارسال متن گزارش با sendMessage؛ بدون توکن، کاری انجام نمی‌دهد."""
    token = report_token()
    if not token:
        logger.info('PREMIUM_REPORT_BOT_TOKEN تنظیم نشده؛ گزارش ارسال نشد.')
        return False
    if not text or not str(text).strip():
        return False
    url = f'{TELEGRAM_API_BASE}/bot{token}/sendMessage'
    payload = {
        'chat_id': chat_id,
        'text': str(text)[:4000],
        # None would be rejected by Telegram; omit parse_mode entirely.
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload,
                                    timeout=aiohttp.ClientTimeout(total=REPORT_TIMEOUT)) as resp:
                body = await resp.json(content_type=None)
                if not resp.ok or not body.get('ok'):
                    logger.warning('گزارش ادمین ارسال نشد (%s)', body.get('description'))
                    return False
        return True
    except Exception as exc:
        logger.warning('ارسال گزارش ادمین ناموفق بود (%s)', type(exc).__name__)
        return False


async def send_report_document(path, caption='', *, chat_id=ADMIN_REPORT_ID):
    """ارسال فایل (مثلاً ZIP نهایی) با sendDocument؛ بدون توکن، no-op."""
    token = report_token()
    if not token:
        logger.info('PREMIUM_REPORT_BOT_TOKEN تنظیم نشده؛ سند ارسال نشد.')
        return False
    import pathlib
    file_path = pathlib.Path(path)
    if not file_path.is_file() or file_path.stat().st_size == 0:
        logger.warning('فایل گزارش پیدا نشد: %s', file_path.name)
        return False
    url = f'{TELEGRAM_API_BASE}/bot{token}/sendDocument'
    try:
        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field('chat_id', str(chat_id))
            if caption:
                data.add_field('caption', str(caption)[:1000])
            data.add_field('document', file_path.open('rb'),
                           filename=file_path.name,
                           content_type='application/zip')
            async with session.post(url, data=data,
                                    timeout=aiohttp.ClientTimeout(total=300)) as resp:
                body = await resp.json(content_type=None)
                if not resp.ok or not body.get('ok'):
                    logger.warning('سند گزارش ارسال نشد (%s)', body.get('description'))
                    return False
        return True
    except Exception as exc:
        logger.warning('ارسال سند گزارش ناموفق بود (%s)', type(exc).__name__)
        return False
    finally:
        pass
