# گزارش Premium Emoji Converter — PishroSelf v0.09.13

تاریخ: 2026-09-16 | مجری: Senior Python/MTProto Agent | مالک: Younes

## خلاصه

سیستم **Premium Emoji Converter** به سلف اضافه شد: وقتی فعال باشد، هر ایموجی
معروف داخل متنی که **اکانت کاربری (Self)** ارسال می‌کند با `MessageEntityCustomEmoji`
به Custom Emoji واقعی تلگرام تبدیل می‌شود؛ متن و تمام فرمت‌ها حفظ می‌شوند.

- خروجی ربات‌ها (پنل، helperproselfbot، ربات اصلی) **هیچ تغییری نمی‌کند**.
- پیام‌های Forward **دست‌نخورده** می‌مانند.
- سیستم **idempotent** است؛ ایموجی زیر Custom Emoji موجود دوباره تبدیل نمی‌شود.

## منبع شناسه‌ها (بدون هیچ حدسی)

- فقط پک‌های عمومی `https://t.me/CustomEmojiPack` (شناسه‌های ۲۱۰گانه موجود در
  `CHANNEL_DOCUMENT_IDS` همین پروژه).
- در 2026-09-16 تمام ۲۱۰ شناسه با متد رسمی `messages.getCustomEmojiDocuments`
  از خود تلگرام resolve شد و `alt` واقعی هر سند خوانده شد
  (`PREMIUM_EMOJI_CONVERTER_RESOLVED_IDS.json`).
- شناسه‌های **تأییدشده با alt واقعی** برای ۵ ایموجی پیدا شد:
  `❤️` (۲ شناسه)، `👑`، `😘`، `😭`، `✨`.
- سایر ایموجی‌ها به `FALLBACK_DOCUMENT_ID = 5938388342281343001`
  (تأیید چشمی مالک) ارجاع می‌شوند. هیچ شناسه‌ای حدس زده نشده است.

## فایل‌های جدید

| فایل | نقش |
| --- | --- |
| `premium_emoji_mapping.py` | نقشه ۳۰ ایموجی → شناسه‌ها + fallback رسمی |
| `services/premium_emoji_converter.py` | موتور تبدیل + بسته‌بندی send/edit/caption |
| `services/premium_report.py` | گزارش ادمین — فقط با ENV `PREMIUM_REPORT_BOT_TOKEN` |
| `tools/send_premium_report.py` | CLI ارسال گزارش نهایی به `ADMIN_REPORT_ID` |
| `tools/premium_emoji_smoke.py` | Smoke Test واقعی روی اکانت سلف |
| `tests/test_premium_emoji_converter.py` | ۳۶ تست پذیرش آفلاین |

## فایل‌های تغییرکرده

| فایل | تغییر |
| --- | --- |
| `self.py` | نصب/حذف مبدل فقط روی کلاینت سلف (بعد از `install_premium_prefix`) |
| `inline.py` | دکمه `🎨 Premium Emoji: روشن/خاموش` در پنل سلف + callback toggle |
| `db.py` | کلید تنظیمات `premium_emoji_converter` (None = پیش‌فرض config) |
| `config.py` | `PREMIUM_EMOJI_CONVERTER_ENABLED=False` + `PREMIUM_EMOJI_MODE="round_robin"` |
| `main.py` | آپدیت پیکربندی نشان‌دار `v0.09.13-premium-emoji-converter` (فقط کلیدهای جدید) |

## رفتار

- **محدوده تغییر:** فقط `send_message` / `send_file` (کپشن) / `edit_message` /
  album / reply / respond / خروجی AI / ترجمه / ارز — چون همه به همین متدهای
  Telethon می‌رسند. هرگز outgoing handler نصب نمی‌شود.
- **Formatting:** Bold/Italic/Underline/Strike/Spoiler/URL/Code/Pre و Custom
  Emoji قبلی با آفست صحیح UTF-16 حفظ می‌شوند؛ متن هرگز تغییر نمی‌کند.
