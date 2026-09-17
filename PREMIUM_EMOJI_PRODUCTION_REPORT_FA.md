# ارتقای Premium Emoji Engine

وضعیت تحویل: **Production Candidate — بخش فنی اصلاح شده؛ تأیید کامل Production-Ready هنوز انجام نشده است.**

دو مانع باقی است: بانکِ انتخاب‌شده بر اساس بررسی ظاهر هنوز دادهٔ تأییدشده ندارد، و شش شکست ازقبل‌موجودِ تست‌های پروژه همچنان باقی‌اند. محدودیت دسترسی مستقیم به Telegram API اجازهٔ شمارش واقعی سندهای معتبر/ردشده و بررسی تصاویر را در این محیط نداد. هیچ دادهٔ تست به‌عنوان تأیید ظاهر واقعی معرفی نشده است.

## تغییرات اجرایی

| فایل | تغییر |
| --- | --- |
| `services/premium_emoji_injector.py` | اولویت curated و variant، exact-smart، warm-up پس‌زمینه، cache محدود به تعداد و حجم، fallback امن مدیا، گزارش DEBUG و idempotency کل پیام |
| `services/custom_emoji_service.py` | تشخیص کامل توکن‌های emoji، دریافت batch اطلاعات رسمی، تفکیک valid/rejected/unresolved، جداسازی ID نامعتبر از batch، نگهداری نتیجهٔ batchهای کامل پس از timeout |
| `config.py` | پیش‌فرض `PREMIUM_EMOJI_STYLE="exact-smart"`؛ گزینهٔ فعال/غیرفعال و تنظیمات پایدار موجود حفظ شدند |
| `self.py` | فقط import تابع cleanup جدید و فراخوانی cleanup موتور هنگام خروج؛ اتصال قبلی injector پس از احراز هویت حفظ شده است |
| `tests/test_premium_emoji_injector.py` | حفظ تمام تست‌های قبلی؛ فرض تغییر glyph در تست‌های قبلی به opt-in صریح creative تبدیل شد |
| `tests/test_premium_emoji_production.py` | تست‌های تکمیلی اولویت، پوشش، exact/creative، رسانه، warm-up، cache، sequence و گزارش |
| `tests/test_storage_updates.py` | تست حفظ تنظیمات قبلی، جداول و اطلاعات حساب در ارتقا و اجرای مجدد |
| `tools/premium_emoji_live_smoke.py` | ابزار اختیاری metadata و نمونه‌های Saved Messages؛ خارج از CI اجباری |
| `tools/benchmark_premium_emoji.py` | benchmark تکرارپذیر و آفلاین، بدون ادعای تأیید ظاهر |

هیچ dependency جدیدی اضافه نشده است. فایل‌های login/session، database، security، billing، مسیرهای داده و سایر featureها بازنویسی نشده‌اند. cleanup تازه فقط task و cache همین قابلیت را پاک می‌کند.

## پوشش و وضعیت واقعی بانک

| معیار | نتیجه |
| --- | --- |
| فهرست ایموجی‌های رایج و استخراج‌شده از متن‌های runtime | ۱۸۰ Unicode emoji |
| محدودیت تشخیص به همین فهرست | ندارد؛ matcher عمومی از emojiهای کامل دیگر، modifier، flag، keycap، variation selector، tag و ZWJ نیز پشتیبانی می‌کند |
| IDهای منبع قبلی | هر ۲۱۰ ID، بدون افزودن یا حذف، حفظ شده‌اند |
| ID معتبر در Telegram زنده | نامشخص؛ metadata زنده در این محیط دریافت نشده |
| ID ردشده در Telegram زنده | نامشخص؛ خطای شبکه به‌معنای ID نامعتبر نیست |
| IDهای دارای تأیید بصری و اولویت curated در این تحویل | **صفر** |
| mapping نهایی تأییدشدهٔ Unicode → IDs | **هنوز موجود نیست**؛ فایل `PREMIUM_EMOJI_MAPPING_STATUS.json` وضعیت هر Unicode را مشخص می‌کند |

