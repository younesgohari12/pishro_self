# گزارش اصلاح Premium Prefix — PishroSelf v0.09.13

## نتیجه

خروجی متنی Self Account به شکل `✨ + فاصله + متن اصلی` ساخته می‌شود. روی ✨ یک `MessageEntityCustomEmoji` واقعی با offset صفر و طول UTF-16 صحیح قرار دارد. متن، معنی و ایموجی‌های موجود جایگزین نمی‌شوند. خروجی Bot Account از این سرویس عبور نمی‌کند.

## پیاده‌سازی و محدوده

- سرویس جدید: `services/premium_emoji_prefix.py`، متد خالص `PremiumEmojiPrefix.inject(text, entities)`.
- ساخت entity دقیقاً با `services.custom_emoji_service.create_custom_emoji`، همان سازندهٔ بخش تست موجود، انجام می‌شود. خود سرویس مشترک و پنل تست تغییر نکرده‌اند.
- نصب فقط در `self.py` بعد از احراز هویت و دریافت `get_me()` انجام می‌شود: `install_premium_prefix(client, account=me)`.
- نصب برای حساب bot یا هویت نامشخص رد می‌شود. وضعیت Premium حساب نگه داشته می‌شود، اما شرط فعال‌شدن معماری نیست.
- متدهای هر نمونهٔ Self Client: `send_message`، `send_file`، `edit_message` و `_send_album` در Telethon 1.44.0 پوشش داده شده‌اند. کلاس سراسری TelegramClient تغییر نمی‌کند.
- `event.reply/respond`، `Message.reply/respond/edit` به همین متدها می‌رسند. خروجی AI، ترجمه، ارز، پاسخ خودکار/دشمن، تبچی در حالت copy و سرویس‌های کپی/ذخیرهٔ مدیا، وقتی از کلاینت سلف ارسال شوند، همین prefix را دریافت می‌کنند.
- ارسال Message object به صورت copy، کپشن، Markdown/HTML، entityهای صریح و امضای کوتاه `edit_message(message_object, new_text)` پشتیبانی می‌شوند.
- هر آلبوم حداکثر ده‌تایی مستقل پردازش می‌شود؛ fallback بستهٔ دوم باعث تکرار بستهٔ اول نمی‌شود.
- کپشن خالی تغییری نمی‌کند و پیام جداگانه‌ای ساخته نمی‌شود.
- `forward_messages`، ارسال‌های واقعی forward و ویرایش InlineBotMessage دست‌نخورده‌اند.
- پیام‌های دستی خارج از متدهای این کلاینت پایش نمی‌شوند؛ outgoing event handler جدیدی وجود ندارد.

## مسیرهای ربات اصلی

فایل‌های `bot/core.py`، `inline.py` و `handlers/premium_emoji.py` بایت‌به‌بایت بدون تغییر هستند. هیچ wrapper روی کلاینت Bot Token یا helperproselfbot نصب نشده است؛ متن پنل مدیر، دکمه‌ها، تنظیمات و خطاهای ورودی ربات اصلی prefix نمی‌گیرند. ملاک هویت کلاینت ارسال‌کننده است، نه محتوای پیام یا مقصد چت.

## شناسه‌ها و انتخاب

```python
PREMIUM_EMOJI_ENABLED = True
PREMIUM_EMOJI_PREFIX_ENABLED = True
PREMIUM_EMOJI_PREFIX_MODE = "round_robin"
PREMIUM_EMOJI_PREFIX_IDS = [5938388342281343001]
```

شناسهٔ فعال و fallback: **5938388342281343001**، بر مبنای تست موفق اعلام‌شده توسط مالک پروژه. منبع معرفی‌شده: https://t.me/CustomEmojiPack

**محدودیت روشن:** در این محیط اعتبارسنجی زنده یا انتخاب بصری ۵ تا ۱۵ ایموجی انجام نشده است. گزارش موجود در ZIP ورودی `live_metadata_attempted=false` داشت. بنابراین pool پیش‌فرض عمداً فقط همان یک شناسهٔ تأییدشده را دارد؛ شناسهٔ تأییدنشده اضافه نشده است. با یک شناسه، تنوع وجود ندارد. pool تا ۱۵ شناسهٔ یکتا را پشتیبانی می‌کند و پس از افزودن شناسه‌های واقعاً تأییدشده به config، چرخش بدون تکرار متوالی انجام می‌شود. اعتبارسنجی عددی config به معنی اعتبارسنجی زندهٔ Telegram نیست.

تنها حالت پیاده‌شده round-robin است. هزینهٔ انتخاب ثابت و بدون random یا کار شبکه‌ای است. تنظیمات Python ذخیره‌شده اولویت دارند؛ خاموش‌بودن قبلی PREMIUM_EMOJI_ENABLED عمداً بازنویسی نمی‌شود.

## حفظ formatting و idempotency

