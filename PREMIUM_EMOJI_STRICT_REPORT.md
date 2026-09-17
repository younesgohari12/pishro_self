# گزارش نهایی Premium Emoji Converter — نسخه Strict Mapping (V2)

پروژه: **PishroSelf v0.09.13 — Premium Emoji Converter (Strict)**
تاریخ اجرای resolve زنده: 2026-09-17 | متد رسمی: `messages.getCustomEmojiDocuments`
منبع شناسه‌ها: فقط پک‌های عمومی https://t.me/CustomEmojiPack

---

## ۱) خلاصه اجرایی

نسخه قبلی برای ایموجی‌هایی که شناسه اختصاصی نداشتند از یک شناسه ثابت
(`FALLBACK_DOCUMENT_ID = 5938388342281343001`) به‌عنوان fallback عمومی
استفاده می‌کرد؛ در نتیجه ممکن بود چند ایموجی مختلف به یک Custom Emoji
یکسان تبدیل شوند و معنی پیام عوض شود. در این نسخه قانون **تطبیق معنایی
اجباری (Strict)** اعمال شد: هر ایموجی فقط به Custom Emoji‌ای تبدیل می‌شود
که `DocumentAttributeCustomEmoji.alt` آن از خود تلگرام دقیقاً همان ایموجی
باشد. در غیر این صورت ایموجی اصلی دست‌نخورده می‌ماند.

یافته کلیدی resolve زنده: alt واقعی همان شناسه fallback از خود تلگرام
«🙄» است — یعنی قبلاً تبدیل 😂🔥❤️ به آن، عملاً نمایش «🙄»
بود. این شناسه طبق قانون جدید از نگامت مبدل **کاملاً حذف شد** و فقط برای
قابلیت مستقل Premium Prefix (طبق دستور: دست‌نخورده) و حالت اضطراری
`PREMIUM_EMOJI_STRICT_MODE=False` نگه داشته شده است.

## ۲) آمار resolve (واقعی، قابل تکرار، بدون حدس)

راستی‌آزمایی مستقل نهایی: ابزار رسمی `tools/resolve_premium_emoji_mapping.py` دوباره
با `messages.getCustomEmojiDocuments` اجرا شد و خروجی تازه جایگزین فایل resolve شد
(همان روش و همان منبع؛ خروجی: `PREMIUM_EMOJI_RESOLVED_MAPPING.json`).

| شاخص | مقدار (اجرای زنده) |
|---|---|
| document_id موجود در پروژه (کانال + نگامت + fallback + prefix) | 638 |
| document_id با پاسخ واقعی تلگرام (resolved) | 638 (missing=0) |
| شناسه با alt دقیقاً برابر یکی از ۳۱ هدف | 433 |
| شناسه انتخاب‌شده برای نگامت نهایی (سقف ۱۵ برای هر ایموجی) | 433 |
| ایموجی فعال (alt تأییدشده دارد) | 30 از ۳۱ |
| ایموجی غیرفعال (هیچ شناسه‌ای با آن alt در منابع پروژه نیست) | 1 (🚀) |
| نسبت‌گذاری اشتباه در نگامت فعلی | 0 (drift صفر) |

در همین اجرا، نگامت زندهٔ خروجی ابزار با `PREMIUM_EMOJI_MAP` ارسالی مقایسه شد:
**100% IDENTICAL** — هر ۴۳۳ شناسه نگامت، alt زنده دقیقاً برابر دارد و هیچ شناسه‌ای
بین دو ایموجی مشترک نیست. `FALLBACK_DOCUMENT_ID=5938388342281343001` طبق پاسخ زندهٔ
تلگرام alt «🙄» دارد و به همین دلیل به هیچ کلیدی از نگامت منصوب نیست.

سه وضعیت طبق spec: **alt موجود → فعال** | **alt پیدا نشد → غیرفعال** |
**alt اشتباه → reject** (هیچ‌کدام جز active وارد نگامت نمی‌شوند).

## ۳) نگامت نهایی (نمونهٔ درخواستی + بقیه)

- 😂 → `6298555228752971886` (+14 شناسه تأییدشده دیگر)
- 🔥 → `6123027087960843224` (+14 شناسه تأییدشده دیگر)
- ❤️ → `6028444945960932052` (+14 شناسه تأییدشده دیگر)
- 👑 → `6014729749585206092` (+14 شناسه تأییدشده دیگر)
- 💎 → `6271537028307881531` (+14 شناسه تأییدشده دیگر)
- 🚀 → **غیرفعال** (هیچ Custom Emoji با alt دقیقاً 🚀 در منابع پروژه پیدا نشد؛ طبق قانون همان ایموجی معمولی می‌ماند)
- ✨ → `6028070583726510691` (+14 شناسه تأییدشده دیگر)

