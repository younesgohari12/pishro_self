# -*- coding: utf-8 -*-
"""پیکربندی PishroSelf — نسخه Loader (راه ۱: دیتای پایدار بیرون از سورس)

این فایل فقط «بارگذار» است و خودش هیچ مقدار حساسی نگه نمی‌دارد:

۱) ریشه ثابت داده‌ها همیشه بیرون از پوشه سورس است:
   - اجرای مالک (root):  /root/PishroSelfData   (DATA_DIR و DB_DIR همیشه همین)
   - سایر حساب‌ها:       <home>/PishroSelfData
   هیچ دیتایی (db، سشن، آپلود، کش، بکاپ، لاگ) داخل پوشه سورس ذخیره نمی‌شود.

۲) تنظیمات حساس و مالک‌قابل‌ویرایش فقط از فایل پایدار خوانده می‌شود:
       /root/PishroSelfData/config.py
   این فایل «تنها منبع حقیقت» اعتبارنامه‌هاست (API_ID، API_HASH، BOT_TOKEN و
   بقیه تنظیمات) و آپدیت‌های نسخه جدید هرگز آن را بازنویسی نمی‌کنند؛ بنابراین
   نصب نسخه جدید بدون هیچ تغییر دستی کار می‌کند.
   خواندن این فایل امن است: فقط انتساب‌های ساده لیترالی با AST خوانده می‌شود و
   هیچ کدی از داخل آن اجرا نمی‌شود (هم‌سو با قرارداد hardening v0.09.7).

۳) قرارداد hardening حفظ شده: پیکربندی فقط پایتونی است؛ ENV هرگز اولویت
   ندارد و validate_runtime_config() با ساختار سالم، خالی برمی‌گردد.
"""
import ast
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# ================================================== مسیرها
TEHRAN_TZ = ZoneInfo('Asia/Tehran')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _resolve_data_root():
    """ریشه ثابت داده‌ها: همیشه بیرون از پوشه سورس.

    اجرای root (سرویس مالک) همیشه /root/PishroSelfData را برمی‌گرداند تا
    DATA_DIR و DB_DIR مستقل از محیط اجرا ثابت بمانند؛ سایر حساب‌ها دیتای
    خودشان را در خانه حساب دارند. تست‌ها هم (با جایگزینی Path.home در
    conftest) هرگز به دیتای واقعی دست نمی‌زنند.
    """
    home = Path.home()
    try:
        if os.geteuid() == 0 and str(home) != '/root':
            home = Path('/root')
    except AttributeError:
        pass
    return str(home / 'PishroSelfData')


DATA_DIR = _resolve_data_root()
DB_DIR = os.path.join(DATA_DIR, 'db')
LOG_DIR = os.path.join(DATA_DIR, 'logs')
SESSIONS_DIR = os.path.join(DATA_DIR, 'sessions')
UPLOAD_DIR = os.path.join(DATA_DIR, 'upload')
MESSAGE_SAVE_CACHE_DIR = os.path.join(DATA_DIR, 'message_cache')
backup_dir = os.path.join(DATA_DIR, 'backup')

# ================================================== ۱) اعتبارنامه
# ❗ این مقادیر فقط «پیش‌فرض امن» هستند؛ در اجرای واقعی از فایل پایدار
#    /root/PishroSelfData/config.py خوانده و جایگزین می‌شوند:
#    API_ID و API_HASH ← از my.telegram.org → API development tools
#    BOT_TOKEN         ← توکن بات پنل از @BotFather
#    INLINE_BOT_TOKEN / INLINE_USERNAME ← بات اینلاین (اختیاری ولی توصیه‌شده)
#    سشن اکانت ← فایل/رشته StringSession در sessions/ همان ریشه دیتا
API_ID = 0
API_HASH = ''
BOT_TOKEN = ''
INLINE_BOT_TOKEN = ''
INLINE_USERNAME = 'test_inline_bot'  # ← در فایل پایدار یوزرنیم بات اینلاین خودت را بگذار (بدون @)

# ================================================== ۲) مالک / ادمین
# شناسه عددی مالک (در فایل پایدار قابل تغییر است)
ADMIN_ID = 8359698350
ADMIN_LOG_IDS = [8359698350]          # مقصد لاگ‌های فارسی سیستم
LOG_LEVEL = 'ERROR'   # پیش‌فرض پروژه؛ لاگ‌های فارسی با فلگ‌های DEBUG جدا کنترل می‌شوند

