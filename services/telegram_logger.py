"""Telegram Admin Logger — گزارش زنده عملیات Premium Emoji و خطاهای مهم.

قانون امنیتی مالک: هیچ توکنی داخل کد ذخیره نمی‌شود. توکن ربات گزارش فقط از
ENV خوانده می‌شود:

    export PREMIUM_LOG_BOT_TOKEN='123:abc...'

اگر متغیر تنظیم نشده باشد، ارسال تلگرامی no-op می‌شود اما لاگ محلی ادامه
دارد (پروژه بدون این قابلیت همان رفتار قبل را دارد). مقصد پیام‌ها
``config.ADMIN_LOG_IDS`` است (پیش‌فرض: شناسه عددی مالک).

سطح‌بندی (OFF < ERROR < WARNING < INFO < DEBUG):
- ``config.LOG_LEVEL`` (پیش‌فرض ERROR) → کانال عمومی: send_log/send_error/
  send_warning (خطاهای RPC/Send/Edit/Database/Session و ...).
- ``config.PREMIUM_EMOJI_LOG_LEVEL`` (پیش‌فرض INFO) → کانال Premium Emoji:
  تبدیل موفق=INFO، fallback=WARNING، خطا=ERROR، بلوک debug=DEBUG.

ضد-اسپم: هر نوع رویداد حداقل‌فاصله ارسال دارد؛ رویدادهای تکراری در بازهٔ
فاصله شمرده می‌شوند و به‌صورت «(+N suppressed)» روی گزارش بعدی می‌آیند.
سقف سراسری ارسال هم داریم تا هیچ‌وقت برای پیام‌های عادی هزاران گزارش زده
نشود.

امنیت: هر پیام قبل از صف‌شدن با ``redact()`` پاک‌سازی می‌شود؛ BOT_TOKEN,
API_HASH, API_ID, SESSION, PASSWORD هرگز در پیام تلگرامی یا فایل لاگ ظاهر
نمی‌شوند. HTTP در یک thread پس‌زمینه انجام می‌شود تا مسیر ارسال پیام هرگز
مسدود یا کند نشود (بدون وابستگی به event loop).

لاگ محلی با rotation:
- ``logs/premium_emoji.log`` — رویدادهای Premium (INFO+) و بلوک‌های debug
- ``logs/error.log`` — خطاها (ERROR+)
هر دو 10MB با 5 نسخه پشتیبان.
"""
from __future__ import annotations

import logging
import os
import queue
import re
import threading
import time
import urllib.parse
import urllib.request
from logging.handlers import RotatingFileHandler

import config
from services.logging_service import redact as _base_redact

TELEGRAM_API_BASE = 'https://api.telegram.org'
SEND_TIMEOUT = 10
MAX_TEXT_CHARS = 3800
MAX_QUEUE = 200

# ---------------------------------------------------------------- levels
_LEVEL_NAMES = ('OFF', 'ERROR', 'WARNING', 'INFO', 'DEBUG')
_LEVEL_VALUE = {name: index for index, name in enumerate(_LEVEL_NAMES)}
_STD_LEVEL = {'DEBUG': logging.DEBUG, 'INFO': logging.INFO,
              'WARNING': logging.WARNING, 'ERROR': logging.ERROR}


def _level_value(name, default):
    """Parse a level name; unknown/None values fall back to ``default``."""
    try:
        return _LEVEL_VALUE[str(name or '').strip().upper()]
    except (KeyError, ValueError):
        return _LEVEL_VALUE[default]


def _channel_enabled(message_level, channel_level):
    return _level_value(message_level, 'ERROR') <= _level_value(channel_level, 'ERROR')


# ---------------------------------------------------------------- redaction
# الگوهای مخصوص این ماژول؛ redact پایه (bot token/api key/api hash/phone) هم
# اعمال می‌شود. SESSION مانند رشته‌های طولانی base64 شناخته و پاک می‌شود.
_SESSION_LIKE = re.compile(r'(?<![A-Za-z0-9_/+=-])[A-Za-z0-9_/+=-]{60,}'
                           r'(?![A-Za-z0-9_/+=-])')
_SECRET_ASSIGN = re.compile(
    r'(?i)\b(bot_?token|api_?hash|api_?id|session|password|passwd|pass|token'
    r'|secret|credential)\b(\s*[=:]\s*)(\S+)')


def redact(value) -> str:
    """پاک‌سازی کامل رازها؛ هرگز ردپای توکن/سشن/رمز باقی نمی‌ماند."""
    text = _base_redact(value)
    text = _SECRET_ASSIGN.sub(lambda m: f'{m.group(1)}{m.group(2)}[REDACTED]', text)
    text = _SESSION_LIKE.sub('[REDACTED-SESSION]', text)
    token = log_token()
    if token:
        # دفاع چندلایه: خود مقدار توکن ENV هرگز نباید دیده شود.
        text = text.replace(token, '[REDACTED-TOKEN]')
    return text