نگامت کامل ۳۱ ایموجی در `premium_emoji_mapping.py` و مبنای هر شناسه در
`PREMIUM_EMOJI_RESOLVED_MAPPING.json` (فایل resolve رسمی ابزار) آمده است.

<details><summary>لیست کامل ایموجی‌های فعال و تعداد شناسه هرکدام</summary>

| ایموجی | تعداد شناسه فعال | اولین شناسه |
|---|---|---|
| 😂 | 15 | `6298555228752971886` |
| 🔥 | 15 | `6123027087960843224` |
| ❤️ | 15 | `6028444945960932052` |
| 😍 | 15 | `6298565175897229863` |
| 🥰 | 15 | `5933681912823421887` |
| 😘 | 15 | `6030599524894905379` |
| 😎 | 15 | `6314319661399806487` |
| 😈 | 15 | `6044389303377730298` |
| 😭 | 15 | `6028293496824141193` |
| 😡 | 15 | `6298728453373953912` |
| 🤔 | 15 | `6314250542491109860` |
| 👍 | 15 | `6307649242181668400` |
| 👎 | 15 | `6041716699848249286` |
| 🙏 | 15 | `5911159851647505885` |
| 💯 | 6 | `5938074049459523495` |
| 👑 | 15 | `6014729749585206092` |
| 💎 | 15 | `6271537028307881531` |
| ✨ | 15 | `6028070583726510691` |
| ⚡ | 15 | `6147565292684843119` |
| 🎉 | 13 | `6041731551845159060` |
| 🤝 | 9 | `5990343805746814903` |
| 💔 | 15 | `6249216517062268463` |
| 😀 | 15 | `6147700446715715515` |
| 🤣 | 15 | `6044385416432327843` |
| 😢 | 15 | `6296341890371422476` |
| 😱 | 15 | `6298426688971736948` |
| 😴 | 15 | `6298513503145691368` |
| 😏 | 15 | `5996563287758610826` |
| 🤬 | 15 | `6048379293635975213` |
| 💪 | 15 | `5942724489723777520` |

</details>

## ۴) تغییرات فنی این پچ

| فایل | تغییر |
|---|---|
| `config.py` | `PREMIUM_EMOJI_STRICT_MODE = True` (پیش‌فرض روشن؛ خاموشی فقط برای بازگشت موقت به رفتار قدیمی) |
| `premium_emoji_mapping.py` | بازنویسی کامل: ۳۱ کلید بررسی‌شده؛ فقط alt-تأییدشده؛ fallback از نگامت حذف؛ `CHECKED_EMOJIS` و `normalize_emoji` اضافه شد |
| `services/premium_emoji_converter.py` | منطق Strict: استخر خالی → بدون تبدیل (هرگز fallback عمومی)؛ `strict` پارامتر موتور + خواندن از config؛ idempotent و UTF-16 قبلی حفظ شد |
| `tools/resolve_premium_emoji_mapping.py` | ابزار رسمی: بررسی همه ۶۳۸ شناسه پروژه + candidates کانال؛ خروجی JSON با document_id/alt/free/animated/media_type و وضعیت active/inactive/rejected. در این نسخه باگ NameError مسیر شبکه (import جاافتاده `GetCustomEmojiDocumentsRequest`) رفع شد و خروجی ابزار حالا رکورد `fallback_document_id` را هم تولید می‌کند |
| `tools/premium_emoji_strict_smoke.py` | اسموک: تبدیل واقعی سه پیام نمونه + تأیید زندهٔ alt همان شناسه‌ها از تلگرام |
| `PREMIUM_EMOJI_RESOLVED_MAPPING.json` | خروجی رسمی resolve (مبنای نگامت + سابقهٔ reject شناسه fallback) — با اجرای زندهٔ ابزار بازتولید و متادیتای آن با آمار واقعی سازگار شد |
| `tests/test_premium_emoji_strict.py` | ۳۲ تست Strict (۱۵ دستهٔ درخواستی + تست تصمیم fallback) |
| `tests/test_premium_emoji_converter.py` | به‌روزرسانی به نگامت جدید + تست fallback قدیمی فقط در strict=False |

