# گزارش ممیزی و اصلاح PishroSelf v0.09.9

تاریخ: 2026-09-12  
نسخه مبنا: `PishroSelf_v0.09.9_AI_TRANSLATE_READY`

## نتیجه کلی

پروژه به‌صورت کامل از نظر ساختار، Syntax، مسیر startup، دیتابیس، AI/ترجمه، حافظه، Web Search، state/callbackها و رگرسیون قابلیت‌های قبلی بررسی شد. ساختار اصلی پروژه حفظ شده، هیچ API Key تغییر نکرده و هیچ migration مخربی روی اطلاعات کاربران اضافه نشده است.

## باگ‌های واقعی پیدا و اصلاح‌شده

1. **تکرار فوری سؤال AI در حافظه ثبت نمی‌شد.** پاسخ از cache برمی‌گشت ولی turn جدید user/assistant ذخیره نمی‌شد. اکنون هر پاسخ موفق، حتی پاسخ cache‌شده، در تاریخچه ثبت می‌شود.
2. **ثبت تاریخچه AI اتمیک نبود.** درج پیام user، درج پیام assistant و prune در transactionهای جدا انجام می‌شد و در درخواست‌های همزمان یک کاربر امکان به‌هم‌خوردن ترتیب وجود داشت. تابع `add_ai_exchange()` اضافه شد و هر exchange در یک transaction اتمیک ذخیره و به 20 پیام آخر محدود می‌شود.
3. **دو درخواست همزمان از یک کاربر می‌توانستند context را به‌صورت مسابقه‌ای بخوانند.** برای AI قفل async جداگانه به‌ازای هر `user_id` اضافه شد؛ درخواست‌های یک کاربر serialize می‌شوند ولی کاربران مختلف همچنان موازی اجرا می‌شوند.
4. **پارسر ترجمه در دستور بدون متن رفتار اشتباه داشت.** `.ترجمه فارسی به انگلیسی` قبلاً ممکن بود خود عبارت زبان‌ها را به‌عنوان متن ترجمه در نظر بگیرد. اکنون زبان‌ها parse می‌شوند و متن خالی باعث نمایش راهنما می‌شود.
5. **فاصله‌های چندگانه در `به` / `to` پشتیبانی نمی‌شد.** parser اکنون whitespace انعطاف‌پذیر را قبول می‌کند.
6. **state ترجمه با دستور مستقیم `.ترجمه ...` گیر می‌کرد.** اگر کاربر داخل حالت پنل دستور مستقیم ترجمه می‌فرستاد، handler عمومی آن را اجرا می‌کرد اما state پنل باقی می‌ماند. اکنون همان controller دستور را مصرف کرده و state درست پاک می‌شود.
7. **Web Search قبل از استفاده از cache می‌توانست درخواست اضافی بفرستد.** برای context سرچ cache کوتاه‌مدت مستقل با TTL تنظیم‌شونده اضافه شد.
8. **خروجی خالی Search به‌عنوان سرچ موفق تلقی می‌شد.** اکنون نتیجه خالی failure محسوب می‌شود و AI صریحاً دستور می‌گیرد ادعای اطلاعات live/current نکند.
9. **Provider قدیمی Bing Search API v7 هنوز در کد فعال بود.** این API از طرف Microsoft بازنشسته شده است؛ انتخاب `bing` اکنون بدون crash خطای راهنمای واضح می‌دهد و providerهای فعال `serper` و `serpapi` هستند.
10. **مسیر SerpAPI و ساخت query به‌روز/تمیز نبود.** endpoint پیش‌فرض به `https://serpapi.com/search` تغییر کرد و query با `params` استاندارد aiohttp ارسال می‌شود.
11. **HTTP errorهای Search بعد از تلاش برای parse JSON بررسی می‌شدند.** اکنون status code قبل از JSON parse بررسی می‌شود و پاسخ غیر JSON/نامعتبر کنترل‌شده است.
12. **خطاهای AI/ترجمه Logging کافی نداشتند.** خطاهای AvalAI و خطاهای unexpected از مسیر logging مرکزی ثبت می‌شوند بدون چاپ secret.
13. **نمایش حافظه AI با Markdown روی متن آزاد کاربر می‌توانست parse را بشکند.** نمایش history به `parse_mode=None` منتقل شد.
14. **نام زبان پیش‌فرض می‌توانست Markdown پنل ترجمه را خراب کند.** مقدار نمایشی sanitize می‌شود.
15. **اجرای مستقیم `pytest` از ریشه پروژه import path را درست نمی‌ساخت.** `tests/conftest.py` ریشه پروژه را صریحاً به `sys.path` اضافه می‌کند.

