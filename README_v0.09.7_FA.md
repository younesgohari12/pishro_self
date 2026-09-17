# PishroSelf v0.09.7 — Audited / Reliability & Security Fix

این نسخه بر مبنای `v0.09.6_CUSTOM_EMOJI_MANAGER` ساخته شده و قابلیت‌های فعلی حذف نشده‌اند. تمرکز این به‌روزرسانی روی پایداری دیتابیس، مدیریت امن تنظیمات، logging، cleanup تسک‌ها و حذف credentialهای hard-code شده است.

## راه‌اندازی امن

1. Python و وابستگی‌ها را نصب کنید:

```bash
python -m pip install -r requirements.txt
```

2. فایل `.env.example` را به `.env` کپی کنید.

Windows:

```bat
copy .env.example .env
```

Linux/macOS:

```bash
cp .env.example .env
```

3. مقادیر واقعی `PISHRO_API_ID`, `PISHRO_API_HASH`, `PISHRO_BOT_TOKEN`, `PISHRO_INLINE_BOT_TOKEN` و در صورت استفاده از TTS مقدار `PISHRO_AVALAI_API_KEY` را فقط در `.env` وارد کنید.

4. اجرا:

```bash
python main.py
```

> مهم: چون credentialهای نسخه قبلی داخل `config.py` قرار گرفته بودند، آن token/keyها باید از پنل سرویس مربوطه rotate/revoke و با credential جدید جایگزین شوند. این نسخه هیچ secret واقعی را داخل ZIP قرار نمی‌دهد.

## تغییرات مهم

- نوشتن JSON اتمیک با فایل موقت + `fsync` + replace اتمیک.
- نگهداری backup آخرین JSON معتبر و recovery هنگام خراب‌شدن فایل اصلی.
- shard rollover امن: نسخه قبلی کاربر تا قبل از قطعی‌شدن مقصد جدید حذف نمی‌شود.
- deep-copy برای stateهای nested تا لیست‌های تنظیمات بین cache/callerها مشترک نشوند.
- logging چرخشی در `logs/pishro.log` با redaction توکن، API key و شماره تلفن.
- startup gate برای تنظیمات ضروری و دیتابیس؛ runtime نیمه‌آماده بالا نمی‌آید.
- cleanup کامل taskهای سرویس‌های اصلی و TTL media registry.
- ثبت User Action برای callbackهای ربات اصلی/اینلاین و ثبت خطاهای DB/API.
- secretها از `config.py` حذف و به `.env`/environment منتقل شدند.

## وضعیت تست آفلاین

- Python compileall: PASS
- Import همه ماژول‌های اجرایی با Telegram/OpenAI offline stubs: PASS (62/62)
- Pytest: 288/288 PASS
- تست زنده Telegram/AvalAI: انجام نشده؛ محیط ممیزی دسترسی شبکه و credential اجرایی ندارد.

جزئیات کامل در `AUDIT_FIX_REPORT_v0.09.7_FA.md` قرار دارد.
