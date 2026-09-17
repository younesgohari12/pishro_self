# وضعیت مرحلهٔ Live Validation و Visual Curation

این بسته همچنان **Production Candidate** است و Final نیست. ابزار بررسی و
ثبت انتخاب‌های انسانی تکمیل شده، اما در محیط توسعه اکانت متصل Telegram در
دسترس نبود. هیچ `GetCustomEmojiDocumentsRequest` زنده‌ای اجرا نشد و هیچ نمونه‌ای
به Saved Messages ارسال یا از نظر ظاهر تأیید نشد. صفحهٔ عمومی کانال برای مشاهدهٔ
نمونه‌ها ارجاع به Telegram می‌دهد؛ متن این صفحه جایگزین metadata و visual review نیست.

منبع مجاز: https://t.me/CustomEmojiPack
مرجع متد: https://core.telegram.org/method/messages.getCustomEmojiDocuments

## آمار واقعی این تحویل

| مورد | نتیجه |
|---|---:|
| source ID | ۲۱۰، همان فهرست قبلی |
| valid ID واقعی | نامشخص؛ بررسی زنده انجام نشده |
| rejected ID واقعی | نامشخص؛ بررسی زنده انجام نشده |
| unresolved ID | ۲۱۰؛ همگی هنوز درخواست نشده‌اند |
| alt واقعی resolve‌شده | نامشخص |
| Unicode emoji با انتخاب curated | ۰ |
| visually approved ID | ۰ |
| variant mapping | ۰ |
| ایموجی‌های ضروری در شرط انتشار | ۵۰ Unicode دقیق، شامل ❤ و ❤️ به‌صورت جدا |

`valid_ids=[]` در گزارش نبودن نتیجهٔ معتبرِ دریافت‌شده را نشان می‌دهد، نه اثبات
نامعتبر بودن ۲۱۰ ID. شمارش valid/rejected در فایل وضعیت `null` است. همهٔ metadataهای
نامعلوم `null` هستند و `live_metadata_attempted=false` حفظ شده است.

| Unicode | selected IDs واقعی |
|---|---|
| 🔥 | تعیین نشده |
| ❤️ | تعیین نشده |
| 😂 | تعیین نشده |
| 😎 | تعیین نشده |
| 👑 | تعیین نشده |
| 💎 | تعیین نشده |
| 🖤 | تعیین نشده |
| 👍 | تعیین نشده |
| 🙏 | تعیین نشده |
| 🫡 | تعیین نشده |
| 🫶 | تعیین نشده |
| 💯 | تعیین نشده |

## فایل‌های این مرحله

تغییرکرده نسبت به Candidate قبلی:

- `tools/premium_emoji_live_smoke.py`: حالت curation، گروه‌بندی exact-alt، worksheet،
  اعتبارسنجی review، خروجی mapping قابل بازبینی، batch کوچک و کنترل FloodWait.
- `PREMIUM_EMOJI_MAPPING_STATUS.json`: شمارش صادقانهٔ موارد نامعلوم و mappingهای لازم.

اضافه‌شده:

- `tests/test_premium_emoji_curation.py`: ۳۵ تست جدید؛ فقط logic و دادهٔ mock.
- `PREMIUM_EMOJI_LIVE_VALIDATION.json`: وضعیت فعلی ۲۱۰ ID، بدون metadata ساختگی.
- `PREMIUM_EMOJI_CURATION.json`: وضعیت خالی و مسدود فعلی؛ تأیید بصری ندارد.
- `PREMIUM_EMOJI_CURATION_REPORT_FA.md`: این گزارش و دستورهای اجرای زنده.
- `PREMIUM_EMOJI_CURATION_TEST_RESULTS.txt`: نتیجهٔ اجرای suite.
- `PREMIUM_EMOJI_CURATION_SHA256.json`: hash محتوای بستهٔ این مرحله.

