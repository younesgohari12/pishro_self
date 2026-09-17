# گزارش Production-Safe Converter — PishroSelf v0.09.13

تاریخ: 2026-09-18 | محدوده: فعال‌سازی Production-Safe کانورتر + Audit کامل مسیر ارسال + تست واقعی

---

## ۱) چرا کانورتر پیش‌فرض خاموش بود؟

زنجیره دقیق علت (بازرسی کد، نه حدس):

| # | محل | مقدار | اثر |
|---|-----|-------|-----|
| 1 | `config.py:11` | `PREMIUM_EMOJI_CONVERTER_ENABLED = False` | پیش‌فرض انتشار عمداً خاموش بود («هر حساب از پنل روشن کند») |
| 2 | `db.py:38` | `'premium_emoji_converter': None` | حسابی که دکمه پنل را لمس نکرده = None |
| 3 | `self.py:364-374` | `_converter_flag` → `None` | لمس‌نشده به موتور برمی‌گردد |
| 4 | `services/premium_emoji_converter.py:365` | fallback به config | None → `False` → wrapper بدون تبدیل عبور می‌کند |
| 5 | `main.py:28` | `apply_config_update('v0.09.13-premium-emoji-converter', ...)` | مقدار False در `~/PishroSelfData/config.py` نصب‌های موجود هم **ذخیره** شده بود |

نتیجه: Unified Pipeline نصب و سالم بود، اما با پیش‌فرض False و پنلِ لمس‌نشده، عملاً برای هیچ حسابی فعال نبود. کشف جانبی: کلید اصلی `PREMIUM_EMOJI_ENABLED` اصلاً در `effective_enabled()` کانورتر چک نمی‌شد (فقط injector قدیمی چک می‌کرد).

## ۲) حالت Production-Safe (پیاده‌سازی شد)

منطق جدید فعال‌سازی (دقیقاً طبق سفارش):

```
فعال = PREMIUM_EMOJI_ENABLED (کلید اصلی)  AND  پنلِ کاربر صراحتاً خاموش نکرده باشد
```

- کلید اصلی `PREMIUM_EMOJI_ENABLED=True` → لازم ولی کافی نیست؛ گیت اول.
- انتخاب پنل (`premium_emoji_converter` در دیتابیس): `False` = همیشه خاموش (اختیار کاربر محترم است)؛ `True` = همیشه روشن؛ `None` = لمس‌نشده → پیش‌فرض جدید config.
- پیش‌فرض انتشار `PREMIUM_EMOJI_CONVERTER_ENABLED` از `False` به `True` تغییر کرد (production-safe).
- نصب‌های موجود که False انتشار قبل را ذخیره دارند: با marker جدید `v0.09.13-premium-emoji-converter-default-on` **یک‌باره** به True ارتقا می‌یابند (فقط مقدارِ نوشته‌شده توسط انتشار قبلی؛ انتخاب پنل در دیتابیس هرگز دست نمی‌خورد).
- پنل (`inline.py`) با همان منطق هم‌راستا شد: نمایش و تاگل از `premium_converter_effective(uid)`؛ اگر کلید اصلی خاموش باشد، پیام شفاف «کلید اصلی خاموش است» می‌دهد.
- cooldown ردِ تلگرامی (300s) همچنان مقدم بر همه است.

جدول واقعیت (تست‌شده — ۱۰ تست جدید در `tests/test_premium_emoji_production_safe.py`):

| کلید اصلی | پنل (db) | config | کانورتر |
|-----------|----------|--------|---------|
| False | هر چیزی | هر چیزی | ❌ خاموش |
| True | False | True | ❌ خاموش (اختیار کاربر) |
| True | True | False | ✅ روشن |
| True | None (لمس‌نشده) | True | ✅ روشن (پیش‌فرض جدید) |

## ۳) Audit کامل `client.send_message` مستقیم

**معماری مسیر:** Pipeline یکپارچه در سطح کلاینت سلف نصب می‌شود (`self.py` → `install_premium_emoji_converter`) و متدهای `send_message` / `send_file` / `edit_message` / `_send_album` را می‌پیچد؛ `event.reply`/`event.respond` در Telethon 1.44 به همین متدها delegate می‌کنند، پس پوشیده‌اند.

**فایل‌های سمت اکانت سلف — همگی از Pipeline عبور می‌کنند ( Covered ✅):**
- `self.py` (crypto، translate، AI، ping، spam، delete، TTS، کپی محتوا، پیام شروع)
- `services/sender.py` (تبچی متن/کپشن) — توسط `services/scheduler.py` استفاده می‌شود
- `services/copy_protected.py` (کپی محتوای محافظت‌شده)
- `services/media_sender.py` + `services/deleted_handler.py` (سیو پیام/آنتی‌دلیت)
- `services/custom_emoji_service.py:146` (ارسال entity صریح؛ idempotent)
- `event.reply` در هندلرهای سلف (`self.py:437`)

**سمت بات — عمداً بدون کانورتر (طبق قانون پروژه، Bot Client هرگز دست‌نخورده):**
- `bot/core.py`، `handlers/*` (`self.bot.send_message`)، `inline.py`، `login_manager.py` (جریان ورود)، `services/telegram_logger.py` (ربات لاگ)

**Raw API (`SendMessageRequest/SendMediaRequest/SendMultiMediaRequest`):** در کد اجرایی **صفر** مورد؛ فقط در tests. ✅