### چیزهایی که عمداً تغییر نکردند

- معماری Self/Bot: مبدل فقط روی کلاینت Self نصب می‌شود؛ Bot Token هرگز.
- دکمه «🎨 Premium Emoji» پنل سلف و ذخیره‌سازی انتخاب هر حساب در دیتابیس.
- قابلیت **Premium Prefix** کاملاً مستقل؛ `PREMIUM_EMOJI_PREFIX_IDS` دست نخورد.
- `forward_messages` بدون wrap؛ فوروارد بایت‌به‌بایت همان است.
- رسانه بدون caption هیچ پیام اضافه‌ای نمی‌سازد؛ caption تبدیل می‌شود.
- حفظ کامل Bold/Italic/Underline/Strike/Spoiler/URL/Code/Pre و Custom Emojiهای موجود؛ آفست‌ها UTF-16.

## ۵) تست‌ها

| گروه | نتیجه |
|---|---|
| `tests/test_premium_emoji_strict.py` | ۳۲ پاس |
| `tests/test_premium_emoji_converter.py` | ۳۸ پاس |
| کل ناحیه Premium Emoji (۵ suite) | ۲۲۳ پاس، ۰ شکست |
| کل پروژه | ۷۶۲ پاس |
| خطاهای پایه (تست‌های stale/محیطی نامرتبط با پچ) | ۶ — بدون regression در ناحیه ایموجی |

پوشش دستور تستی پچ: strict mapping، wrong alt rejection، fallback disabled،
😂/🔥/❤️ exact conversion، unknown untouched، bot untouched، self converted،
UTF-16 validation، markdown، HTML، caption، existing custom emoji،
duplicate prevention — همه ✅ (جزئیات: `PREMIUM_EMOJI_STRICT_TEST_RESULTS.txt`).

## ۶) Smoke Test

اجرای `tools/premium_emoji_strict_smoke.py` با موتور واقعی و تأیید زندهٔ
alt از تلگرام:

این اسموک در محیط ساخت هم با توکن بات مالک (از ENV) اجرا شد؛ برای هر entity،
شناسه با `messages.getCustomEmojiDocuments` از تلگرام پرسیده شد و alt زنده دقیقاً
برابر ایموجی هدف بود:

```text
offline guards: OK (idempotent, no fallback, text intact)
✉️ سلام 😂🔥❤️
   ✅ 😂 → document_id=6298555228752971886  telegram_alt=😂
   ✅ 🔥 → document_id=6123027087960843224  telegram_alt=🔥
   ✅ ❤️ → document_id=6028444945960932052  telegram_alt=❤️
✉️ عالی شد 👑💎
   ✅ 👑 → document_id=6014729749585206092  telegram_alt=👑
   ✅ 💎 → document_id=6271537028307881531  telegram_alt=💎
✉️ دمت گرم 🚀✨
   ✅ ✨ → document_id=6028070583726510691  telegram_alt=✨
   ⚪ 🚀 بدون تبدیل (تأییدشده نیست → همان ایموجی معمولی)
checks=6 failures=0
```

ارسال واقعی با سشن پرمیوم مالک: `python -m tools.premium_emoji_smoke --session <SESSION> --chat 8359698350`
(در محیط ساخت، سشن واقعی پرمیوم موجود نیست؛ ابزار داخل ZIP آماده است.)

## ۷) نکته برای مالک درباره Prefix

شناسه `5938388342281343001` که در `PREMIUM_EMOJI_PREFIX_IDS` است، طبق
resolve زنده تلگرام alt «🙄» دارد. قابلیت Prefix طبق دستور پچ
دست نخورده است؛ اما اگر خروجی Prefix فعلاً «{alt}» نمایش می‌دهد این علتش
است. در صورت تمایل، لیست Prefix را با شناسه‌های تأییدشده همین گزارش
(مثلاً ✨ یا 👑) جایگزین کنید.

## ۸) بستهٔ انتشار

- `PishroSelf_v0.09.13_PREMIUM_EMOJI_STRICT_FINAL.zip`
- بدون: BOT_TOKEN، API_HASH، API_ID، session files، .env، pycache، لاگ‌ها
- نصب: مقدارهای واقعی را در `PishroSelfData/config.py` نگه دارید (طبق روال قبل).
- مبدل از پنل سلف: «🎨 Premium Emoji» (پیش‌فرض انتشار در config: خاموش).
