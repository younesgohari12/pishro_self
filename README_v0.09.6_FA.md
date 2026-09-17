# نسخه 0.09.6 — Custom Emoji Manager برای Bot + Self

این نسخه مستقیماً روی `PishroSelf_v0.09.5_PREMIUM_EMOJI` ساخته شده و قابلیت قبلی ایموجی پرمیوم را به یک مدیر مشترک و کامل برای **ربات اصلی** و **پنل سلف (`.پنل`)** ارتقا می‌دهد. دیتابیس قبلی به‌صورت غیرمخرب Migration می‌شود و اطلاعات کاربران قبلی حذف نمی‌شود.

## پنل سلف (`.پنل`)

گزینه **✨ ایموجی پرمیوم** به منوی اصلی اضافه شده و شامل این بخش‌هاست:

- **✨ استخراج ایموجی** — دریافت پیام متنی یا کپشن عکس/ویدیو/فایل، بررسی `message.entities` و استخراج فقط `MessageEntityCustomEmoji`.
- **🧪 تست ایموجی** — دریافت `document_id` به‌عنوان ورودی و ارسال تست فقط با Entity واقعی `MessageEntityCustomEmoji`؛ عدد خام به‌عنوان خروجی تست ارسال نمی‌شود.
- **📦 ایموجی‌های ذخیره شده** — مشاهده و حذف ایموجی‌های همان مالک.

حالت منتظر ورودی سلف در SQLite نگهداری می‌شود تا Inline Bot و Self Client از یک state پایدار استفاده کنند. فرمان‌های معمولی سلف که با `.` شروع می‌شوند توسط state قدیمی مصرف نمی‌شوند.

## ربات اصلی

منوی **✨ ایموجی پرمیوم** شامل:

- **🔍 استخراج از پیام**
- **🧪 تست ID**
- **📦 لیست ایموجی‌ها**
- **🗑 حذف ایموجی**

استخراج از متن و کپشن مدیا انجام می‌شود. پیام بدون `MessageEntityCustomEmoji` خطای واضح دریافت می‌کند.

## سرویس مشترک

فایل جدید:

`services/custom_emoji_service.py`

مسئول تمام منطق مشترک است:

- `extract_custom_emojis(message)`
- `create_custom_emoji(text, document_id)`
- `send_custom_emoji(entity, ...)`
- resolve کردن `document_id` با `messages.getCustomEmojiDocuments`
- محاسبهٔ صحیح offset/length بر حسب UTF-16
- تأیید اینکه پیام خروجی واقعاً Entity موردنظر را حفظ کرده است

`services/emoji_extractor.py` برای سازگاری با کد قبلی به‌صورت shim باقی مانده و API را از سرویس جدید export می‌کند.

## دیتابیس و Migration

جدول `custom_emojis` اکنون این فیلدها را دارد:

- `id`
- `owner_id`
- `document_id`
- `emoji`
- `emoji_text` (برای سازگاری نسخه قبلی)
- `source_message_id`
- `created_at`

همچنین جدول `custom_emoji_flow` برای state ورودی Self/Bot ساخته می‌شود. Migration با `ALTER TABLE` فقط ستون‌های گمشده را اضافه و داده‌های قبلی را نگه می‌دارد.

در `main.py` نیز `init_custom_emojis_db()` قبل از بالا آمدن Bot/Self/Inline اجرا می‌شود.

## پنل مدیریت

ادمین اصلی می‌تواند از پنل مدیریت، لیست عمومی ایموجی‌های ذخیره‌شده را با `owner_id` ببیند و رکورد موردنظر را حذف کند. دسترسی به callbackهای مدیریت داخل گارد `ADMIN_ID` باقی مانده است.

## فایل‌های تغییرکرده نسبت به 0.09.5

فایل جدید:

- `services/custom_emoji_service.py`

فایل‌های تغییرکرده:

- `main.py`
- `self.py`
- `inline.py`
- `bot/core.py`
- `handlers/premium_emoji.py`
- `database/models.py`
- `services/emoji_extractor.py`
- `ui/keyboards.py`
- `tests/test_premium_emoji.py`

## نتیجه تست

- Custom Emoji tests: **52/52 PASS**
- Full regression: **278/278 PASS**
- Syntax/compileall: **PASS**
- Failure: **0**

محیط ساخت دسترسی شبکه برای نصب dependency یا اتصال زنده به تلگرام نداشت؛ بنابراین تست زنده با حساب/توکن واقعی انجام نشده است. Regression با mock/stub سازگار با API مورد استفادهٔ Telethon اجرا شده و کد پروژه با dependency اعلام‌شده در `requirements.txt` (`Telethon>=1.44,<2.0`) نگه داشته شده است.
