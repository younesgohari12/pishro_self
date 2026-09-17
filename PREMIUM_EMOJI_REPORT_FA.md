# Premium Emoji Injection Engine — v0.09.13

## تغییرات

- `services/premium_emoji_injector.py`: تزریق متن و entity، انتخاب قطعی با کلیدواژه‌های فارسی/انگلیسی و شش گروه cool/love/funny/fire/angry/luxury؛ بدون انتخاب تصادفی.
- `services/custom_emoji_service.py`: افزودن دریافت گروهی اطلاعات رسمی سند. ساخت entity و محاسبه UTF-16 همچنان از همین سرویس مشترک استفاده می‌کنند؛ توابع قبلی بدون تغییر هستند.
- `self.py`: فقط import و اتصال injector در ابتدای `_run_connected_self`، پس از ورود و بازیابی حساب. login، session، امنیت، دیتابیس و معماری عوض نشده‌اند.
- `config.py`: دو گزینهٔ PREMIUM_EMOJI_ENABLED=True و PREMIUM_EMOJI_STYLE="smart" و ثبت آن‌ها در تنظیمات پایدار موجود؛ مقدار ذخیره‌شدهٔ کاربر اولویت دارد.
- `tests/test_premium_emoji_injector.py`: ۳۶ تست اختصاصی شامل مسیرهای واقعی Telethon با transport ساختگی و بدون شبکه.
- `tests/test_storage_updates.py`: یک تست ارتقای نسخه و حفظ تنظیمات قبلی، کیف پول/جداول، session، رسانه و تنظیمات کاربران.

## مسیرهای ارسال بررسی‌شده

در self.py تعداد ۲۹ فراخوانی send_message، هفت edit، دو send_file، یک reply و یک ارسال پنل inline وجود دارد. send_message/send_file/edit_message فقط روی نمونهٔ کلاینت سلف متصل wrap می‌شوند. بنابراین پیام AI، ترجمه، ارز، پاسخ خودکار، پیام شروع، وضعیت و پیام مدیریتی، reply/respond، ویرایش و کپشن از همان لایه عبور می‌کنند. سرویس‌های جانبی که همین کلاینت را استفاده می‌کنند نیز پوشش دارند. کلاینت ربات اصلی تغییر نمی‌کند.

ارسال پنل `.پنل` با `InlineResult.click` از نتیجه‌ای استفاده می‌کند که سرور برای ربات inline ذخیره کرده است؛ درخواست آن متن یا formatting_entities قابل تغییر ندارد. این مسیر و forward اصلی پیام دیگران بازنویسی نشده‌اند تا منو/دکمه‌ها و رفتار موجود حفظ شوند. متن‌هایی که سلف خودش می‌فرستد یا کپی می‌کند از injector عبور می‌کنند. پیام فاقد ایموجی، کد، لینک و ایموجی ناشناخته تغییر نمی‌کند.

## بانک و اعتبارسنجی

منبع شناسه‌ها: https://t.me/s/CustomEmojiPack

۲۱۰ شناسه از سه پک معرفی‌شده در کانال ثبت شده‌اند:
WhiteXkysluv_by_TgEmodziBot، royal_emoji00996_by_TgEmodziBot، Iranemoji_Mehran.

صفحهٔ عمومی کانال مقابل شناسه‌ها مربع عمومی نشان می‌دهد؛ از این صفحه نمی‌توان نگاشت قابل‌اعتماد ID به 🔥/❤️ یا «بهترین ظاهر» را استخراج کرد. هیچ نگاشت ساختگی وارد کد نشده است. نخستین پردازش مرتبط، سندها را در batchهای حداکثر ۱۰۰تایی با مهلت کلی پنج ثانیه از Telegram می‌گیرد. فقط سندهایی با alt واقعیِ شناخته‌شده وارد PREMIUM_EMOJI_POOL می‌شوند؛ لیست‌های شش دسته تا آن زمان خالی‌اند. اگر اسناد یک دسته در دسترس یا منطبق نباشند، آن دسته ممکن است خالی بماند و ایموجی عادی حفظ می‌شود. نمایش و پوشش هر شش دسته با اکانت واقعی در این محیط تأیید نشده است.

