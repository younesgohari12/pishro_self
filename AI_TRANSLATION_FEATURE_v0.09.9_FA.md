# PishroSelf v0.09.9 — AI + Translation

مبنای این نسخه: `PishroSelf_v0.09.8_CRYPTO_READY`

## قابلیت‌های اضافه‌شده

- 🌐 ترجمه چندزبانه با AvalAI
  - دستور `.ترجمه`
  - تعیین صریح زبان مبدا/مقصد مثل `.ترجمه فارسی به انگلیسی سلام`
  - تشخیص خودکار زبان مبدا در حالت `.ترجمه hello`
  - زبان مقصد پیش‌فرض جدا برای هر کاربر
  - کش ترجمه برای کنترل مصرف API
- 🤖 دستیار هوش مصنوعی با AvalAI
  - دستورات `.ai`، `.هوش` و `.هوش مصنوعی`
  - `AI_MODEL` و `TRANSLATE_MODEL` قابل تغییر از `.env`
  - System Prompt قابل تغییر توسط ادمین از پنل
  - حافظه SQLite مستقل برای هر کاربر؛ حداکثر 20 پیام آخر
  - مشاهده، شمارش و پاک‌کردن حافظه از پنل
  - Rate Limit per-user و کش پاسخ
- 🌍 Web Search شرطی
  - فقط برای پرسش‌های زمان‌حساس/نیازمند اطلاعات جدید
  - Providerهای فعال: `serper` و `serpapi`
  - `bing` قدیمی عمداً غیرفعال شده چون Bing Search API v7 بازنشسته شده است
  - کلید سرچ فقط از `.env`
  - نتایج وب به‌عنوان داده غیرقابل‌اعتماد به مدل داده می‌شوند تا دستورهای داخل صفحات اجرا نشوند.

## فایل‌های اصلی جدید

- `handlers/ai.py`
- `handlers/translate.py`
- `services/avalai_ai.py`
- `services/ai_assistant.py`
- `services/translator.py`
- `services/web_search.py`
- `services/memory.py`
- `tests/test_ai_translate.py`

## تنظیمات `.env`

حداقل برای AI/ترجمه:

```env
AVALAI_API_KEY=YOUR_KEY
AI_MODEL=gpt-4o-mini
TRANSLATE_MODEL=gpt-4o-mini
```

برای Web Search نیز یکی از providerها را انتخاب کن. پیش‌فرض `serper` است:

```env
SEARCH_API_KEY=YOUR_SEARCH_KEY
SEARCH_API_PROVIDER=serper
```

providerهای دیگر:

```env
# SEARCH_API_PROVIDER=serpapi
# Bing v7 retired; use serper or serpapi
```

تنظیمات اختیاری:

```env
AI_MAX_TOKENS=1200
AI_TEMPERATURE=0.55
AI_RATE_LIMIT_PER_MINUTE=8
AI_CACHE_TTL=600
AI_WEB_CACHE_TTL=90
SEARCH_MAX_RESULTS=5
SEARCH_HTTP_TIMEOUT=10
```

## دیتابیس

جدول اصلی حافظه مطابق نیاز:

`ai_history(id, user_id, role, message, created_at)`

جدول‌های کمکی `ai_user_settings`، `ai_config` و `ai_cache` نیز اضافه شده‌اند. همه روی SQLite فعلی پروژه هستند و بعد از Restart باقی می‌مانند.

## تست

- AST/Syntax کل Python: PASS
- Compileall کل پروژه: PASS
- تست‌های AI/Translation + چند Regression موجود: 35/35 PASS
- Parser smoke برای `.ai` / `.هوش` / `.هوش مصنوعی` / `.ترجمه`: PASS
- برخورد callback ترجمه با `tr_*` قدیمی انتقال الماس شناسایی و رفع شد؛ namespace جدید ترجمه `translate_*` است.

تست زنده با API Key واقعی انجام نشده تا هیچ کلید یا اعتبار کاربر مصرف نشود. اجرای کامل suite وابسته به Telethon در محیط ساخت ممکن نبود چون Telethon در این sandbox نصب نبود؛ این محدودیت در گزارش تست ثبت شده است.