# ================================================== ۳) ایموجی ویژه (Premium)
PREMIUM_EMOJI_ENABLED = True          # کلید اصلی سیستم ایموجی ویژه
PREMIUM_EMOJI_MODE = 'round_robin'    # round_robin | random
PREMIUM_EMOJI_STRICT_MODE = True      # حالت سخت‌گیرانه Entity
PREMIUM_EMOJI_CONVERTER_ENABLED = True  # تبدیل قبل از ارسال
PREMIUM_EMOJI_OUTGOING_FIX = True     # هندلر پیام‌های خروجی
PREMIUM_EMOJI_RESEND_MODE = True      # 🔁 ارسال دوباره: Delete + New Send
PREMIUM_EMOJI_RESEND_DEBUG = True
PREMIUM_EMOJI_LOG_LEVEL = 'ERROR'   # پیش‌فرض پروژه؛ لاگ‌های فارسی با فلگ‌های DEBUG جدا کنترل می‌شوند
PREMIUM_EMOJI_DEBUG = True
PREMIUM_EMOJI_STYLE = 'auto'
CUSTOM_EMOJI_DEBUG = True

# ================================================== ۴) پیام عدم حضور (Away)
AWAY_BYPASS_PREMIUM = True            # 🔒 استقلال کامل: پاسخ Away هرگز وارد Premium نمی‌شود
AWAY_RESET_HOURS = 0                  # 0 = فقط ریست دستی (.عدم_حضور خاموش) یا سشن جدید
AWAY_DEBUG = True
AWAY_LOG_LEVEL = 'ERROR'   # پیش‌فرض پروژه؛ لاگ‌های فارسی با فلگ‌های DEBUG جدا کنترل می‌شوند

# ================================================== ۵) مدیریت وضعیت (Presence)
PRESENCE_DEBUG = True                 # لاگ فارسی [مدیریت وضعیت]
PRESENCE_LOG_LEVEL = 'ERROR'   # پیش‌فرض پروژه؛ لاگ‌های فارسی با فلگ‌های DEBUG جدا کنترل می‌شوند
STATE_DEBUG = True

# ================================================== ۶) ترجمه
TRANSLATE_ENABLED = True
SELF_TRANSLATE_ENABLED = True
SELF_TRANSLATE_PROVIDER = 'google'
SELF_TRANSLATE_MODEL = ''
SELF_TRANSLATE_MAX_CHARS = 3000
GOOGLE_TRANSLATE_API_KEY = ''

# ================================================== ۷) جستجو
SEARCH_API_PROVIDER = 'none'          # none | google | bing | ...
SEARCH_API_KEY = ''
SEARCH_API_URL = ''
SEARCH_HTTP_TIMEOUT = 10
SEARCH_MAX_RESULTS = 5

# ================================================== ۸) هوش مصنوعی (AI)
AI_MODEL = 'gpt-4o-mini'
AI_SYSTEM_PROMPT = 'You are a helpful assistant.'
AI_MAX_TOKENS = 1024
AI_TEMPERATURE = 0.7
AI_CACHE_TTL = 300
AI_WEB_CACHE_TTL = 600
AI_RATE_LIMIT_PER_MINUTE = 10

# ================================================== ۹) آوالای (صدا/تصویر)
AVALAI_API_KEY = ''
AVALAI_BASE_URL = 'https://api.avalai.ir/v1'
AVALAI_REQUEST_TIMEOUT = 60
AVALAI_STT_MODEL = 'whisper-1'
AVALAI_TTS_MODEL = 'tts-1'
AVALAI_AUDIO_MAX_BYTES = 20 * 1024 * 1024
AVALAI_TTS_MAX_BYTES = 20 * 1024 * 1024
AVALAI_TTS_MAX_CHARS = 4096

# ================================================== ۱۰) ارز دیجیتال
CRYPTO_ENABLED = True
SELF_CRYPTO_ENABLED = True
COINGECKO_API_BASE = 'https://api.coingecko.com/api/v3'
COINGECKO_API_KEY = ''
COINGECKO_API_KEY_TYPE = 'demo'
NOBITEX_API_BASE = 'https://api.nobitex.ir'
CRYPTO_CACHE_TTL = 60
CRYPTO_COIN_LIST_TTL = 3600
CRYPTO_HTTP_TIMEOUT = 15

# ================================================== ۱۱) اشتراک / نقل‌وانتقال
TRIAL_DURATION_HOURS = 24
MIN_TRANSFER = 10
TRANSFER_FEE_PERCENT = 2
MIN_DIAMOND = 10
MAX_DIAMOND = 100000
DIAMOND_STEP = 10
PRICE_PER_DIAMOND = 5000
SELF_START_COST = 0
REF_REWARD = 50
BILL_DIAMONDS_PER_HOUR = 1

