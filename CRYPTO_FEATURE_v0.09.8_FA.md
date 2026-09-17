# گزارش قابلیت ارز دیجیتال — PishroSelf v0.09.8

مبنای این نسخه: `PishroSelf_v0.09.7_AUDITED_FIXED`

## قابلیت اضافه‌شده

سیستم قیمت و تبدیل لحظه‌ای ارز دیجیتال به‌صورت ماژولار به پروژه اضافه شد.

### دستور سلف

- `.ارز بیت کوین`
- `.ارز bitcoin`
- `.ارز btc`
- `.ارز 10 تتر`
- `.ارز 100 داگز بیت کوین`
- `.ارز 100 تون به تتر`

Parser اعداد فارسی/عربی/انگلیسی و جداکننده‌های عددی را نرمال می‌کند و نام‌های چندکلمه‌ای را با لیست CoinGecko resolve می‌کند.

## منبع قیمت

- CoinGecko: لیست کامل ارزها، قیمت USD، تغییر 24 ساعته و زمان آخرین بروزرسانی.
- Nobitex: نرخ عمومی `USDT-RLS` برای محاسبه قیمت ایران. چون این نرخ ریالی است، قبل از محاسبه تومان بر 10 تقسیم می‌شود.
- کلید CoinGecko اختیاری است و در صورت استفاده فقط از `.env`/environment خوانده می‌شود.

## Cache

- TTL قیمت: پیش‌فرض 20 ثانیه و قابل تنظیم بین 10 تا 30 ثانیه.
- لیست ارزها: پیش‌فرض 1 ساعت cache می‌شود تا درخواست‌های اضافی کم شود.
- قیمت provider اگر بیش از 5 دقیقه قدیمی باشد نمایش داده نمی‌شود.
- جدول `crypto_cache` آخرین قیمت‌های دریافت‌شده را ثبت می‌کند.

## دیتابیس

دو جدول بدون حذف/بازنویسی جداول قبلی اضافه می‌شوند:

- `crypto_cache(symbol, price_usd, price_usdt, price_toman, updated_at)`
- `crypto_settings(user_id, favorite_coins, created_at)`

ارزهای محبوب پیش‌فرض:

`BTC, ETH, TON, USDT, DOGS`

تنظیمات هر کاربر در SQLite باقی می‌ماند و بعد از Restart حفظ می‌شود.

## پنل‌ها

### ربات اصلی

منوی `💰 ارز دیجیتال` اضافه شد و شامل این بخش‌هاست:

- `📊 قیمت ارز`
- `🔄 تبدیل ارز`
- `🔥 ارزهای محبوب`
- `⚙️ تنظیمات`

در تنظیمات ربات اصلی، کاربر می‌تواند 1 تا 10 ارز محبوب را تغییر دهد یا به پیش‌فرض برگرداند.

### پنل سلف `.پنل`

دکمه `💰 ارز دیجیتال` اضافه شد و قیمت/تبدیل/محبوب‌ها/تنظیمات را نمایش می‌دهد. اجرای مستقیم قیمت و تبدیل با دستور `.ارز ...` در سلف انجام می‌شود.

## فایل‌های جدید

- `handlers/crypto.py`
- `services/crypto_api.py`
- `services/crypto_parser.py`
- `services/price_converter.py`
- `tests/test_crypto.py`

## فایل‌های متصل/اصلاح‌شده

- `.env.example`
- `config.py`
- `database/models.py`
- `main.py`
- `self.py`
- `inline.py`
- `bot/core.py`

## تست نسخه جدید

- `python -m compileall -q .` → PASS
- تست‌های crypto + رگرسیون‌های قابل اجرای محیط فعلی → `49/49 PASS`
- تست محاسبه `USDT-RLS → تومان` → PASS
- تست cache بدون درخواست دوم در TTL → PASS
- Secret scan روی سورس اجرایی → PASS
- dangerous primitive scan (`eval/exec/os.system/shell=True/unsafe pickle`) → PASS

محدودیت محیط ساخت: پکیج Telethon در محیط sandbox نصب نبود و دسترسی pip به اینترنت نیز در دسترس نبود، بنابراین suite کامل تاریخی 288 تست روی همین sandbox دوباره اجرا نشد. نسخه ورودی v0.09.7 طبق گزارش ممیزی خودش قبل از این تغییرات `288/288 PASS` بوده است. هیچ ادعایی مبنی بر اجرای مجدد آن 288 تست در این محیط وجود ندارد.

## متغیرهای جدید `.env`

```env
PISHRO_COINGECKO_API_KEY=
PISHRO_COINGECKO_API_KEY_TYPE=demo
PISHRO_COINGECKO_API_BASE=https://api.coingecko.com/api/v3
PISHRO_NOBITEX_API_BASE=https://api.nobitex.ir
PISHRO_CRYPTO_CACHE_TTL=20
PISHRO_CRYPTO_COIN_LIST_TTL=3600
PISHRO_CRYPTO_HTTP_TIMEOUT=10
```

برای CoinGecko Pro مقدار `PISHRO_COINGECKO_API_KEY_TYPE=pro` قرار داده شود؛ base URL پیش‌فرض متناسب با نوع کلید انتخاب می‌شود مگر اینکه صراحتاً override شده باشد.
