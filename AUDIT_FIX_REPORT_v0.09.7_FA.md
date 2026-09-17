# گزارش ممیزی و اصلاح PishroSelf v0.09.7

مبنای بررسی: `PishroSelf_v0.09.6_CUSTOM_EMOJI_MANAGER.zip`

## دامنه بررسی

کل آرشیو، ساختار پوشه‌ها، 78 فایل Python، import graph، requirements، JSON sharding، SQLite migrations/models، handlerها و callbackها، stateهای چندکاربره، Self/Inline/Main Bot، Custom Emoji، Message Saver/TTL، Tabchi، فونت، TTS، عضویت/Trial/Support/Admin، Wallet/Transfer/Game و تست‌های امنیتی/رگرسیون بررسی شدند.

## باگ‌ها و ریسک‌های اصلاح‌شده

### 1. Critical — credentialهای production داخل سورس

در نسخه ورودی Telegram/AvalAI credentialها به شکل plaintext در `config.py` قرار داشتند. همه secretهای واقعی از سورس حذف شدند و config اکنون از `.env` یا environment variable می‌خواند. `.env.example` بدون secret اضافه شده و `.env` در `.gitignore` است. مقادیر قدیمی باید rotate/revoke شوند، چون قبلاً در آرشیو plaintext بوده‌اند.

### 2. High — احتمال از دست رفتن تنظیمات هنگام shard rollover

در `_save_user` نسخه قبلی، هنگام عبور shard از سقف حجم ابتدا رکورد کاربر از shard قدیمی حذف می‌شد و سپس shard جدید نوشته می‌شد. خرابی دیسک در مرحله دوم می‌توانست آخرین نسخه durable کاربر را حذف کند. ترتیب commit اصلاح شد: مقصد جدید → index durable → cleanup قدیمی. شکست cleanup فقط duplicate امن ایجاد می‌کند و داده را حذف نمی‌کند.

### 3. High — JSON write غیراتمیک/بدون recovery کافی

ذخیره JSON به atomic temp write + flush + `fsync` + `os.replace` تبدیل شد. backup آخرین primary معتبر نگهداری می‌شود و اگر primary خراب باشد backup معتبر با نسخه خراب overwrite نمی‌شود. `_read_json` در parse/read failure از `.bak` بازیابی می‌کند.

### 4. Medium — alias شدن nested state در تنظیمات کاربر

`DEFAULT_SETTINGS.copy()` و خروجی‌های shallow-copy می‌توانستند باعث اشتراک لیست‌هایی مثل `muted_chats` و `enemy_chats` بین cache و caller شوند. تمام مسیرهای حساس به `deepcopy` تبدیل شدند.

### 5. Medium — cache می‌توانست state ذخیره‌نشده را معتبر نشان دهد

`save_user_settings`/`update_user_settings` قبل از موفقیت write، cache را تغییر می‌دادند. اکنون cache فقط بعد از commit موفق JSON به‌روزرسانی می‌شود.

### 6. Medium — startup نیمه‌کاره و task lifecycle

`main.py` در صورت failure بعضی DB initها می‌توانست بخش‌های دیگر را بالا بیاورد و `gather` نیز در توقف بی‌صدای یک سرویس حیاتی ممکن بود runtime نیمه‌جان نگه دارد. DB init به startup gate تبدیل شد، تنظیمات ضروری قبل از اتصال validate می‌شوند و خروج غیرمنتظره Main Bot/Inline/Scheduler باعث shutdown کنترل‌شده taskهای باقی‌مانده می‌شود.

### 7. Low/Medium — TTL task registry خالی باقی می‌ماند

بعد از اتمام آخرین TTL task، set خالی برای owner در `_TTL_TASKS` می‌ماند. callback cleanup اکنون bucket خالی را هم حذف می‌کند.

### 8. Observability — logging پراکنده و print-only

`services/logging_service.py` اضافه شد: RotatingFileHandler با سقف 5MB و 3 backup، console log، دسته‌بندی DB/API/User Action و redaction credential/phone. مسیرهای DB، scheduler، message saver، session crash و Custom Emoji به logging مرکزی متصل شدند. UI فعلی حذف یا بازطراحی نشده است.

## موارد بررسی‌شده که مشکل قابل اثبات نداشتند

- SQL Injection: queryهای value با parameter binding اجرا می‌شوند. SQL داینامیک موجود فقط نام ستون‌های انتخاب‌شده از allowlist داخلی را interpolate می‌کند.
- `eval`, `exec`, `os.system`, `shell=True`, unsafe pickle load: در سورس اجرایی پیدا نشد.
- Custom Emoji: owner scope، UTF-16 entity offset، save token expiry، stale callback و migration توسط تست‌های موجود پوشش داده شده‌اند.
- Admin/permissions: callbackهای حساس و RBAC توسط تست‌های security موجود پوشش داده شده‌اند.
- Tabchi/whitelist/blacklist: owner isolation و policy re-check قبل از send تست شده‌اند.
- Payment/Wallet/Game/Transfer: تست‌های atomicity، concurrency، replay protection و owner/identity guards پاس شدند.
- Message Saver: owner-scoped settings/cache/history و delete ambiguity تست شده‌اند.

## فایل‌های بلااستفاده/سازگاری

`self_panel_bot.py` در نسخه ورودی فایل صفر بایتی و بدون import فعال بود. برای رعایت قانون «قابلیت/ساختار را بی‌دلیل حذف نکن» حذف نشد. `services/banner_sender.py` wrapper سازگاری نسخه‌های قبلی است و با وجود استفاده‌نشدن مستقیم در runtime فعلی حفظ شد.

## تست نهایی

- Safe ZIP extraction / path traversal: PASS
- Python `compileall`: PASS
- Runtime module imports (offline stubs): 62/62 PASS
- Pytest baseline + hardening: 288/288 PASS
- Hardening tests جدید: 10/10 PASS
- Secret scan در سورس اجرایی: credential واقعی پیدا نشد
- Static dangerous primitive scan: PASS

تست زنده Telegram/AvalAI انجام نشده است، چون محیط ممیزی شبکه/credential اجرایی ندارد. بنابراین صحت اتصال واقعی providerها را نباید با تست آفلاین یکی دانست؛ اما مسیرهای Python، state، migration و handler logic توسط تست‌های موجود و جدید بدون تماس شبکه بررسی شده‌اند.