`services/premium_emoji_injector.py`، `services/custom_emoji_service.py`، `self.py` و
`config.py` عیناً حفظ شده‌اند. هیچ تغییری در login، session، database، security،
billing، مسیرهای ارسال، ساختار cache یا dependencyهای پروژه ایجاد نشده است.
تست‌ها و گزارش‌های نسخه‌های قبلی حذف نشده‌اند؛ گزارش‌های قبلی سابقهٔ همان نسخه‌اند.

## اجرای واقعی روی میزبان اکانت

از پوشهٔ پروژه و محیط Python دارای dependencyهای موجود اجرا کنید. پارامتر session
مسیر فایل StringSession موجود خودتان است؛ ابزار login نمی‌کند و فایل را تغییر نمی‌دهد.
پوشهٔ خروجی باید جدید باشد. فرمان اول فقط metadata می‌گیرد:

```bash
python tools/premium_emoji_live_smoke.py \
  --session-string-file /path/to/existing-user-session.txt \
  --curation --output-dir /path/to/emoji-metadata-run
```

مقایسهٔ همهٔ candidateهای معتبر در Saved Messages، حداکثر پنج پیام بین مکث‌ها:

```bash
python tools/premium_emoji_live_smoke.py \
  --session-string-file /path/to/existing-user-session.txt \
  --curation --send-samples --batch-size 5 --pause 2 \
  --output-dir /path/to/emoji-visual-run
```

این حالت همیشه همهٔ source IDها را resolve می‌کند. `--limit 20` فقط تعداد ردیف‌های
نمونه را محدود می‌کند؛ metadata را محدود نمی‌کند. هر candidate یک پیام با Unicode،
شمارهٔ candidate، ID و یک entity صحیح UTF-16 دارد. ترتیب اولیه ترتیب منبع است و
هیچ ادعای برتری ظاهری ندارد. برای اکانت غیرPremium نمونهٔ paid ارسال نمی‌شود؛
ردیف مربوطه با `skipped_premium_required` ثبت می‌شود و review کامل آن به مشاهدهٔ
واقعی روی اکانت مناسب نیاز دارد.

FloodWait در metadata فقط برای IDهای unresolved، یک بار پس از انتظار مجاز، retry
می‌شود. برای ارسال نمونه هیچ retry وجود ندارد؛ پس از FloodWait مکث و توقف انجام
می‌شود و resume index ثبت می‌شود. اگر انتظار بیش از `--max-flood-wait` باشد، ابزار
مدت لازم را گزارش می‌کند و توقف می‌کند. در timeout یا خطای مبهم، نتیجهٔ ارسال
نامعلوم تلقی می‌شود و هیچ resume خودکاری پیشنهاد نمی‌شود. ابتدا Saved Messages
را بررسی کنید. `--start` شمارهٔ ردیف از صفر است؛ فقط با مقایسهٔ فهرست candidateهای
دو اجرا و بررسی تحویل قبلی از آن استفاده کنید، زیرا حذف شدن ID می‌تواند ردیف‌ها را
جابه‌جا کند. ابزار session و credential را در هیچ گزارش نمی‌نویسد.

## ثبت review و ساخت mapping

در `PREMIUM_EMOJI_CURATION.json` خروجی زنده، برای هر alt بخش `groups` وجود دارد:

- `candidate_ids`: همهٔ candidateهای exact-alt، بدون اولویت ظاهری.
- `reviewed_ids`: تمام candidateهایی که واقعاً ظاهرشان مقایسه شده است.
- `ranked_ids`: یک تا پنج انتخاب به ترتیب ترجیح واقعی؛ در صورت امکان دو تا پنج.
- `evidence`: توضیح مقایسهٔ واقعی یا ارجاع به نمونه‌ها.
- `styles`: فقط styleهایی که واقعاً بررسی شده‌اند؛ هر style شامل `ranked_ids`
  و `evidence` است. IDهای style باید زیرمجموعهٔ انتخاب‌های curated همان alt باشند.

هیچ‌کدام از این فیلدها از روی animated بودن یا ID پر نمی‌شوند. خروجی نمونه‌فرستی
نیز تأیید بصری نیست. گزارش کامل‌شده را دوباره با metadata زنده تطبیق دهید:

