# گزارش نهایی — Premium Emoji Unified Pipeline + حذف کامل Prefix

پروژه: **PishroSelf v0.09.13 — PREMIUM_EMOJI_DEBUG_FINAL**
تاریخ: 2026-09-17 | پایه: `v0.09.13-premium-emoji-debug` (commit 21497e5)

---

## ۱) خلاصه اجرایی

این نسخه سه مشکل گزارش‌شده مالک را به‌صورت ریشه‌ای حل می‌کند:

1. **ایموجی ثابت ابتدای پیام‌ها** — سیستم Prefix Emoji (تزریق «✨ » با
   `FALLBACK_DOCUMENT_ID = 5938388342281343001` به ابتدای هر پیام) به‌طور
   کامل حذف شد. هیچ پیام ارسالی دیگر هیچ ایموجی اضافه‌ای نمی‌گیرد.
2. **تبدیل واقعی Custom Emoji** — همان ایموجی‌های کاربر (😂🔥❤️) در متن
   دست‌نخورده می‌مانند و فقط `MessageEntityCustomEmoji` با
   `document_id = custom_emoji_id` به آن‌ها اضافه می‌شود.
3. **مشکل «فقط Saved Messages کار می‌کند»** — معماری به **Unified Pipeline
   پیش از ارسال** تغییر کرد؛ تبدیل قبل از ارسال روی همهٔ مسیرهای پیام سلف
   انجام می‌شود و روش «ارسال سپس ویرایش» فقط برای پیام‌های رسیده از دستگاه
   دیگر (گوشی/اپ رسمی) به‌عنوان **fallback** باقی می‌ماند.

## ۲) علت ریشه‌ای مشکل Saved-Only + ایموجی ثابت

| # | علت | توضیح |
|---|-----|-------|
| RC-P1 | **سیستم Prefix روشن بود** (`PREMIUM_EMOJI_PREFIX_ENABLED=True` در config) | هر پیام سلف «✨ » می‌گرفت و همهٔ entity ها ۳ واحد UTF-16 جابه‌جا می‌شدند؛ در چت‌هایی که تلگرام entity را رد می‌کرد، پیامِ بدون entity بازفرست می‌شد (رفتار «fallback» خود Prefix) — یعنی ایموجی ثابت می‌ماند ولی تبدیل نمی‌شد. |
| RC-P2 | **پیام‌های گوشی از کانورتر عبور نمی‌کنند** | wrapper ها فقط متدهای کلاینت پایتون را می‌بندند؛ پیام اپ رسمی مستقیم به سرور می‌رود. تنها راه، edit بعد از ارسال است که در Saved بی‌دردسر و در چت‌های دیگر مستعد خطای بی‌صدا بود. |
| RC-P3 | **شکست‌های edit بعد از ارسال بی‌صدا بودند** | خطای resolve کردن input_chat یا رد شدن RPC فقط swallow می‌شد؛ ادمین هرگز دلیل را نمی‌دید. |

**رفع:** RC-P1 با حذف کامل ماژول و کلیدهای config؛ RC-P2 با فرودن
تبدیل به «قبل از ارسال» روی همهٔ مسیرها (کپشن/ریپلای/آلبوم/ویرایش) و
نگه‌داشتن edit فقط به‌عنوان fallback؛ RC-P3 با گزارش دلیل دقیق هر شکست
(نوع چت + Exception) به ربات گزارش Admin و لاگ محلی.

## ۳) معماری جدید — Unified Pipeline

```
Input (send_message / send_file / edit_message / _send_album)
        ↓
Pre Processor  →  client._parse_message_text (پارس یک‌بارهٔ Telethon)
        ↓
Custom Emoji Converter  →  تشخیص ایموجی + نگاشت مرکزی (فقط alt دقیقاً برابر)
        ↓
Telegram Entity Builder  →  MessageEntityCustomEmoji با آفست UTF-16 دقیق
        ↓
Sender  →  پیام از همان ابتدا با entity صحیح روی سیم می‌رود
```

- **fallback (فقط برای پیام‌های گوشی):** هندلر outgoing → تشخیص ایموجی بدون
  entity → `EditMessageRequest` با همان متن + entity ها؛ هر خطا با دلیل
  گزارش می‌شود (`Send: EDITED` در بلوک debug).