## موارد بررسی‌شده و سالم

- 93 فایل Python از نظر AST/Syntax بدون خطا هستند.
- `main.py`، startup gate دیتابیس، ساخت taskها و shutdown/cancel flow بررسی شد.
- dependencyهای runtime استفاده‌شده (`telethon`, `aiohttp`, `openai`, `jdatetime`) در `requirements.txt` تعریف شده‌اند.
- فایل تکراری دقیق (exact duplicate) پیدا نشد.
- `self_panel_bot.py` صفر بایت است اما طبق گزارش ممیزی نسخه قبلی placeholder سازگاری است و عمداً حذف نشد.
- دستورات `.ai`، `.هوش` و `.هوش مصنوعی` در regex فعلی پشتیبانی می‌شوند.
- AvalAI از `AVALAI_API_KEY` / alias پروژه و `AVALAI_BASE_URL` خوانده می‌شود؛ درخواست `POST /chat/completions` با Bearer auth، model، timeout و payload سازگار با OpenAI ساخته می‌شود.
- تاریخچه بر اساس `user_id` جداست و روی SQLite ذخیره می‌شود.
- جدول‌های AI با `CREATE TABLE IF NOT EXISTS` ساخته می‌شوند و اطلاعات قبلی حذف نمی‌شوند.
- callback namespaceهای AI (`ai_*`) و ترجمه (`translate_*`) از namespaceهای قبلی جدا هستند.
- Rate Limit فعلی حفظ شده و برای مصرف AI/ترجمه اعمال می‌شود.

## تست‌های نهایی

- `python -m compileall -q .` → **PASS**
- `tests/test_ai_translate.py` → **19/19 PASS**
- regression قابل‌اجرا در sandbox (بدون dependency Telethon) → **71/71 PASS**
- تست همزمانی دو کاربر → **PASS**
- serialize شدن دو درخواست همزمان یک کاربر → **PASS**
- تست persistence در دو اجرای جداگانه Python → **PASS**
- contract درخواست AvalAI با HTTP mock → **PASS**
- مدیریت AvalAI HTTP 429 با mock → **PASS**
- cache و failure path Web Search → **PASS**

## محدودیت محیط ممیزی

Sandbox ممیزی دسترسی شبکه برای نصب dependencyها نداشت و `telethon` در محیط از قبل نصب نبود؛ بنابراین test fileهایی که برای collection خود Telethon را import می‌کنند در این محیط قابل اجرای کامل نبودند. همچنین برای جلوگیری از مصرف API Key کاربر و به‌دلیل نبود شبکه، درخواست live به Telegram/AvalAI/Search ارسال نشد. مسیر AvalAI و Search با mock و تست واحد بررسی شد. روی سرور مقصد با `pip install -r requirements.txt` می‌توان suite کامل را اجرا کرد.

## فایل‌های اصلی تغییرکرده

- `.env.example`
- `database/models.py`
- `services/memory.py`
- `services/ai_assistant.py`
- `services/translator.py`
- `services/web_search.py`
- `handlers/ai.py`
- `handlers/translate.py`
- `tests/conftest.py`
- `tests/test_ai_translate.py`
- `AI_TRANSLATION_FEATURE_v0.09.9_FA.md`
- `TEST_RESULTS_v0.09.9.json`
- `SECURITY_SHA256.json` (بازسازی manifest)

هیچ قابلیت فعلی عمداً حذف نشده و ساختار اصلی پروژه حفظ شده است.