# ---------------------------------------------------------------- local logs
def _build_file_logger(name, filename):
    logger = logging.getLogger(f'pishro.telegram_logger.{name}')
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:
        return logger

    class _RedactingFormatter(logging.Formatter):
        def format(self, record):
            return redact(super().format(record))

    try:
        log_dir = os.path.join(getattr(config, 'DATA_DIR', os.getcwd()), 'logs')
        os.makedirs(log_dir, exist_ok=True)
        handler = RotatingFileHandler(
            os.path.join(log_dir, filename),
            maxBytes=10 * 1024 * 1024, backupCount=5, encoding='utf-8')
        handler.setFormatter(_RedactingFormatter(
            '%(asctime)s | %(levelname)s | %(message)s'))
        logger.addHandler(handler)
    except OSError:
        pass  # لاگ فایل هرگز نباید اجرای برنامه را متوقف کند
    return logger


_premium_file_log = _build_file_logger('premium', 'premium_emoji.log')
_error_file_log = _build_file_logger('error', 'error.log')


# ---------------------------------------------------------------- transport
def log_token():
    """توکن فقط از ENV؛ هرگز از فایل/کد خوانده نمی‌شود."""
    return (os.environ.get('PREMIUM_LOG_BOT_TOKEN') or '').strip()


def logging_enabled():
    return bool(log_token()) and bool(getattr(config, 'ADMIN_LOG_IDS', []))


_queue = queue.Queue(maxsize=MAX_QUEUE)
_worker_started = False
_worker_lock = threading.Lock()


def _worker():
    """صف مصرف می‌شود؛ هر خطا بلعیده می‌شود تا هیچ‌وقت crash نکند."""
    while True:
        item = _queue.get()
        try:
            if item is None:
                return
            _post(item)
        except Exception:  # noqa: BLE001 - logger must never raise
            pass
        finally:
            try:
                _queue.task_done()
            except Exception:  # noqa: BLE001
                pass


def _start_worker():
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True
        threading.Thread(target=_worker, name='telegram-logger',
                         daemon=True).start()