منبع مجاز IDها همان [CustomEmojiPack](https://t.me/s/CustomEmojiPack) است. لیست قبلی حفظ شده، اما صرف انتشار در کانال اثبات نمی‌کند ID هنوز معتبر است یا کدام شکل/رنگ/سبک را دارد.

`CURATED_PREMIUM_MAP` و `CURATED_VARIANT_MAP` مسیر واقعی اولویت‌دهی دارند، اما ورودی‌های آن‌ها عمداً تا تأیید واقعی خالی‌اند. شکل، رنگ، جذابیت یا دسته‌های dark/cute/neon/gang از روی عدد ID یا اسم پک حدس زده نشده‌اند. بنابراین داشتن چند variant واقعاً انتخاب‌شده برای هر emoji هنوز تکمیل نشده؛ این نسخه چنین ادعایی ندارد.

پس از warm-up، fallback تنها از اسناد اعتبارسنجی‌شده‌ای استفاده می‌کند که **alt دقیقاً یکسان** دارند. در نبود mapping curated، اولویت با animated/video و سپس ترتیب منبع است؛ مقدار عددی document_id هرگز معیار زیبایی نیست. در حالت curated، ترتیب صریح لیست بر animated بودن مقدم است. فهرست variant تأییدشدهٔ مرتبط با متن، سپس فهرست عمومی curated و بعد fallback استفاده می‌شود. تا وقتی فهرست variant پر نشده، کلیدواژهٔ «گنگ» یا «عاشقانه» به‌تنهایی ظاهر تازه‌ای ایجاد نمی‌کند.

## معنی پیام و entityها

- پیش‌فرض `exact-smart` و alias قدیمی `smart` فقط همان glyph/alt را پرمیوم می‌کنند. نبود نمونهٔ دقیق باعث حفظ Unicode اصلی می‌شود؛ برای مثال ✅ خودکار به 🔥 تبدیل نمی‌شود.
- جایگزینی هم‌معنی فقط با `PREMIUM_EMOJI_STYLE="creative"` انجام می‌شود.
- مقایسهٔ پیش‌فرض حتی variation selector را حفظ می‌کند؛ وجود Premium ❤️ الزاماً به معنی تبدیل ❤ بدون variation selector نیست.
- اگر حتی یک `MessageEntityCustomEmoji` در پیام وجود داشته باشد، کل آن پیام از تزریق مجدد صرف‌نظر می‌کند.
- Bold، Italic، Underline، Strike و Spoiler حفظ می‌شوند. TextUrl، Url، Code، Pre و CustomEmoji موجود بازنویسی نمی‌شوند.
- Markdown/HTML ابتدا با parser فعلی Telethon به متن و entity تبدیل می‌شوند. entityهای صریح حتی اگر لیست خالی باشند محترم‌اند.
- offset/length با UTF-16 محاسبه و مرز surrogateها بررسی می‌شود. در creative نیز entityهای بعدی و span دربرگیرنده جابه‌جا می‌شوند.
- توکن مرکب فقط به‌صورت کامل قابل تبدیل است؛ تطابق با یک تکه از ZWJ، skin tone، keycap یا tag نادیده گرفته می‌شود.

الزام تطابق entity با alt سند از [مستند رسمی Telegram](https://core.telegram.org/api/custom-emoji) گرفته شده است.

## مسیر ارسال و خطاها

همان کلاینت متصل Self برای send_message، send_file/caption، send_message(file=...)، edit_message، event.reply/respond و Message.edit پوشش دارد. خروجی AI، ترجمه، ارز، پاسخ‌های خودکار/دشمن و copy-mode تبچی از همین کلاینت عبور می‌کنند. forward واقعی و InlineResult.click دستکاری نشده‌اند.

- `DocumentInvalidError` برای مدیا، چه send_file باشد چه send_message(file=...) یا پیام حاوی media، باعث ارسال مجدد نمی‌شود.
- آلبوم‌ها، از جمله ورودی generator، به‌صورت کامل replay نمی‌شوند.
- در ارسال/ویرایش بدون media، یا خطای صریح entity در ارسال تکی، تنها یک fallback به ورودی اصلی مجاز است.
- Timeout، FloodWait و خطاهای عمومی تکرار ارسال ایجاد نمی‌کنند. پاسخ با entity حذف‌شده توسط Telegram نیز خودکار دوباره فرستاده نمی‌شود.
- این قواعد تضمین شبکهٔ exactly-once نیستند؛ از retry اضافه‌ای که این موتور ممکن بود ایجاد کند جلوگیری می‌کنند.

## warm-up، cache و log

warm-up پس از اتصال معتبر، قبل از ثبت handlerها زمان‌بندی می‌شود و startup/ارسال را منتظر شبکه نمی‌گذارد. تا آماده‌شدن snapshot، Unicode اصلی ارسال می‌شود. scrape کانال در مسیر ارسال وجود ندارد؛ فقط IDهای همراه کد با `GetCustomEmojiDocumentsRequest` resolve می‌شوند.

کاتالوگ مشترک است؛ batch حداکثر ۱۰۰ ID، سقف ۶۴ درخواست برای جداسازی اسناد خطادار و deadline کلی ۳۰ ثانیه دارد. نتایج کامل batchهای قبلی در timeout حفظ می‌شوند. اعتبارسنجی موفق تا ۲۴ ساعت cache می‌شود؛ شکست یا نتیجهٔ ناقص حداقل پنج دقیقه cooldown دارد و FloodWait بزرگ‌تر رعایت می‌شود. جداسازی ID نامعتبر تعداد درخواست‌ها را بی‌حد نمی‌کند.

cache برنامهٔ تزریق برای هر کلاینت حداکثر ۲۵۶ ورودی و ۲۵۶ KiB حجم محاسبه‌شده نگه می‌دارد. این سقف به معنی کل RSS پردازش نیست. cache نتایج انتخاب برای Unicodeهای ناموجود رشد نمی‌کند. کاتالوگ پس از پایان warm-up مرجع کلاینت را آزاد می‌کند و task نیمه‌تمام هنگام قطع همان حساب لغو می‌شود. فایل cache دائمی ایجاد نمی‌شود.

`diagnostic_selection` در DEBUG، Unicode، document_id، alt، free، نوع animated/video/static، اولویت منبع و style را ثبت می‌کند. پیام کاربر یا credential ثبت نمی‌شود و انتخاب تکراری تا ظرفیت ۲۵۶ مورد log تکراری ندارد. در سطح معمول production، این logهای انتخاب خاموش‌اند؛ فقط خلاصهٔ warm-up با تعدادها ثبت می‌شود.

## اجرای اختیاری بررسی زنده

از ریشهٔ پروژه و با محیط نصب‌شده:

```bash
python tools/premium_emoji_live_smoke.py --session-string-file /path/to/existing-session.txt --report emoji-live-report.json
```

این دستور فقط metadata می‌گیرد. برای حداکثر پنج نمونه در Saved Messages همان حساب:

```bash
python tools/premium_emoji_live_smoke.py --session-string-file /path/to/existing-session.txt --send-samples --report emoji-live-samples.json
```

`--ids` انتخاب چند ID از لیست منبع را ممکن می‌کند. فایل گزارش باید جدید باشد. ابزار از session موجود در حافظه استفاده می‌کند و start/sign_in/logout، migration یا نوشتن session ندارد. هیچ live smoke-test در این محیط اجرا نشده و در workflow CI نیز اضافه نشده است.

گزارش زنده شامل شمارش معتبر/ردشده/نامشخص، علت‌ها، alt/free/media_kind، mapping و وضعیت حفظ entity نمونه‌هاست. پس از مشاهدهٔ نمونه‌های واقعی می‌توان IDها را به ترتیب پسند در `CURATED_PREMIUM_MAP` گذاشت و فقط variantهای بررسی‌شده را در `CURATED_VARIANT_MAP` ثبت کرد. warm-up بعدی مجدداً تعلق ID به منبع و تطابق alt را بررسی می‌کند. خروجی metadata یا یک تصویر برای تأیید ظاهر کافی است؛ نیازی به ارسال فایل session نیست.

## تست‌ها و benchmark

نتیجهٔ عددی اجرای نهایی در `PREMIUM_EMOJI_PRODUCTION_TEST_RESULTS.txt` و دادهٔ اندازه‌گیری در `PREMIUM_EMOJI_BENCHMARK.json` است. تست‌های قبلی حذف نشده‌اند. تست‌های آفلاین با mock و fixture فقط رفتار را بررسی می‌کنند و هیچ ID/alt تستی مدرک ظاهر واقعی نیست.

شش شکست تاریخی خارج از محدودهٔ این تغییر باقی‌اند:

1. `test_admin_user_pagination.py::test_admin_users_page_two`
2. `test_hardening_v097.py::test_config_is_python_only_and_validates_without_values`
3. `test_memory_streaming_lock_pagination.py::test_iter_user_ids_does_not_hold_lock_while_consuming`
4. `test_memory_streaming_lock_pagination.py::test_large_user_pagination_is_page_bounded`
5. `test_release_regression.py::test_release_markdown_escape`
6. `test_user_streaming.py::test_broadcast_can_consume_iterator`

Python 3.12.14 و Telethon 1.44.0 در این محیط آزموده شده‌اند؛ آزمون زندهٔ Telegram، بار واقعی سرور، Python 3.11/3.13 و پوشش واقعی تمام Unicodeها ادعا نمی‌شود. وضعیت premium از هویت زمان اتصال گرفته می‌شود؛ پس از تغییر اشتراک، اتصال مجدد برای تازه‌شدن آن لازم است. سقف محافظه‌کارانهٔ این نسخه ۱۰۰ custom entity در هر پیام است؛ محدودیت واقعی سرور همچنان اعمال می‌شود.


### عددهای اندازه‌گیری‌شدهٔ نهایی

- کل مجموعه: **۵۵۲ موفق و همان ۶ شکست قبلی**، ۱۰٫۷۲ ثانیه؛ ۹۴ تست موتور ایموجی در این مجموعه موفق‌اند.
- پردازش پیام کوتاه با cache: میانهٔ **۱۰٫۵۶ µs** و P95 برابر ۱۱٫۱۴ µs.
- پردازش بدون cache: میانهٔ **۳۶٫۴۹ µs** و P95 برابر ۵۲٫۴۰ µs.
- سناریوی پیام طولانی: ۲۰ ورودی cache، حجم محاسبه‌شدهٔ ۲۵۷٬۷۶۰ بایت؛ تخصیص باقی‌ماندهٔ اندازه‌گیری‌شدهٔ ۲۷۱٬۹۰۹ بایت و اوج ۵۶۲٬۸۶۵ بایت.
- این اعداد برای یک کلاینت، همین ماشین و fixtureهای metadata ساختگی‌اند؛ هزینهٔ شبکه و RSS کل برنامه را اندازه نمی‌گیرند.

گزارش‌ها و SHA256های بدون پسوند PRODUCTION مربوط به تحویل قبلی‌اند. مرجع این تغییر `PREMIUM_EMOJI_PRODUCTION_SHA256.json` است.