# ================================================== ۱۲) نگه‌داری و امنیت
BACKUP_INTERVAL_HOURS = 6
MAX_DB_FILE_SIZE = 50 * 1024 * 1024
MAX_DELETE_COUNT = 500
MAX_SPAM_COUNT = 100
DELETE_CHUNK_SIZE = 50
DELETE_DELAY = 1.0
SPAM_DELAY = 1.5
UPDATE_INTERVAL = 5

# ================================================== ۱۳) تبچی
TABCHI_SEND_DELAY_SECONDS = 3
TABCHI_SCHEDULER_INTERVAL = 60

# ================================================== ۱۴) ساعت شناور
CLOCK_FONTS = {}

# ================================================== بارگذار تنظیمات پایدار
# کلیدهایی که هرگز نباید از فایل پایدار بازنویسی شوند (مسیرها هویت معماری‌اند
# و همیشه به ریشه ثابت دیتا اشاره می‌کنند).
_NON_PERSISTENT_KEYS = frozenset({
    'BASE_DIR', 'DATA_DIR', 'DB_DIR', 'LOG_DIR', 'SESSIONS_DIR',
    'UPLOAD_DIR', 'MESSAGE_SAVE_CACHE_DIR', 'backup_dir', 'TEHRAN_TZ',
})

# در پروسه تست هرگز فایل پایدار خوانده نمی‌شود (جداسازی کامل تست از دیتای واقعی).
_IN_TEST_RUNTIME = 'pytest' in sys.modules

PERSISTENT_CONFIG_PATH = os.path.join(DATA_DIR, 'config.py')
PERSISTENT_SETTINGS_LOADED = False
_PERSISTENT_LOADED_KEYS = []
_PERSISTENT_SKIPPED_KEYS = []


def _load_persistent_settings(path):
    """خواندن امن فایل پایدار: فقط انتساب Name = literal، بدون اجرای هیچ کدی.

    کلید ناشناخته نادیده گرفته می‌شود؛ ناسازگاری نوع کلید، آن کلید را رد
    می‌کند (پیش‌فرض امن حفظ می‌شود) و نامش برای گزارش ثبت می‌شود.
    """
    loaded, skipped = [], []
    try:
        tree = ast.parse(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, SyntaxError, ValueError):
        return loaded, skipped
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            continue
        name = node.targets[0].id
        if name.startswith('_') or name in _NON_PERSISTENT_KEYS or name not in globals():
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, SyntaxError, TypeError):
            skipped.append(name)
            continue
        current = globals()[name]
        if isinstance(current, (str, int, float, bool, list, dict)) and type(value) is type(current):
            globals()[name] = value
            loaded.append(name)
        elif name in ('API_ID', 'ADMIN_ID') and isinstance(value, str) and value.strip().isdigit():
            globals()[name] = int(value.strip())
            loaded.append(name)
        else:
            skipped.append(name)
    return loaded, skipped


if not _IN_TEST_RUNTIME and os.path.isfile(PERSISTENT_CONFIG_PATH):
    _PERSISTENT_LOADED_KEYS, _PERSISTENT_SKIPPED_KEYS = _load_persistent_settings(PERSISTENT_CONFIG_PATH)
    PERSISTENT_SETTINGS_LOADED = bool(_PERSISTENT_LOADED_KEYS)
    if 'ADMIN_ID' in _PERSISTENT_LOADED_KEYS and 'ADMIN_LOG_IDS' not in _PERSISTENT_LOADED_KEYS:
        ADMIN_LOG_IDS = [ADMIN_ID]

# ================================================== تنظیمات per-user
_USER_DEFAULTS = {
    'PREMIUM_EMOJI_CONVERTER_ENABLED': True,
    'PREMIUM_EMOJI_MODE': 'round_robin',
    'PREMIUM_EMOJI_RESEND_MODE': True,
}

_USER_CONFIG_KEYS = [
    'PREMIUM_EMOJI_CONVERTER_ENABLED', 'PREMIUM_EMOJI_MODE',
    'PREMIUM_EMOJI_RESEND_MODE', 'AWAY_RESET_HOURS',
]

