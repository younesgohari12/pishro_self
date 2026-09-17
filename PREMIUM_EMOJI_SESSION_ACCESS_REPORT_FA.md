# Patch دسترسی به session موجود — Candidate

این نسخه فقط مسیر دسترسی به session/client و فرمان‌های محدود ادمین را تکمیل می‌کند.
LIVE CURATION در این محیط اجرا نشده است. وضعیت production_ready یا PRODUCTION_FINAL اعلام نمی‌شود.
فایل‌های اعتبارسنجی زنده و mapping اصلی همان وضعیت قبلی را حفظ کرده‌اند؛ خروجی تست جایگزین آنها نشده است.

## روش ترجیحی: ربات در حال اجرا

پس از نصب patch و راه‌اندازی مجدد معمول پروژه، در گفت‌وگوی خصوصی با ربات اصلی، با حساب ADMIN_ID:

```text
/emoji_curation 123456789
```

این دستور فقط metadata را بررسی می‌کند و هیچ sample ارسال نمی‌کند.
کاربر هدف باید Self client آنلاین داشته باشد. در غیر این صورت پاسخ دقیق:

```text
Self client is not online
```

برای ارسال صریح نمونه‌ها:

```text
/emoji_curation_samples 123456789
```

از همان client ثبت‌شده در self_manager استفاده می‌شود. ابزار آن را reconnect یا disconnect نمی‌کند.
این دسترسی به ادمین‌های تفویض‌شده تعلق ندارد؛ فقط ADMIN_ID مجاز است.
اجرای همزمان فرمان‌های curation رد می‌شود تا sampleها از دو اجرای همپوشان ارسال نشوند.
هر فرمان جدید یک اجرای تازه است؛ این فرمان‌ها را برای تکرار خودکار یک اجرای نیمه‌تمام استفاده نکنید.

خروجی هر اجرای موفق در پوشهٔ جدید زیر config.DATA_DIR نوشته می‌شود:

```text
premium_emoji_curation/run-YYYYMMDD-HHMMSS-microseconds/
```

چهار فایل:

- PREMIUM_EMOJI_LIVE_VALIDATION.json
- PREMIUM_EMOJI_CURATION.json
- PREMIUM_EMOJI_MAPPING_STATUS.json
- PREMIUM_EMOJI_SAMPLES.json

هیچ session یا credential در این پوشه کپی نمی‌شود. sample فقط به Saved Messages خود client هدف (`me`) ارسال می‌شود.

## CLI با session موجود پروژه

از پوشهٔ پروژه و با همان حساب سیستم‌عامل و محیط اجرای نصب اصلی:

```bash
python tools/premium_emoji_live_smoke.py --user-id 123456789 --curation --send-samples --output-dir live-curation-run-01
```

مسیر session فقط از `config.SESSIONS_DIR` و نام `user_123456789.txt` محاسبه می‌شود.
مسیر home در ابزار hardcode نشده است. نیازی به export یا copy/paste محتوای StringSession نیست.
همچنین:

```bash
python tools/premium_emoji_live_smoke.py --session-name user_123456789 --curation --output-dir live-curation-metadata-01
```

نام با `services.session_restore.session_user_id` بررسی می‌شود؛ مسیر، پسوند دلخواه، traversal و symlink فایل پذیرفته نمی‌شود.
حالت diagnostic قبلی `--session-string-file /path/to/existing.txt` نیز باقی است.
دقیقاً یکی از سه گزینهٔ منبع لازم است؛ چند منبع یا نبود منبع خطاست.
`--user-id` باید متعلق به هویت Telegram همان session باشد؛ session غیرمجاز یا bot رد می‌شود.

CLI مستقل به حافظهٔ یک process دیگر دسترسی ندارد. اگر Self روشن است، فرمان ادمین را ترجیح دهید؛
CLI مستقل از فایل موجود اتصال خودش را می‌سازد و فقط همان اتصال را در finally قطع می‌کند.
فایل session تنها خوانده می‌شود؛ login، QR، دریافت کد، ذخیره‌سازی یا حذف session انجام نمی‌شود.

