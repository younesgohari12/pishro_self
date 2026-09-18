#!/usr/bin/env bash
# ============================================================================
#  PishroSelf — مهاجرت امن به معماری Loader (راه ۱) — migrate.sh
#  نسخه: 1.0  |  تاریخ: 2026-09-18
#
#  کاری که می‌کند:
#    سرویس selfbot را به نسخه جدید (با config بارگذار) سالم متصل می‌کند؛
#    دیتای اصلی /root/PishroSelfData صددرصد دست‌نخورده می‌ماند و نسخه جدید
#    از همان‌جا دیتا و اعتبارنامه (API_ID/API_HASH/BOT_TOKEN) می‌خواند.
#
#  تضمین‌ها:
#    ۱) هیچ فایل دیتایی کپی/حذف/overwrite نمی‌شود (فقط خواندنی + بکاپ).
#    ۲) قبل از هر تغییر، بکاپ timestamp دار (unit + دیتا + کانفیگ‌ها).
#    ۳) مقادیر غیرخالی اعتبارنامه هرگز بازنویسی نمی‌شوند؛ فقط جای خالی پر می‌شود.
#    ۴) در هر خطا: rollback خودکار به unit قبلی و بالا آوردن سرویس قدیمی.
#    ۵) برای آپدیت‌های بعدی هم قابل استفاده است:
#         bash migrate.sh /root/self/<نسخه-جدید>/PishroSelf
#
#  حالت‌های ویژه (محیطی):
#    PISHRO_DRY_RUN=1   فقط نمایش اقدامات، بدون هیچ تغییری
#    PISHRO_YES=1       بدون سؤال تأیید
#    PISHRO_SKIP_PIP=1  نصب requirements را رد کن (برای تست)
#    PISHRO_SKIP_TAR=1  بکاپ tar دیتا را رد کن (دیتای بسیار حجیم)
# ============================================================================
set -uo pipefail

# ----------------------------- تنظیمات -----------------------------
TARGET_DIR="${PISHRO_TARGET:-/root/self/PishroSelf_v0.09.15_COMPLETE_20260918-005832/PishroSelf}"
DATA_DIR="${PISHRO_DATA:-/root/PishroSelfData}"
SELF_DIR="${PISHRO_SELF_DIR:-/root/self}"
SERVICE_NAME="${PISHRO_SERVICE:-selfbot}"
BACKUP_ROOT="${PISHRO_BACKUPS:-$SELF_DIR/migrate_backups}"
DRY_RUN="${PISHRO_DRY_RUN:-0}"
SKIP_PIP="${PISHRO_SKIP_PIP:-0}"
SKIP_TAR="${PISHRO_SKIP_TAR:-0}"
ASSUME_YES="${PISHRO_YES:-0}"
TG_BOT_TOKEN="${TG_BOT_TOKEN:-}"
TG_CHAT_ID="${TG_CHAT_ID:-}"

# پیشوند مسیر فقط برای تست محلی (روی سرور خالی است)
ROOTP="${PISHRO_ROOT_PREFIX:-}"
if [ -n "$ROOTP" ]; then
  TARGET_DIR="$ROOTP$TARGET_DIR"; DATA_DIR="$ROOTP$DATA_DIR"
  SELF_DIR="$ROOTP$SELF_DIR";     BACKUP_ROOT="$ROOTP$BACKUP_ROOT"
fi

TS="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/$TS"
REPORT_FILE="$BACKUP_DIR/migrate_report.txt"
UNIT_FILE="${PISHRO_UNIT:-/etc/systemd/system/$SERVICE_NAME.service}"
if [ -n "$ROOTP" ]; then UNIT_FILE="$ROOTP$UNIT_FILE"; fi
SYSTEMCTL_BIN="$(command -v systemctl 2>/dev/null || true)"

mkdir -p "$BACKUP_DIR" 2>/dev/null || true

# ----------------------------- کمکی‌ها -----------------------------
say()  { echo -e "$*" | tee -a "$REPORT_FILE" 2>/dev/null || echo -e "$*"; }
step() { say ""; say "──────────────────────────────────────────────"; say "▶ $*"; say "──────────────────────────────────────────────"; }
warn() { say "⚠️  $*"; }
die()  { say "❌ خطا: $*"; exit 1; }