```bash
python tools/premium_emoji_live_smoke.py \
  --session-string-file /path/to/existing-user-session.txt \
  --curation --review-file /path/to/emoji-visual-run/PREMIUM_EMOJI_CURATION.json \
  --output-dir /path/to/emoji-reviewed-run
```

خروجی شامل validation، curation تأییدشده توسط اپراتور، status، و
`CURATED_MAPS_REVIEWED.py` است. این فایل fragment شامل دو constant است؛ خودکار
import یا وارد مسیر ارسال نمی‌شود. پس از بازبینی، مقادیر آن باید در دو constant
موجود `services/premium_emoji_injector.py` قرار بگیرند و تست‌ها اجرا شوند تا بستهٔ
Final ساخته شود. warm-up همچنان صحت ID و alt را مجدداً از Telegram می‌گیرد.
فایل‌های JSON گزارش آفلاین‌اند و runtime cache دائمی نیستند.

شرط `production_ready` در خروجی review: تمام ۲۱۰ ID بررسی شده، هیچ unresolved باقی
نمانده، حداقل ۵۰ emoji curated، همهٔ ۵۰ Unicode ضروری پوشش داده شده و هر هفت style
دارای mapping بررسی‌شده باشند. IDهای rejected مجاز به ورود به mapping نیستند.
کمبود candidate در منبع یا تفاوت alt دقیق باعث حفظ Candidate می‌شود. ممکن است
۲۱۰ ID فعلی برای این پوشش کافی نباشند؛ این موضوع پیش از resolve واقعی معلوم نیست.

ابزار صحت رابطهٔ review و metadata را بررسی می‌کند؛ نمی‌تواند راست‌بودن ادعای
انسان دربارهٔ دیدن نمونه‌ها را اثبات کند. وضعیت نهایی وابسته به review واقعی است.
متن آزاد evidence در mapping نهایی کپی نمی‌شود؛ فایل review اصلی را برای بازبینی
نگه دارید. برای ادامهٔ کار، JSON validation و worksheet تکمیل‌شده و نمونه‌های
قابل مشاهده کافی‌اند؛ session یا credential را ارسال نکنید.

## حفظ رفتار و هزینه

انتخاب variant از متن و style در موتور قبلی موجود است؛ پس از قرار گرفتن mapping
واقعی، اولویت variant سپس curated سپس validated same-alt اعمال می‌شود. تست‌های
gang/love/luxury/fire و fallback این مسیر را با fixture آفلاین بررسی می‌کنند.
exact-smart معنی Unicode را تغییر نمی‌دهد و creative تنها حالت تغییر glyph است.
نمی‌توان از mock test نتیجه گرفت که هیچ ID واقعی ظاهر خاصی دارد.

چون runtime byte-identical است، هزینهٔ CPU/RAM و cache مسیر ارسال تغییری ندارد.
benchmark تاریخی Candidate برای پیام کوتاه: median حدود ۱۰٫۵۶ میکروثانیه با cache
و ۳۶٫۴۹ میکروثانیه بدون cache؛ سقف LRU برابر ۲۵۶ entry و ۲۵۶ KiB برآوردی است.
این اعداد benchmark جدید یا مصرف کل RAM نیستند. ابزار curation خارج از runtime
اجرا می‌شود و مدل NLP یا dependency جدیدی ندارد.

## نتیجهٔ تست‌ها

اجرای کامل نهایی: **۵۸۷ موفق، ۶ شکست قبلی در ۲۲٫۶۶ ثانیه**.
۱۲۹ تست موتور/production/curation در همین اجرا موفق‌اند؛ ۳۵ مورد مربوط به این
مرحله است. تست‌های قبلی تغییر نکرده‌اند. نام شش baseline failure در
`PREMIUM_EMOJI_CURATION_TEST_RESULTS.txt` آمده است. هیچ‌کدام برای سبز کردن suite
دستکاری نشده‌اند. Python 3.12.14 و Telethon 1.44.0 استفاده شد؛ تست زنده و visual
review انجام نشده است.