## بررسی بصری و revalidation

ارسال sample یا metadata به معنی تأیید بصری نیست. تمام candidateهای هر exact alt باید واقعاً دیده و مقایسه شوند.
در یک کپی از PREMIUM_EMOJI_CURATION.json، reviewed_ids، ranked_ids، evidence و styles را با نتیجهٔ واقعی تکمیل کنید.
سپس با session پروژه و پوشهٔ خروجی جدید:

```bash
python tools/premium_emoji_live_smoke.py --user-id 123456789 --curation --review-file live-curation-run-01/PREMIUM_EMOJI_CURATION.json --output-dir live-curation-reviewed-01
```

این دستور metadata را دوباره از Telegram می‌گیرد و از compile_curation موجود استفاده می‌کند.
خروجی CURATED_MAPS_REVIEWED.py تنها پس از اعتبارسنجی review ساخته می‌شود؛ این patch آن را خودکار وارد runtime نمی‌کند.
API داخلی runtime نیز `runtime_curation(user_id, review_file=...)` را برای همان process پشتیبانی می‌کند.

کنترل batch فعلی (حداکثر ۵)، بودجهٔ FloodWait و عدم replay ارسال نامطمئن حفظ شده‌اند.
در تحویل نامشخص، Saved Messages را قبل از هر اجرای مجدد بررسی کنید؛ گزارش قبلی را نگه دارید.
پرچم‌ها و منطق resume ابزار قبلی تغییر نکرده‌اند.

## وضعیت واقعی این تحویل

| مورد | مقدار |
|---|---:|
| Source IDs | 210 |
| Valid | null — بررسی زنده انجام نشده |
| Rejected | null — بررسی زنده انجام نشده |
| Unresolved | 210 |
| Actual alt count | null — نامشخص |
| Curated Unicode | 0 |
| Visually approved IDs | 0 |
| Variant mappings | 0 |
| live_metadata_attempted | false |

mapping تأییدشده برای 🔥 ❤️ 😂 😎 👑 💎 🖤 👍 🙏 🫡 🫶 💯 هنوز وجود ندارد.

## نتیجهٔ تست

- تست‌های تازهٔ دسترسی/امنیت: 45 pass / 0 fail.
- مجموع تست‌های دسترسی و curation: 80 pass / 0 fail.
- کل suite نسخهٔ patch: 632 pass / 6 fail.
- کل suite ZIP اولیه، در همین محیط: 587 pass / 6 fail.
- نام هر شش تست fail در دو نسخه یکسان است؛ در این patch اصلاح نشده‌اند.
- تمام این تست‌ها offline هستند؛ ادعای اتصال Telegram، ارسال واقعی یا visual approval ندارند.

شش خطای موجود:

- `tests/test_admin_user_pagination.py::test_admin_users_page_two`
- `tests/test_hardening_v097.py::test_config_is_python_only_and_validates_without_values`
- `tests/test_memory_streaming_lock_pagination.py::test_iter_user_ids_does_not_hold_lock_while_consuming`
- `tests/test_memory_streaming_lock_pagination.py::test_large_user_pagination_is_page_bounded`
- `tests/test_release_regression.py::test_release_markdown_escape`
- `tests/test_user_streaming.py::test_broadcast_can_consume_iterator`

## دامنهٔ تغییر

سه فایل اجرایی تغییر کرده‌اند: ابزار live smoke، کنترلر ادمین و ثبت مسیر فرمان در bot/core.py.
یک تست قبلی برای logger خصوصی ابزار به‌روز شده و یک فایل تست جدید اضافه شده است.
شش تابع اصلی curation با مقایسهٔ AST بدون تغییر تأیید شدند.
services/premium_emoji_injector.py، session_restore.py، self_manager.py، config.py و منطق storage تغییر نکرده‌اند.
گزارش‌های قبلی تاریخی هستند؛ گزارش این patch فایل‌های PREMIUM_EMOJI_SESSION_ACCESS_* است.

ZIP فاقد __pycache__ و *.pyc است. اطلاعات ورود جدیدی ایجاد، درخواست یا در گزارش ثبت نشده است.