## ۴) فایل‌هایی که بدون عبور از Unified Pipeline ارسال می‌کنند

| فایل | خط | شرح | ریسک |
|------|----|----|------|
| `login_manager.py` | 525 | پیام `SELF_STARTED_TEXT` با **کلاینت موقتی لاگین** (قبل از ساخت سشن نهایی) ارسال می‌شود؛ کانورتر روی آن کلاینت نصب نیست | کم — یک پیام خوش‌آمد؛ همان متن در `self.py:1362` روی کلاینت کانورت‌شده هم ارسال می‌شود |
| `bot/core.py`, `handlers/*`, `inline.py`, `login_manager.py` (بات)، `services/telegram_logger.py` | — | کلاینت‌های بات: طبق قانون پروژه عمداً خارج از Pipeline | عمدی/صفر (اکانت بات پرمیوم نیست) |
| `tools/*` (smoke/live) | — | ابزارهای عملیاتی دستی؛ سشن مستقل با `is_enabled=lambda: True` | عمدی/صفر (خارج از runtime) |
| `forward_messages` | — | طبق قانون: هرگز پیچیده نمی‌شود | عمدی |

تنها یافته واقعی runtime همان `login_manager.py:525` است؛ برای حفظ backward-compatible و پرهیز از دست‌زدن جریان ورود، ثبت شد و تغییر نکرد (پیام تکراری همین متن از مسیر کانورت‌شده هم می‌رسد).

## ۵) تست واقعی — فقط نتایج اجرا‌شده

**سطح آفلاین (شبیه‌سازی مرز شبکه با Telethon واقعی — اجرای واقعی انجام شد):**

```
tests/test_premium_emoji_production_safe.py   10 passed   (جدول Production-Safe)
tests/test_premium_emoji_pipeline.py          21 passed
  ├─ test_all_chat_types_convert_without_prefix[Saved]      PASSED
  ├─ test_all_chat_types_convert_without_prefix[Private]    PASSED
  ├─ test_all_chat_types_convert_without_prefix[Group]      PASSED
  ├─ test_all_chat_types_convert_without_prefix[Channel]    PASSED
  ├─ test_file_caption_converted                            PASSED
  ├─ test_album_captions_converted_per_item                 PASSED
  └─ test_reply_message_converted                           PASSED
کل سوئیت: 763 passed / 6 failed (هر ۶ روی HEAD بدون تغییرات من هم دقیقاً همین‌هاست —
پیش‌عضو و محیطی: pagination/db خالی/escape_md/validate_config بدون مقدار)
```

**سطح لایو تلگرام (ارسال واقعی):** در این محیط سشن پرمیوم/API_ID/API_HASH وجود ندارد؛ بنابراین تست لایو **اجرا نشد و هیچ نتیجه‌ای برایش ادعا نمی‌شود**. به‌جای آن ابزار واقعی `tools/premium_emoji_live_test.py` ساخته شد (۶ سناریو: Saved/Private/Group/Channel/Caption/Album؛ ارسال واقعی، راستی‌آزمایی از entities پیامِ برگشتی، حذف خودکار، ممنوعیت نتیجه ساختگی) و مسیر گارد آن واقعاً اجرا و تأیید شد:

```
$ python -m tools.premium_emoji_live_test
FAIL: متغیر محیطی PREMIUM_LIVE_SESSION تنظیم نشده است؛ تست لایو اجرا نشد.
این ابزار هرگز نتیجه ساختگی تولید نمی‌کند.   (exit code = 2)
```

اجرا روی سرور خودت (که سشن پرمیوم دارد):
```bash
export PREMIUM_LIVE_SESSION='<StringSession اکانت پرمیوم>'
python -m tools.premium_emoji_live_test --private <id> --group <id> --channel <id> --file a.jpg --file2 b.jpg
```

## ۶) تضمین عدم افزودن ایموجی ثابت

- سیستم Prefix/FALLBACK/placeholder از انتشار DEBUG FINAL حذف شده و در این نسخه هم بازگشته است: نگاشتِ خالی → «بدون تبدیل» (هرگز به شناسه ثابت نمی‌رسد).
- متن پیام عوض نمی‌شود؛ فقط `MessageEntityCustomEmoji` اضافه می‌شود (idempotent).
- تست `test_no_auto_prefix_anywhere_in_sources` و `test_sent_text_never_mutated_for_any_chat_type` پاس است.

## فایل‌های تغییر یافته

| فایل | تغییر | دلیل |
|------|-------|------|
| `config.py` | پیش‌فرض `PREMIUM_EMOJI_CONVERTER_ENABLED=True` + کامنت | Production-Safe |
| `services/premium_emoji_converter.py` | `effective_enabled()`: گیت کلید اصلی + پیش‌فرض True | منطق سفارش‌شده |
| `inline.py` | `premium_converter_effective()` هم‌راستا + تاگل DRY + alert کلید اصلی | پنل = موتور |
| `main.py` | marker جدید `...-default-on` | نصب‌های موجود False → True |
| `tests/test_premium_emoji_converter.py` | پیش‌فرض‌های جدید + marker | هم‌راستایی تست |
| `tests/test_premium_emoji_production_safe.py` | **جدید** — ۱۰ تست | جدول واقعیت + audit + marker |
| `tools/premium_emoji_live_test.py` | **جدید** — ابزار لایو ۶ سناریو | تست واقعی بدون جعل |
| `AGENTS.md`, `SETUP_SECRETS_FA.txt` | مستندات | هم‌راستایی |