نسخهٔ متحرک بر ثابت اولویت دارد. اولویت نخست حفظ همان ایموجی است؛ در smart، اگر همان alt پیدا نشود، از ایموجی هم‌معنی همان دسته استفاده می‌شود. کلیدواژه‌ها برای موارد چندمعنایی مثل 🔥 و ✨ دسته را انتخاب می‌کنند. حالت exact فقط شکل همان Unicode را پرمیوم می‌کند؛ نام یک دسته هم به عنوان style پذیرفته می‌شود. پیام بدون ایموجی تزئین اضافه دریافت نمی‌کند.

طبق مستند رسمی https://core.telegram.org/api/custom-emoji، متن entity باید دقیقاً با alt سند تطابق داشته باشد. پرمیوم‌های غیررایگان فقط برای اکانتی انتخاب می‌شوند که هنگام اتصال premium بوده است؛ حساب عادی فقط از اسناد دارای free=True استفاده می‌کند. تغییر اشتراک حساب با اتصال مجدد شناخته می‌شود.

## حفظ رفتار و fallback

- ابتدا Markdown/HTML موجود توسط parser فعلی Telethon پردازش می‌شود، سپس entity اضافه می‌شود. formatting_entities صریح حتی اگر خالی باشد محترم است.
- offset و length بر اساس UTF-16 هستند. با تغییر طول ایموجی، entityهای بعدی و قالب‌بندیِ دربرگیرنده جابه‌جا می‌شوند؛ ورودی mutate نمی‌شود.
- Custom Emoji قبلی، code/pre، متن لینک، URL خام و کدهای fenced دست‌نخورده‌اند؛ اجزای ایموجی ZWJ/رنگ پوست جداگانه جایگزین نمی‌شوند.
- کش مشترک metadata با عمر ۲۴ ساعت، single-flight و مهلت شکست حداقل پنج دقیقه؛ کش برنامهٔ تزریق حداکثر ۲۵۶ ورودی برای هر کلاینت؛ حداکثر ۱۰۰ entity سفارشی در هر پیام.
- شکست پردازش/metadata باعث حفظ متن اصلی می‌شود. رد صریح emoji/entity توسط Telegram برای ارسال تکی فقط یک بار با متن/قالب‌بندی اصلی fallback می‌شود.
- Timeout، FloodWait و خطاهای عمومی دوباره ارسال نمی‌شوند. آلبوم چندفایلی ممکن است بخشی را قبلاً فرستاده باشد؛ برای جلوگیری از تکرار، کل آلبوم پس از خطا بازپخش نمی‌شود.
- تزریق هیچ فایل cache/session پایدار و هیچ dependency جدیدی ایجاد نمی‌کند.

## تست‌ها

محیط اجرا: Python 3.12، Telethon 1.44.0؛ نصب requirements-dev در venv جداگانه انجام شد. تست شبکهٔ واقعی نداریم.

نسخهٔ ورودی پیش از تغییر: ۴۵۶ موفق، ۶ ناموفق.

شش شکست ازقبل‌موجود:
- tests/test_admin_user_pagination.py::test_admin_users_page_two
- tests/test_hardening_v097.py::test_config_is_python_only_and_validates_without_values
- tests/test_memory_streaming_lock_pagination.py::test_iter_user_ids_does_not_hold_lock_while_consuming
- tests/test_memory_streaming_lock_pagination.py::test_large_user_pagination_is_page_bounded
- tests/test_release_regression.py::test_release_markdown_escape
- tests/test_user_streaming.py::test_broadcast_can_consume_iterator

این موارد خارج از محدودهٔ تغییر حاضرند و اصلاح نشده‌اند؛ بنابراین CI کلی هنوز سبز نیست. نتیجهٔ اجرای نسخهٔ نهایی در PREMIUM_EMOJI_TEST_RESULTS.txt آمده است. ادعای تست Python 3.11/3.13 یا ارسال زندهٔ Telegram نمی‌شود.

فایل‌های SHA256 و گزارش‌های قبلی مربوط به نسخه‌های قبلی‌اند. برای تغییرات این تحویل، PREMIUM_EMOJI_SHA256.json مرجع است.