طول prefix با UTF-16 محاسبه می‌شود. entityهای قبلی کپی و offset آن‌ها جابه‌جا می‌شود؛ نوع، طول و اطلاعات اضافی مانند URL، زبان Pre و document_id حفظ می‌شوند. متن Unicode و entityهای ورودی mutate نمی‌شوند. Bold، Italic، Underline، Strike، Spoiler، TextUrl، URL، Code، Pre و CustomEmoji در تست‌ها پوشش داده شده‌اند.

امضای prefix این سیستم: placeholder ابتدای متن، فاصلهٔ بعد از آن، و CustomEmoji با offset صفر و شناسهٔ pool/fallback. همان امضا دوباره تزریق نمی‌شود. متن Unicode شبیه prefix بدون entity، prefix معتبر فرض نمی‌شود.

## fallback و جلوگیری از duplicate

ابتدا ارسال/ویرایش با entity واقعی امتحان می‌شود. فقط رد صریح PremiumAccountRequired، EmoticonInvalid یا خطاهای RPC اختصاصی CustomEmoji/Emoticon document باعث یک تلاش مجدد با متن اصلی و entityهای اصلی می‌شود. اگر ویرایش از قبل prefix همین سیستم داشته باشد، در fallback فقط همان prefix برداشته می‌شود؛ CustomEmojiهای اصلی متن حفظ می‌شوند.

خطای شبکه، timeout، FloodWait، خطای عمومی entity یا DocumentInvalid مدیا موجب replay نمی‌شود. اگر تلاش fallback نیز خطا بدهد، خطای اصلی آن ارسال مانند قبل به فراخواننده می‌رسد؛ retry نامحدود وجود ندارد. این patch دسترسی شبکه، محدودیت‌های چت یا خرابی مدیا را پنهان نمی‌کند. nested send و هر آلبوم نیز در تست‌ها کنترل شده‌اند.

## اعتبارسنجی واقعی این خروجی

- تست‌های اختصاصی prefix: **59 PASS / 0 FAIL**.
- تست‌های prefix همراه با storage: **91 PASS / 0 FAIL**.
- کل تست‌های نسخهٔ ورودی بدون تغییر: **632 PASS / 6 FAIL**.
- کل تست‌های نسخهٔ اصلاح‌شده: **692 PASS / 6 FAIL**.
- تست جدید ناموفق یا regression جدید نسبت به baseline: **0**.
- Syntax تمام فایل‌های Python: PASS.
- داده‌ها، wallet، history، session، media و تنظیمات قبلی در تست ارتقا و restart حفظ شدند.
- تست‌ها آفلاین با کلاس‌ها، parser، serializer و متدهای واقعی Telethon انجام شده‌اند؛ فقط مرز شبکه شبیه‌سازی شده است. ارسال/نمایش زندهٔ Telegram در این محیط انجام نشده و تأیید نشده است.

شش شکست موجود در baseline و خروجی نهایی یکسان‌اند:

1. `test_admin_user_pagination.py::test_admin_users_page_two`
2. `test_hardening_v097.py::test_config_is_python_only_and_validates_without_values`
3. `test_memory_streaming_lock_pagination.py::test_iter_user_ids_does_not_hold_lock_while_consuming`
4. `test_memory_streaming_lock_pagination.py::test_large_user_pagination_is_page_bounded`
5. `test_release_regression.py::test_release_markdown_escape`
6. `test_user_streaming.py::test_broadcast_can_consume_iterator`

این موارد خارج از patch ایموجی باقی مانده‌اند. خروجی کامل تست‌ها در `PREMIUM_PREFIX_TEST_RESULTS.txt` و baseline در `PREMIUM_PREFIX_BASELINE_TEST_RESULTS.txt` قرار دارد.

## فایل‌های تغییرکرده

فقط سه فایل اصلی تغییر کرده‌اند: `self.py`، `config.py` و `tests/test_storage_updates.py`. سرویس prefix، تست‌های اختصاصی و گزارش‌های این patch اضافه شده‌اند. مسیر session، داده‌ها، schema دیتابیس و منطق billing تغییری نکرده‌اند.

موتور و ابزارهای curation قبلی به عنوان ابزار قدیمی باقی مانده‌اند، اما runtime سلف آن‌ها را نصب یا import نمی‌کند و به visual review، alt matching، semantic replacement یا ۲۱۰ شناسه وابسته نیست. گزارش‌های قبلی PREMIUM_EMOJI_* تاریخی‌اند؛ این گزارش و PREMIUM_PREFIX_* مرجع همین patch هستند.

## استفاده

این ZIP را به عنوان نسخهٔ جدید پروژه اجرا کنید و سلف را restart کنید. داده‌های پایدار و config فعلی را نگه دارید. تنظیمات جدید از defaults به config اضافه می‌شوند؛ اگر قبلاً ایموجی را خاموش کرده‌اید، برای فعال‌شدن باید هر دو پرچم فعال باشند. هیچ session export یا ابزار ورود جدیدی لازم نیست.