# کلیدهای مالک‌قابل‌ویرایش که در فایل پایدار نگه‌داری می‌شوند؛ نصب/مهاجرت
# تازه این‌ها را در /root/PishroSelfData/config.py می‌نویسد و از آن پس
# «تنظیمات ذخیره‌شده» همیشه بر پیش‌فرض نسخه جدید اولویت دارد.
_PERSISTENT_SEED_KEYS = (
    # اعتبارنامه و مالک
    'API_ID', 'API_HASH', 'BOT_TOKEN', 'INLINE_BOT_TOKEN', 'INLINE_USERNAME',
    'ADMIN_ID', 'ADMIN_LOG_IDS', 'LOG_LEVEL',
    # ایموجی ویژه
    'PREMIUM_EMOJI_ENABLED', 'PREMIUM_EMOJI_MODE', 'PREMIUM_EMOJI_STRICT_MODE',
    'PREMIUM_EMOJI_CONVERTER_ENABLED', 'PREMIUM_EMOJI_OUTGOING_FIX',
    'PREMIUM_EMOJI_RESEND_MODE', 'PREMIUM_EMOJI_STYLE',
    # عدم حضور و وضعیت
    'AWAY_RESET_HOURS',
    # ترجمه / جستجو / هوش مصنوعی / آوالای
    'TRANSLATE_ENABLED', 'SELF_TRANSLATE_ENABLED', 'SELF_TRANSLATE_PROVIDER',
    'SELF_TRANSLATE_MODEL', 'SELF_TRANSLATE_MAX_CHARS', 'GOOGLE_TRANSLATE_API_KEY',
    'SEARCH_API_PROVIDER', 'SEARCH_API_KEY', 'SEARCH_API_URL',
    'SEARCH_HTTP_TIMEOUT', 'SEARCH_MAX_RESULTS',
    'AI_MODEL', 'AI_SYSTEM_PROMPT', 'AI_MAX_TOKENS', 'AI_TEMPERATURE',
    'AI_CACHE_TTL', 'AI_WEB_CACHE_TTL', 'AI_RATE_LIMIT_PER_MINUTE',
    'AVALAI_API_KEY', 'AVALAI_BASE_URL', 'AVALAI_REQUEST_TIMEOUT',
    'AVALAI_STT_MODEL', 'AVALAI_TTS_MODEL', 'AVALAI_AUDIO_MAX_BYTES',
    'AVALAI_TTS_MAX_BYTES', 'AVALAI_TTS_MAX_CHARS',
    # ارز دیجیتال
    'CRYPTO_ENABLED', 'SELF_CRYPTO_ENABLED', 'COINGECKO_API_BASE',
    'COINGECKO_API_KEY', 'COINGECKO_API_KEY_TYPE', 'NOBITEX_API_BASE',
    'CRYPTO_CACHE_TTL', 'CRYPTO_COIN_LIST_TTL', 'CRYPTO_HTTP_TIMEOUT',
    # اشتراک / اقتصاد
    'TRIAL_DURATION_HOURS', 'MIN_TRANSFER', 'TRANSFER_FEE_PERCENT',
    'MIN_DIAMOND', 'MAX_DIAMOND', 'DIAMOND_STEP', 'PRICE_PER_DIAMOND',
    'SELF_START_COST', 'REF_REWARD', 'BILL_DIAMONDS_PER_HOUR',
    # نگه‌داری / امنیت / تبچی
    'BACKUP_INTERVAL_HOURS', 'MAX_DB_FILE_SIZE', 'MAX_DELETE_COUNT',
    'MAX_SPAM_COUNT', 'DELETE_CHUNK_SIZE', 'DELETE_DELAY', 'SPAM_DELAY',
    'UPDATE_INTERVAL', 'TABCHI_SEND_DELAY_SECONDS', 'TABCHI_SCHEDULER_INTERVAL',
)


def get_tehran_time():
    return datetime.now(TEHRAN_TZ).time()


def get_tehran_datetime():
    return datetime.now(TEHRAN_TZ)


def _apply_days(delta_days):
    return datetime.now(TEHRAN_TZ) + timedelta(days=delta_days)


def user_config_values():
    """بذر تنظیمات پایدار برای نصب/مهاجرت؛ مقادیر بارگذاری‌شده حفظ می‌شوند."""
    values = {name: globals()[name] for name in _PERSISTENT_SEED_KEYS if name in globals()}
    return values


def release_config_updates():
    return {}


def premium_converter_config_updates():
    return {'PREMIUM_EMOJI_CONVERTER_ENABLED': True,
            'PREMIUM_EMOJI_MODE': 'strict'}


def resend_mode_config_updates():
    return {'PREMIUM_EMOJI_RESEND_MODE': True}


def validate_runtime_config():
    """اعتبارسنجی هنگام اجرا — قرارداد پروژه: با مقادیر پیش‌فرض خالی می‌گذرد.

    سلف در بوت، نبود اعتبارنامه را خودش تشخیص می‌دهد و راهنمای
    راه‌اندازی نشان می‌دهد؛ این تابع فقط مشکلات ساختاری را برمی‌گرداند.
    """
    return []