- هم‌پوشانی zero: forward ،via_bot (پنل اینلاین)، پیام‌های سرویس و پیام‌های
  دارای Custom Emoji هرگز دست نمی‌خورند؛ کلاینت Bot هرگز wrap نمی‌شود.

## ۴) فایل‌های تغییر یافته و دلیل

| فایل | تغییر | دلیل |
|------|-------|------|
| `services/premium_emoji_prefix.py` | **حذف کامل** | منبع ایموجی ثابت «✨ » و `FALLBACK_DOCUMENT_ID`؛ طبق spec مالک باید حذف شود. |
| `tests/test_premium_emoji_prefix.py` | **حذف کامل** | تستِ قابلیت حذف‌شده. |
| `premium_emoji_mapping.py` | حذف `FALLBACK_DOCUMENT_ID` + به‌روزرسانی مستند | هیچ مسیر runtime به شناسه ثابت ختم نمی‌شود. |
| `services/premium_emoji_converter.py` | **بازنویسی به Unified Pipeline**: حذف fallback از `_sanitize_pool`؛ `_mapping_key` روی نگاشت رجیستری خود موتور؛ `load_emoji_map()` + `_map_file_path()` (فایل مرکزی امن)؛ `_chat_kind()` (نوع چت بدون شبکه)؛ بلوک‌های `[PREMIUM DEBUG]` (با `Chat Type`) و `[CustomEmoji]`؛ گزارش 🎨 فقط بعد از ارسال موفق؛ `Send: SUCCESS/FAILED` واقعی؛ fallback پس از ارسال با گزارش دلیل + `Send: EDITED` | معماری spec: تبدیل قبل از ارسال، edit فقط fallback، debug کامل، ایموجی ثابت ممنوع. |
| `services/telegram_logger.py` | افزودن `format_custom_emoji_debug()` و `send_custom_emoji_debug()` (سپرده ضد-اسپم مستقل `custom_debug`)؛ `format_premium_debug(..., chat_type=)` | بلوک کوتاه `[CustomEmoji]` مطابق نمونهٔ spec مالک. |
| `config.py` | حذف ۳ کلید `PREMIUM_EMOJI_PREFIX_*`؛ افزودن `PREMIUM_EMOJI_DEBUG/CUSTOM_EMOJI_DEBUG/PREMIUM_EMOJI_MAP_FILE/PREMIUM_EMOJI_OUTGOING_FIX/LOG_LEVEL/PREMIUM_EMOJI_LOG_LEVEL/ADMIN_LOG_IDS`؛ حذف `os.getenv` از `TRIAL_DURATION_HOURS` | سوییچ‌های debug/لاگ + پاک‌سازی hardening. |
| `self.py` | حذف `install_premium_prefix` از import/install/uninstall؛ به‌روزرسانی کامنت معماری | نصب Prefix از زمان‌بندی راه‌اندازی حذف شود. |
| `emoji_map.json` | **فایل جدید — نگاشت مرکزی** `{ایموجی: [document_id, ...]}` (۳۱ ایموجی، تولیدشده از نگاشت alt-تأییدشده) | فایل مرکزی قابل ویرایش بدون تغییر کد؛ نگاشتِ نبود = پیام عادی. |
| `tools/export_emoji_map.py` | ابزار جدید تولید/بررسی همگامی فایل نگاشت (`--check` برای CI) | نگهداری امن فایل مرکزی. |
| `tools/premium_emoji_debug_smoke.py` | ارتقا به **۶ سناریو** (Saved/Private/Group/Channel/Caption/Reply) + گزارش هر شکست به ربات | تست‌های الزامی spec. |
| `tools/resolve_premium_emoji_mapping.py` | حذف منابع fallback/prefix از جمع شناسه‌ها؛ افزودن شناسه‌های فایل مرکزی | سازگاری با حذف fallback. |
| `tools/premium_emoji_strict_smoke.py` | شناسه ممنوع به‌صورت سنتینل تستی (`BANNED_FALLBACK_ID`) | تضمین دائمی «بدون fallback». |
| `tests/test_premium_emoji_pipeline.py` | **۲۱ تست جدید** — ۶ سناریو + تضمین‌ها | پوشش کامل spec (جدول پایین). |
| `tests/test_premium_emoji_converter.py` / `test_premium_emoji_strict.py` / `test_storage_updates.py` | سازگاری با حذف fallback/prefix + تست «کلیدهای prefix دیگر وجود ندارند» | نگاشت‌ها بدون fallback هم Strict می‌مانند. |
| `AGENTS.md` | به‌روزرسانی قوانین معماری | مستندسازی حذف Prefix و نقش fallback. |