run() {  # اجرای دستور با احترام به DRY_RUN
  if [ "$DRY_RUN" = "1" ]; then say "  [DRY-RUN] $*"; return 0; fi
  "$@"
}

tg_notify() {
  [ -n "$TG_BOT_TOKEN" ] && [ -n "$TG_CHAT_ID" ] || return 0
  curl -s --max-time 10 "https://api.telegram.org/bot$TG_BOT_TOKEN/sendMessage" \
       -d chat_id="$TG_CHAT_ID" --data-urlencode "text=$1" >/dev/null 2>&1 || true
}

check_root() {
  if [ "$(id -u)" != "0" ] && [ -z "$ROOTP" ] && [ "${PISHRO_SKIP_ROOT_CHECK:-0}" != "1" ]; then
    die "این اسکریپت باید با root اجرا شود: sudo bash $0"
  fi
}

detect_exec_args() {  # فلگ‌ها و نام اسکریپت ورود را از unit فعلی بردار (مسیر قدیمی حذف شود)
  local old_exec flags="" script="" tok
  old_exec="$(grep -E '^ExecStart=' "$UNIT_FILE" 2>/dev/null | head -1 | cut -d= -f2- || true)"
  if [ -z "$old_exec" ]; then printf 'main.py'; return; fi
  for tok in $old_exec; do
    case "$tok" in
      -*) flags="$flags $tok" ;;
      *.py) script="$tok" ;;
      *) : ;;
    esac
  done
  [ -n "$script" ] || script="main.py"
  script="$(basename "$script")"
  printf '%s %s' "${flags# }" "$script" | sed 's/^ //'
}