def _post(item):
    token = log_token()
    if not token:
        return
    for chat_id in item['chat_ids']:
        url = f'{TELEGRAM_API_BASE}/bot{token}/sendMessage'
        data = urllib.parse.urlencode(
            {'chat_id': int(chat_id), 'text': item['text']}).encode('ascii')
        request = urllib.request.Request(
            url, data=data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'})
        try:
            with urllib.request.urlopen(request, timeout=SEND_TIMEOUT) as resp:
                if 200 <= resp.status < 300:
                    continue
        except Exception:  # noqa: BLE001 - شبکه/محدودیت تلگرام نباید اثر بگذارد
            continue


def _enqueue(text, chat_ids):
    try:
        _queue.put_nowait({'text': text, 'chat_ids': chat_ids})
    except queue.Full:
        pass  # صف پر یعنی بار زیاد؛ گم‌شدن گزارش بهتر از فشار روی سیستم است
    _start_worker()


# ---------------------------------------------------------------- anti-spam
_MIN_INTERVALS = {
    'converted': 5.0,       # 🎨 Premium Emoji Converted
    'fallback': 60.0,       # ⚠️ Premium Emoji Fallback
    'premium_error': 30.0,  # ❌ Premium Emoji Error
    'post_fix': 5.0,        # 🔧 تزریق entity بعد از ارسال
    'debug': 3.0,           # [PREMIUM DEBUG]
    'generic': 30.0,        # send_log/error/warning عمومی
}
_GLOBAL_CAP_PER_MINUTE = 30
_GLOBAL_CAP_PER_HOUR = 300

_state_lock = threading.Lock()
_last_sent = {}      # kind -> monotonic time of last accepted send
_suppressed = {}     # kind -> count of coalesced events
_send_times = []     # global rate window (monotonic stamps)


def _anti_spam_ok(kind, now):
    with _state_lock:
        minimum = _MIN_INTERVALS.get(kind, 30.0)
        previous = _last_sent.get(kind)
        if previous is not None and now - previous < minimum:
            _suppressed[kind] = _suppressed.get(kind, 0) + 1
            return False
        _send_times[:] = [stamp for stamp in _send_times if now - stamp < 3600]
        if sum(1 for stamp in _send_times if now - stamp < 60) >= _GLOBAL_CAP_PER_MINUTE:
            _suppressed[kind] = _suppressed.get(kind, 0) + 1
            return False
        if len(_send_times) >= _GLOBAL_CAP_PER_HOUR:
            _suppressed[kind] = _suppressed.get(kind, 0) + 1
            return False
        _last_sent[kind] = now
        _send_times.append(now)
        return True


def _suppressed_suffix(kind):
    with _state_lock:
        count = _suppressed.pop(kind, 0)
    return f'\n(+{count} suppressed)' if count else ''


# ---------------------------------------------------------------- formatting
def _render(title, fields):
    lines = [title]
    for key, value in (fields or {}).items():
        if value is None:
            continue
        lines.append(f'{key}: {value}')
    return '\n'.join(lines)[:MAX_TEXT_CHARS]


def format_premium_debug(*, method, chat_id, original_text, detected, document_ids,
                         entity_count, client='Self',
                         final_entity='MessageEntityCustomEmoji'):
    """بلوک استاندارد [PREMIUM DEBUG] مطابق spec مالک."""
    emoji_list = '\n'.join(detected or []) or '-'
    ids = '\n'.join(str(value) for value in (document_ids or [])) or '-'
    return (
        '[PREMIUM DEBUG]\n'
        f'Client: {client}\n'
        f'Method: {method}\n'
        f'Chat ID: {chat_id}\n'
        f'Original Text: {original_text}\n'
        f'Detected Emoji: {emoji_list}\n'
        f'Created Custom Emoji Entities: {entity_count}\n'
        f'Document IDs: {ids}\n'
        f'Final Entity: {final_entity}'
    )


# ---------------------------------------------------------------- public API
def _chat_ids(chat_id=None):
    if chat_id is not None:
        return [chat_id]
    ids = getattr(config, 'ADMIN_LOG_IDS', []) or []
    return list(ids)


def send_log(title, fields=None, *, level='INFO', chat_id=None):
    """گزارش عمومی — کانال LOG_LEVEL (پیش‌فرض ERROR).

    سطح پیام باید <= سطح کانال باشد؛ با پیش‌فرض، فقط ERROR ارسال/ثبت می‌شود.
    """
    text = redact(_render(title, fields))
    if not _channel_enabled(level, getattr(config, 'LOG_LEVEL', 'ERROR')):
        return False
    if _STD_LEVEL.get(level, logging.INFO) >= logging.ERROR:
        _error_file_log.error('%s', text)
    if not logging_enabled():
        return False
    if not _anti_spam_ok('generic', time.monotonic()):
        return False
    _enqueue(text + _suppressed_suffix('generic'), _chat_ids(chat_id))
    return True


def send_error(title, fields=None, *, where=None, chat_id=None):
    """❌ گزارش خطا (File/Line/Error) — کانال عمومی؛ با پیش‌فرض همیشه عبور می‌کند."""
    fields = dict(fields or {})
    if where:
        fields.setdefault('File', where)
    return send_log(title, fields, level='ERROR', chat_id=chat_id)


def send_warning(title, fields=None, *, where=None, chat_id=None):
    """⚠️ هشدار عمومی — فقط وقتی LOG_LEVEL >= WARNING ارسال می‌شود."""
    fields = dict(fields or {})
    if where:
        fields.setdefault('File', where)
    return send_log(title, fields, level='WARNING', chat_id=chat_id)


def send_premium_event(title, fields=None, *, level='INFO', kind='converted',
                       chat_id=None):
    """🎨 گزارش کانال Premium Emoji — سطح مستقل PREMIUM_EMOJI_LOG_LEVEL.

    kind: converted | fallback | premium_error | post_fix | debug
    بلوک debug فقط وقتی PREMIUM_EMOJI_DEBUG=True ساخته می‌شود و در تلگرام
    فقط وقتی کانال روی DEBUG است ارسال می‌شود (لاگ محلی همیشه دارد).
    """
    text = redact(_render(title, fields))
    _premium_file_log.info('%s', text)  # premium_emoji.log — همیشه
    if kind == 'debug' and not getattr(config, 'PREMIUM_EMOJI_DEBUG', False):
        return False
    channel = getattr(config, 'PREMIUM_EMOJI_LOG_LEVEL', 'INFO')
    if not _channel_enabled(level, channel):
        return False
    if not logging_enabled():
        return False
    if not _anti_spam_ok(kind, time.monotonic()):
        return False
    _enqueue(text + _suppressed_suffix(kind), _chat_ids(chat_id))
    return True


def send_premium_debug_block(block_text, *, chat_id=None):
    """بلوک [PREMIUM DEBUG] — محلی همیشه؛ تلگرام فقط در سطح DEBUG."""
    return send_premium_event(block_text, None, level='DEBUG', kind='debug',
                              chat_id=chat_id)


def pending_count():
    """ابعاد صف (فقط برای تست/پایش)."""
    return _queue.qsize()


def reset_rate_state():
    """پاک‌کردن وضعیت ضد-اسپم (فقط تست‌ها)."""
    with _state_lock:
        _last_sent.clear()
        _suppressed.clear()
        _send_times.clear()