## ۵) تست‌ها و نتیجه

**۲۱ تست جدید** در `tests/test_premium_emoji_pipeline.py` (همه آفلاین، مرز
شبکه شبیه‌سازی‌شده — قرارداد AGENTS.md):

| سناریو / تضمین | نتیجه |
|---|---|
| Saved Messages (InputPeerSelf) — تبدیل ۳ ایموجی، متن دست‌نخورده | ✅ |
| Private Chat (InputPeerUser) — همان | ✅ |
| Group (InputPeerChat) — همان | ✅ |
| Channel (InputPeerChannel) — همان (تا جایی که MTProto اجازه دهد) | ✅ |
| File Caption (send_file) + آلبوم ۳تایی کپشن | ✅ |
| Reply Message (reply_to → InputReplyToMessage) | ✅ |
| آفست UTF-16 دقیقاً روی موقعیت ایموجی‌ها | ✅ |
| هیچ prefix/placeholder در هیچ سورس runtime (اسکن سورس) | ✅ |
| متن ارسالی برای هر ۴ نوع چت = دقیقاً ورودی | ✅ |
| ایموجی بدون نگاشت → بدون تبدیل، بدون crash | ✅ |
| بارگذاری `emoji_map.json` به‌عنوان منبع پیش‌فرض | ✅ |
| فایل خراب/ناموجود → نگاشت داخلی، بدون crash | ✅ |
| فرمت تخت + شناسه نامعتبر → حذف امن | ✅ |
| fallback پس از ارسال برای پیام گوشی → `Send: EDITED` | ✅ |
| شکست ارسال → `Send: FAILED` در بلوک debug | ✅ |
| پرچم `CUSTOM_EMOJI_DEBUG=False` → بی‌صدا | ✅ |
| نشت توکن/سشن در بلوک debug → redact | ✅ |
| کلاینت Bot کامل دست‌نخورده | ✅ |
| پرچم‌های config (DEBUG/LOG_LEVEL/ADMIN_LOG_IDS/OUTGOING_FIX) | ✅ |

**کل سوئیت:** `753 passed / 6 failed` — هر ۶ خطا از قبل موجود و محیطی‌اند
(نبود credentials کاربر در sandbox تست؛ ارتباطی با این تغییرات ندارند؛
`30 error` قبلی به‌خاطر config ناقص sandbox بود و اکنون صفر است).

## ۶) اجرای زنده روی سرور مالک

```bash
# توکن ربات گزارش فقط از ENV
export PREMIUM_LOG_BOT_TOKEN='<توکن ربات گزارش>'

# تست شش‌سناریو با گزارش زنده به ربات:
python -m tools.premium_emoji_debug_smoke --session '<STRING_SESSION>' \
    --private <user_id> --group <group_id> --channel <channel_id>
```

هر پیام تبدیل‌شده بلوک‌های `[CustomEmoji]` (Chat/Emoji/ID/Entity/Send) و
`[PREMIUM DEBUG]` کامل را در `logs/premium_emoji.log` می‌نویسد و در سطح
`PREMIUM_EMOJI_LOG_LEVEL=DEBUG` به ربات گزارش هم می‌رود؛ هر خطا/فال‌بک در
سطح ERROR/WARNING همیشه گزارش می‌شود (پیش‌فرض `LOG_LEVEL=ERROR`).

## ۷) تضمین نهایی

- هیچ ایموجی ثابتی به هیچ پیامی اضافه نمی‌شود — سیستم Prefix با همهٔ
  زیرساختش (placeholder، `FALLBACK_DOCUMENT_ID`، کلیدهای config، تست‌ها)
  حذف شده و تست سورس‌اسکن آن را دائمی می‌کند.
- تبدیل فقط از نگاشت دقیق alt انجام می‌شود؛ ایموجیِ بدون نگاشت دست‌نخورده
  می‌ماند؛ هیچ خطایی پیام‌رسانی را نمی‌شکند.
- تمرکز اصلی محقق است: **Custom Emoji واقعی تلگرام با
  `MessageEntityCustomEmoji`** در همهٔ چت‌ها — نه ایموجی معمولی، نه prefix.