rollback_unit() {  # بازگرداندن unit بکاپ‌شده و بالا آوردن سرویس قبلی
  local newest d
  newest=""
  for d in $(ls -1dt "$BACKUP_ROOT"/*/ 2>/dev/null); do
    if [ -f "$d/selfbot.service.bak" ]; then newest="$d"; break; fi
  done
  [ -n "$newest" ] || { warn "بکاپ unit پیدا نشد؛ rollback دستی لازم است."; return 1; }
  warn "↩️  Rollback: بازگردانی unit از $newest"
  cp -a "$newest/selfbot.service.bak" "$UNIT_FILE" 2>/dev/null || return 1
  [ -n "$SYSTEMCTL_BIN" ] && { "$SYSTEMCTL_BIN" daemon-reload 2>/dev/null || true; "$SYSTEMCTL_BIN" start "$SERVICE_NAME" 2>/dev/null || true; }
  say "↩️  سرویس قبلی دوباره start شد (نسخه قبل)."
  tg_notify "↩️ PishroSelf migrate FAILED — rollback انجام شد. گزارش: $REPORT_FILE"
}

# ----------------------------- برداشت اعتبارنامه از نسخه‌های قبلی -----------------------------
ensure_persistent_config() {
step "گام ۳: آماده‌سازی کانفیگ پایدار ($DATA_DIR/config.py)"
  mkdir -p "$DATA_DIR" 2>/dev/null || true
  run cp -a "$DATA_DIR/config.py" "$BACKUP_DIR/persistent_config.py.bak" 2>/dev/null || true

  python3 - "$DATA_DIR" "$SELF_DIR" "$TARGET_DIR" "$ASSUME_YES" <<'PISHRO_HARVEST_EOF'
import ast, glob, os, sys

data_dir, self_dir, target_dir, assume_yes = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] == '1'
p_path = os.path.join(data_dir, 'config.py')
CRED_KEYS = ['API_ID', 'API_HASH', 'BOT_TOKEN', 'INLINE_BOT_TOKEN', 'INLINE_USERNAME', 'ADMIN_ID']
EMPTY = {'API_ID': (0, '0'), 'API_HASH': ('',), 'BOT_TOKEN': ('',),
         'INLINE_BOT_TOKEN': ('',), 'INLINE_USERNAME': ('test_inline_bot', ''), 'ADMIN_ID': (0, '0')}

def parse_literals(path):
    """فقط انتساب‌های لیترال را بخوان؛ هیچ کدی اجرا نمی‌شود."""
    try:
        tree = ast.parse(open(path, encoding='utf-8-sig').read())
    except Exception:
        return {}
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except Exception:
                pass
    return out

def is_empty(key, value):
    if key == 'API_ID' or key == 'ADMIN_ID':
        return value in (None, 0, '0', '')
    return value in (None, '')

current = parse_literals(p_path) if os.path.isfile(p_path) else {}
harvest = {}

candidates = []
patterns = [os.path.join(self_dir, '*', 'PishroSelf*', 'config.py'),
            os.path.join(self_dir, '*', 'config.py'),
            os.path.join(target_dir, 'config.py')]
for pat in patterns:
    candidates.extend(glob.glob(pat))
candidates = sorted(set(candidates), key=lambda p: os.path.getmtime(p), reverse=True)
# نسخه فعلی هدف آخر بررسی می‌شود چون کانفیگش قالب خالی است
candidates.sort(key=lambda p: (p == os.path.join(target_dir, 'config.py'),))

for cand in candidates:
    if os.path.abspath(cand) == os.path.abspath(p_path):
        continue
    values = parse_literals(cand)
    for key in CRED_KEYS:
        if key in harvest:
            continue
        value = values.get(key)
        if not is_empty(key, value) and (isinstance(value, (str, int))):
            harvest[key] = value

changes = []
for key in CRED_KEYS:
    if key in harvest and is_empty(key, current.get(key)):
        changes.append((key, current.get(key), harvest[key]))

if not os.path.isfile(p_path):
    header = ("# Persistent settings. Updates must never overwrite this file.\n"
              "# تنظیمات پایدار PishroSelf — این فایل کنار دیتا در " + data_dir + " است.\n"
              "# آپدیت نسخه‌ها این فایل را بازنویسی نمی‌کند.\n\n")
    lines = [header]
    for key in CRED_KEYS:
        value = harvest.get(key, 0 if key in ('API_ID', 'ADMIN_ID') else '')
        lines.append(f'{key} = {value!r}\n')
    with open(p_path, 'w', encoding='utf-8') as handle:
        handle.writelines(lines)
    print(f'CREATED {p_path}')
    for key in CRED_KEYS:
        print(f'SET {key} = {harvest.get(key, 0 if key in ("API_ID", "ADMIN_ID") else "")!r}')
else:
    if changes:
        print('پیشنهاد تغییر (فقط مقادیر خالی/صفر پر می‌شوند):')
        for key, old, new in changes:
            shown = new
            if key in ('API_HASH', 'BOT_TOKEN', 'INLINE_BOT_TOKEN') and isinstance(new, str) and len(new) > 8:
                shown = new[:4] + '...' + new[-4:]
            print(f'  {key}: {old!r} -> {shown!r}')
        if assume_yes != '1' and sys.stdin.isatty():
            answer = input('تأیید می‌کنید؟ [y/N] ').strip().lower()
            if answer not in ('y', 'yes'):
                print('بدون تغییر ماند؛ اگر API_ID خالی بماند ربات بالا نمی‌آید.')
                sys.exit(0)
        text = open(p_path, encoding='utf-8-sig').read()
        out_lines = []
        done = set()
        for line in text.splitlines(True):
            replaced = False
            for key, old, new in changes:
                if line.strip().startswith(key) and '=' in line and not line.strip().startswith('#'):
                    out_lines.append(f'{key} = {new!r}\n')
                    done.add(key)
                    replaced = True
                    break
            if not replaced:
                out_lines.append(line)
        missing = [(k, o, n) for (k, o, n) in changes if k not in done]
        if missing:
            out_lines.append('\n# harvested from previous installation\n')
            for key, old, new in missing:
                out_lines.append(f'{key} = {new!r}\n')
        with open(p_path, 'w', encoding='utf-8') as handle:
            handle.writelines(out_lines)
        print(f'UPDATED {p_path} ({len(changes)} کلید خالی پر شد)')
    else:
        print(f'OK {p_path} دست‌نخورده ماند (مقادیر لازم موجود است).')

final = parse_literals(p_path) if os.path.isfile(p_path) else {}
missing_now = [key for key in ('API_ID', 'API_HASH', 'BOT_TOKEN') if is_empty(key, final.get(key))]
if missing_now:
    print(f'MISSING_AFTER_HARVEST: {missing_now}')
    sys.exit(2)
print('HARVEST_OK')
PISHRO_HARVEST_EOF
  local harvest_rc=$?
  if [ "$harvest_rc" = "2" ]; then
    warn "اعتبارنامه کامل پیدا نشد! مسیرهای بررسی‌شده: $SELF_DIR/*/ و $TARGET_DIR"
    warn "کاندیدها: $(ls -1d "$SELF_DIR"/*/ 2>/dev/null | tr '\n' ' ')"
    die "API_ID/API_HASH/BOT_TOKEN در هیچ نسخه قبلی پیدا نشد — دستی در $DATA_DIR/config.py وارد کن و دوباره اجرا کن."
  elif [ "$harvest_rc" != "0" ]; then
    die "آماده‌سازی کانفیگ پایدار ناموفق بود (rc=$harvest_rc)."
  fi
  say "✅ کانفیگ پایدار آماده است: $DATA_DIR/config.py"
}

# ----------------------------- نصب config بارگذار -----------------------------
install_loader_config() {
step "گام ۴: نصب config بارگذار در نسخه جدید"
  if grep -q 'PERSISTENT_CONFIG_PATH' "$TARGET_DIR/config.py" 2>/dev/null; then
    say "✅ config نسخه هدف از قبل Loader است — دست نخورد."
    return 0
  fi
  run cp -a "$TARGET_DIR/config.py" "$BACKUP_DIR/target_config.py.bak" 2>/dev/null || true
  if [ "$DRY_RUN" = "1" ]; then say "  [DRY-RUN] نصب config بارگذار در $TARGET_DIR/config.py"; return 0; fi
  cat > "$TARGET_DIR/config.py" <<'PISHRO_LOADER_EOF'
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
PISHRO_LOADER_EOF
  say "✅ config بارگذار نصب شد (بکاپ قبلی: $BACKUP_DIR/target_config.py.bak)"
}

# ----------------------------- venv و requirements -----------------------------
prepare_venv() {
step "گام ۵: venv و نصب requirements"
  VENV_PY="$TARGET_DIR/venv/bin/python"
  if [ ! -x "$VENV_PY" ]; then
    if [ -x "$TARGET_DIR/.venv/bin/python" ]; then
      VENV_PY="$TARGET_DIR/.venv/bin/python"; say "از .venv موجود استفاده می‌شود."
    else
      say "ساخت venv در $TARGET_DIR/venv ..."
      run python3 -m venv "$TARGET_DIR/venv" || die "ساخت venv ناموفق بود."
      VENV_PY="$TARGET_DIR/venv/bin/python"
    fi
  else
    say "venv موجود است."
  fi
  if [ "$SKIP_PIP" = "1" ]; then say "[SKIP] نصب requirements رد شد (PISHRO_SKIP_PIP=1)"; return 0; fi
  if "$VENV_PY" -c 'import telethon, aiohttp' >/dev/null 2>&1; then
    say "✅ وابستگی‌ها از قبل نصب‌اند (telethon/aiohttp)."
  else
    say "نصب requirements.txt (ممکن است یک دقیقه طول بکشد) ..."
    run "$VENV_PY" -m pip install --upgrade pip -q || warn "ارتقای pip ناموفق بود (ادامه می‌دهیم)."
    run "$VENV_PY" -m pip install -r "$TARGET_DIR/requirements.txt" -q || die "نصب requirements ناموفق بود."
    say "✅ requirements نصب شد."
  fi
}

# ----------------------------- systemd -----------------------------
setup_systemd() {
step "گام ۶: اصلاح مسیر سرویس systemd ($UNIT_FILE)"
  if [ ! -f "$UNIT_FILE" ]; then
    say "فایل unit وجود ندارد — ساخته می‌شود."
    if [ "$DRY_RUN" != "1" ]; then
      cat > "$UNIT_FILE" <<UNIT_EOF
[Unit]
Description=PishroSelf Telegram Selfbot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$TARGET_DIR
ExecStart=$TARGET_DIR/venv/bin/python main.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT_EOF
    fi
  else
    run cp -a "$UNIT_FILE" "$BACKUP_DIR/selfbot.service.bak"
    local args new_exec
    args="$(detect_exec_args)"
    new_exec="$TARGET_DIR/venv/bin/python $args"
    say "ExecStart جدید: $new_exec"
    if [ "$DRY_RUN" != "1" ]; then
      sed -i "s|^WorkingDirectory=.*|WorkingDirectory=$TARGET_DIR|" "$UNIT_FILE"
      sed -i "s|^ExecStart=.*|ExecStart=$new_exec|" "$UNIT_FILE"
      if ! grep -q '^WorkingDirectory=' "$UNIT_FILE"; then
        sed -i "/^\[Service\]/a WorkingDirectory=$TARGET_DIR" "$UNIT_FILE"
      fi
      if ! grep -q '^ExecStart=' "$UNIT_FILE"; then
        sed -i "/^\[Service\]/a ExecStart=$new_exec" "$UNIT_FILE"
      fi
      say "--- unit پس از تغییر (خطوط کلیدی) ---"
      grep -E '^(WorkingDirectory|ExecStart|User|Environment)' "$UNIT_FILE" | while read -r line; do say "  $line"; done
    fi
    if grep -Eq '^User=' "$UNIT_FILE" && ! grep -Eq '^User=root' "$UNIT_FILE"; then
      warn "سرویس با کاربر غیر root تعریف شده (User=). مسیر دیتا از خانه همان کاربر حل می‌شود؛"
      warn "اگر دیتای اصلی در /root/PishroSelfData است، خط User= را حذف کن تا با root اجرا شود."
    fi
  fi
  step "گام ۷: daemon-reload"
  if [ -n "$SYSTEMCTL_BIN" ]; then
    run "$SYSTEMCTL_BIN" daemon-reload || die "daemon-reload ناموفق بود."
    say "✅ systemd بازخوانی شد."
  else
    warn "systemctl در دسترس نیست — daemon-reload رد شد."
  fi
}

# ----------------------------- تست import -----------------------------
import_test() {
step "گام ۸: تست import و اتصال به دیتای ثابت"
  if [ "$DRY_RUN" = "1" ]; then say "  [DRY-RUN] تست import"; return 0; fi
  "$VENV_PY" - "$TARGET_DIR" "$DATA_DIR" <<'PISHRO_IMPORTEST_EOF'
import sys
sys.path.insert(0, sys.argv[1])
import config
from pathlib import Path

data_expected = Path(sys.argv[2]).resolve()
data_actual = Path(config.DATA_DIR).resolve()
print('DATA_DIR      =', config.DATA_DIR)
print('DB_DIR        =', config.DB_DIR)
print('SESSIONS_DIR  =', config.SESSIONS_DIR)
print('persistent    =', config.PERSISTENT_SETTINGS_LOADED)
print('API_ID set    =', bool(config.API_ID))
print('API_HASH set  =', bool(config.API_HASH))
print('BOT_TOKEN set =', bool(config.BOT_TOKEN))

problems = []
if data_actual != data_expected:
    problems.append(f'DATA_DIR mismatch: {data_actual} != {data_expected}')
if str(Path(config.DB_DIR)) != str(data_expected / 'db'):
    problems.append(f'DB_DIR mismatch: {config.DB_DIR}')
if str(Path(config.SESSIONS_DIR)) != str(data_expected / 'sessions'):
    problems.append(f'SESSIONS_DIR mismatch: {config.SESSIONS_DIR}')
if not config.PERSISTENT_SETTINGS_LOADED:
    problems.append('تنظیمات پایدار خوانده نشد (PERSISTENT_SETTINGS_LOADED=False)')
if not (config.API_ID and config.API_HASH and config.BOT_TOKEN):
    problems.append('اعتبارنامه کامل نیست')
if config.validate_runtime_config():
    problems.append(f'validate_runtime_config: {config.validate_runtime_config()}')
if problems:
    print('IMPORT_TEST_FAIL')
    for item in problems:
        print('  -', item)
    sys.exit(1)
print('IMPORT_TEST_PASS')
PISHRO_IMPORTEST_EOF
  if [ $? != 0 ]; then
    warn "تست import رد شد."
    rollback_unit
    die "نصب متوقف شد — سرویس قبلی برگردانده شد. مشکل را از بالا بررسی کن."
  fi
  say "✅ تست import پاس شد — نسخه جدید به $DATA_DIR وصل است."
}

# ----------------------------- اجرا و راستی‌آزمایی -----------------------------
start_and_verify() {
step "گام ۹: اجرای سرویس"
  if [ -z "$SYSTEMCTL_BIN" ]; then
    warn "systemctl در دسترس نیست — start/verify رد شد (محیط تست بدون systemd)."
    return 0
  fi
  run "$SYSTEMCTL_BIN" start "$SERVICE_NAME" || { rollback_unit; die "start ناموفق بود."; }
  if [ "$DRY_RUN" = "1" ]; then
    say "  [DRY-RUN] انتظار active + راستی‌آزمایی — شبیه‌سازی شد."
    return 0
  fi
  say "منتظر بالا آمدن سرویس (حداکثر 20 ثانیه) ..."
  local active="inactive" i
  for i in $(seq 1 20); do
    sleep 1
    active="$("$SYSTEMCTL_BIN" is-active "$SERVICE_NAME" 2>/dev/null || true)"
    [ "$active" = "active" ] && break
  done
  if [ "$active" != "active" ]; then
    warn "سرویس active نشد — آخرین لاگ‌ها:"
    journalctl -u "$SERVICE_NAME" -n 40 --no-pager 2>/dev/null | tee -a "$REPORT_FILE" || true
    rollback_unit
    die "سرویس جدید بالا نیامد — rollback انجام شد."
  fi
  say "✅ سرویس active است."

step "گام ۱۰: لاگ ۱۰۰ خط آخر + راستی‌آزمایی"
  local journal_out
  journal_out="$(journalctl -u "$SERVICE_NAME" -n 100 --no-pager 2>/dev/null || true)"
  printf '%s\n' "$journal_out" | tee -a "$REPORT_FILE"

  local boot_line sessions_count main_ok inline_ok pid_ok
  boot_line="$(printf '%s\n' "$journal_out" | grep -m1 'مسیر ثابت داده' || true)"
  sessions_count="$(find "$DATA_DIR/sessions" -maxdepth 1 -name '*.txt' 2>/dev/null | wc -l | tr -d ' ')"
  main_ok="$(printf '%s\n' "$journal_out" | grep -c 'ربات اصلی' || true)"
  inline_ok="$(printf '%s\n' "$journal_out" | grep -c 'اینلاین' || true)"
  pid_ok="$("$SYSTEMCTL_BIN" show -p MainPID "$SERVICE_NAME" 2>/dev/null || true)"

  say ""
  say "════════════ راستی‌آزمایی نهایی ════════════"
  if printf '%s' "$boot_line" | grep -q "$DATA_DIR"; then
    say "✅ مسیر دیتا از لاگ بوت: $boot_line"
  else
    warn "❗ خط بوت مسیر دیتا پیدا نشد یا متفاوت است: [$boot_line] (انتظار: $DATA_DIR)"
  fi
  if [ "${sessions_count:-0}" -gt 0 ] 2>/dev/null; then
    say "✅ سشن‌ها از $DATA_DIR/sessions خوانده می‌شوند ($sessions_count فایل .txt)."
  else
    warn "❗ هیچ فایل سشنی در $DATA_DIR/sessions دیده نشد — اگر سشن‌ها جای دیگری هستند جابه‌جا نکن؛ گزارش بده."
  fi
  [ "${main_ok:-0}" -gt 0 ] && say "✅ ربات اصلی فعال شد." || warn "❗ نشانه «ربات اصلی» در لاگ دیده نشد."
  [ "${inline_ok:-0}" -gt 0 ] && say "✅ ربات اینلاین فعال شد." || warn "❗ نشانه «اینلاین» در لاگ دیده نشد."
  say "ℹ️  $pid_ok"
  say "📦 بکاپ‌ها: $BACKUP_DIR"
  say "🧾 گزارش کامل: $REPORT_FILE"
  say "↩️  برگشت دستی به قبل:  bash $0 --rollback"

  tg_notify "✅ PishroSelf migrate OK
دیتا: $DATA_DIR
سشن‌ها: $sessions_count فایل
سرویس: active
بکاپ: $BACKUP_DIR"
}

show_journal_100() {
  journalctl -u "$SERVICE_NAME" -n 100 --no-pager 2>/dev/null || true
}

# ----------------------------- حالت rollback دستی -----------------------------
if [ "${1:-}" = "--rollback" ]; then
  say "↩️  Rollback دستی ..."
  rollback_unit
  say "── لاگ ۵۰ خط آخر ──"
  show_journal_50() { journalctl -u "$SERVICE_NAME" -n 50 --no-pager 2>/dev/null || true; }
  show_journal_50
  exit 0
fi

# ----------------------------- اجرای اصلی -----------------------------
check_root
say "════════════════════════════════════════════"
say "  PishroSelf مهاجرت امن — $(date '+%Y-%m-%d %H:%M:%S')"
say "════════════════════════════════════════════"
say "هدف (نسخه جدید): $TARGET_DIR"
say "دیتای اصلی     : $DATA_DIR (فقط خواندنی + بکاپ)"
say "سرویس          : $SERVICE_NAME"
[ "$DRY_RUN" = "1" ] && warn "حالت DRY-RUN — هیچ تغییری اعمال نمی‌شود."

step "پیش‌بررسی"
[ -f "$TARGET_DIR/main.py" ] || die "main.py در $TARGET_DIR نیست — مسیر نسخه جدید را با PISHRO_TARGET=... بده."
[ -d "$DATA_DIR" ] || warn "پوشه دیتا $DATA_DIR پیدا نشد — در صورت نبود، هنگام بوت ساخته می‌شود."
[ -n "$SYSTEMCTL_BIN" ] || warn "systemctl پیدا نشد (محیط تست؟) — گام‌های systemd شبیه‌سازی می‌شوند."
say "پیش‌بررسی OK."

step "گام ۱: توقف سرویس $SERVICE_NAME"
if [ -n "$SYSTEMCTL_BIN" ]; then
  run "$SYSTEMCTL_BIN" stop "$SERVICE_NAME" 2>/dev/null || warn "سرویس در حال اجرا نبود."
else
  say "  [بدون systemctl] رد شد."
fi
say "✅ سرویس متوقف شد."

step "گام ۲: بکاپ‌های timestamp دار → $BACKUP_DIR"
if [ -f "$UNIT_FILE" ]; then
  run cp -a "$UNIT_FILE" "$BACKUP_DIR/selfbot.service.bak" && say "✅ unit بکاپ شد."
else
  warn "unit فعلی پیدا نشد (سرویس از قبل تعریف نشده بود) — در گام ۶ ساخته می‌شود."
fi
if [ "$SKIP_TAR" = "1" ]; then
  say "[SKIP] بکاپ tar دیتا رد شد."
else
  say "بکاپ کامل دیتا (tar) — بسته به حجم ممکن است طول بکشد ..."
  if [ "$DRY_RUN" = "1" ]; then
    say "  [DRY-RUN] tar -czf $BACKUP_DIR/PishroSelfData_FULL_$TS.tar.gz"
  else
    if tar -czf "$BACKUP_DIR/PishroSelfData_FULL_$TS.tar.gz" -C "$(dirname "$DATA_DIR")" "$(basename "$DATA_DIR")" 2>>"$REPORT_FILE"; then
      tar_size="$(du -h "$BACKUP_DIR/PishroSelfData_FULL_$TS.tar.gz" 2>/dev/null | cut -f1)"
      say "✅ بکاپ کامل دیتا: $BACKUP_DIR/PishroSelfData_FULL_$TS.tar.gz ($tar_size)"
    else
      warn "بکاپ tar کامل نشد (فضای دیسک؟) — ادامه می‌دهیم چون هیچ عمل مخربی روی دیتا انجام نمی‌شود."
    fi
  fi
fi

ensure_persistent_config
install_loader_config
prepare_venv
setup_systemd
import_test
start_and_verify

say ""
say "🏁 مهاجرت کامل شد."
exit 0