- **Fallback:** اگر تلگرام Entity را رد کند (PremiumAccountRequired و امثال آن)،
  پیام اصلی دقیقاً یک بار بدون Entity تکرار می‌شود؛ برای آلبوم رسانه‌ای که بخشی
  از آن commit شده، retry ممنوع است. بعد از ردشدن، مبدل ۵ دقیقه خنک می‌شود تا
  retry loop ایجاد نشود. Duplicate تولید نمی‌شود.
- **بهره‌وری:** بدون شبکه روی مسیر ارسال، بدون کش سنگین — فقط یک ایندکس
  round_robin به‌ازای هر ایموجی؛ حالت `random` کنترل‌شده با RNG ایزوله.
- **سقف:** حداکثر ۵۰ تبدیل در هر پیام، حداکثر ۱۵ شناسه برای هر ایموجی.

## تست‌ها

`tests/test_premium_emoji_converter.py` — ۳۶ تست، همه پاس:

- تبدیل پیام سلف + حفظ متن؛ پیام ربات دست‌نخورده حتی با تلاش برای نصب
- نقشه کامل ۳۰ ایموجی؛ شناسه‌های تأییدشده؛ fallback برای ورودی نامعتبر
- ساخت `MessageEntityCustomEmoji` واقعی؛ آفست UTF-16 با متن astral و ZWJ
- Markdown و HTML (Bold/Italic/Strike/Underline) سالم با آفست درست
- کپشن و آلبوم؛ رسانه بدون کپشن هیچ پیام جدیدی نمی‌سازد
- Custom Emoji موجود دوباره تبدیل نمی‌شود؛ اجرای تکراری idempotent
- کد/پیش‌کد/لینک/بازة لفظی محافظت می‌شوند
- forward_messages بسته‌بندی نمی‌شود؛ خطاهای نامرتبط retry نمی‌شوند؛ cooldown
- دکمه پنل (روشن/خاموش) + persistence تنظیمات در دیتابیس
- گزارش ادمین فقط ENV؛ بدون توکن no-op؛ مقدار توکن در هیچ فایلی نیست

کل مجموعه: **۷۰۸ پاس**. ۲۶ خطای موجود که قبل از این تغییرات هم در ZIP پایه
وجود داشتند (تست‌های قرارداد نسخه‌های قبلی مانند `os.getenv` در config و
pagination/ترجمه‌ی محیط‌محور) بدون تغییر باقی‌اند؛ مجموعه شکست‌ها با baseline
بایت‌به‌بایت یکسان است (diff صفر).

## اجرا و تنظیم

1. ZIP را در پوشه جدید استخراج و مثل همیشه اجرا کنید؛ دیتای قبلی حفظ می‌شود.
2. در سلف دستور `.پنل` را بزنید → دکمه **🎨 Premium Emoji** را روشن کنید.
3. پیش‌فرض انتشار `False` است؛ انتخاب پنل بر آن اولویت دارد و ماندگار است.

## Smoke Test واقعی (روی حساب پرمیوم مالک)

تست خودکار آفلاین است و ادعای لاگین واقعی ندارد. برای تأیید زنده:

```bash
python -m tools.premium_emoji_smoke --session '<SESSION_STRING>' --chat 8359698350 --keep
```

سه پیام `سلام 😂🔥❤️` / `عالی شد 👑💎` / `دمت گرم 🚀✨` ارسال و Entityهای
Custom Emoji واقعیِ پیام ارسال‌شده راستی‌آزمایی می‌شود؛ ربات هیچ نقشی در
ارسال ندارد.

## گزارش ادمین

فقط با متغیر محیطی — بدون ذخیره توکن در کد:

```bash
PREMIUM_REPORT_BOT_TOKEN='<token>' python -m tools.send_premium_report \
  --report build_report.json --zip dist/PishroSelf_v0.09.13_PREMIUM_EMOJI_FINAL.zip
```
