# گزارش اصلاح v0.09.17 — API پایدار زمان تهران + حسابرسی سرویس ارسال دوباره

تاریخ: 2026-09-18 | نسخه: 0.09.17 | پایه: 878d3fd (v0.09.16-data-loader-fa)

---

## ۱) باگ گزارش‌شده و ریشه اصلی

**خطا:** `TypeError: get_tehran_time() takes 0 positional arguments but 1 was given`

**ریشه اصلی (قطعی):** در بازنویسی Loader نسخه v0.09.16، تابع `get_tehran_time` در
`config.py` بدون پارامتر تعریف شد:

```python
def get_tehran_time():            # ← ۰ پارامتر
    return datetime.now(TEHRAN_TZ).time()
```

در حالی که ۶ فراخوانی از کد اصلی پروژه، یک «رشته فرمت strftime» می‌فرستند.
این رگرسیون از تست‌سوت عبور کرد چون هیچ تستی مسیر «آرگومان فرمت» را پوشش
نمی‌داد (۸ فراخوانی موجود، فقط ۲ مسیر بدون آرگومان را تست‌شده می‌گذراند).

### نقشه کامل همه فراخوانی‌های `get_tehran_time` (بررسی مالک: بند ۲)

| # | فایل:خط | فراخوانی | وضعیت قبل | وضعیت بعد |
|---|---------|-----------|------------|------------|
| 1 | inline.py:427 | `get_tehran_time("%H:%M:%S")` | ❌ TypeError | ✅ |
| 2 | inline.py:490 | `get_tehran_time("%H:%M:%S")` | ❌ TypeError | ✅ |
| 3 | inline.py:529 | `get_tehran_time("%H:%M")` | ❌ TypeError | ✅ |
| 4 | inline.py:560 | `get_tehran_time("%H:%M")` | ❌ TypeError | ✅ |
| 5 | inline.py:590 | `get_tehran_time("%H:%M:%S")` | ❌ TypeError | ✅ |
| 6 | self.py:1080 | `get_tehran_time('%H:%M:%S')` | ❌ TypeError | ✅ |
| 7 | db.py:637 | `get_tehran_time()` | ✅ | ✅ بدون تغییر |
| 8 | bot/core.py:1167 | `get_tehran_time()` | ✅ | ✅ بدون تغییر |

## ۲) راه‌حل منتخب: گزینه «آرگومان اختیاری + backward compatible»

هیچ فراخوانی‌ای تغییر نکرد (صفر ریسک برای db و bot-core):

```python
def get_tehran_time(fmt=None):
    now = datetime.now(TEHRAN_TZ)
    if fmt is None:
        return now.time()          # رفتار قدیمی — دست‌نخورده
    if isinstance(fmt, str):
        return now.strftime(fmt)   # رشته فرمت — رفع TypeError
    raise TypeError('... use get_tehran_datetime()')  # پیام واضح
```

- `get_tehran_time()` → همان `datetime.time` قدیمی (db.py / bot/core.py بدون تغییر)
- `get_tehran_time('%H:%M:%S')` → رشته فرمت‌شده (پنل/اینلاین/سلف)
- آرگومان نامعتبر → پیام TypeError روشن به‌جای خطای مبهم

## ۳) یافته اضافی در بررسی (همان خانواده ریشه): CLOCK_FONTS خالی

`CLOCK_FONTS = {}` باعث می‌شد:
- `self.py:1722` → `KeyError: 1` در `clock_updater` (ساعت پروفایل بی‌صدا می‌مرد)
- `inline.py:511/533/591` → `KeyError` در منوهای فونت پنل اینلاین
- `db.py:273` → اعتبارسنجی همیشه به کلید ۱ برمی‌گشت

**اصلاح:** بازسازی ۶ فونت (استاندارد، فارسی، بولد، مونواسپیس، دولته، فول‌ویدث)
+ افزودن `CLOCK_FONTS` به `_NON_PERSISTENT_KEYS` تا فایل پایدار هرگز آن را
(به‌عنوان دیکشنری لیترالی بدون تابع) بازنویسی نکند.

## ۴) سرویس Premium Emoji / Resend Message — نتیجه حسابرسی

این سیستم **از قبل کامل پیاده‌سازی و تست‌شده** است (v0.09.13→15):
موتور اصلی `services/emoji_resend_manager.py` + شیم سازگاری
`services/premium_resend.py` + نصب در `self.py:421`.

طبق spec مالک، **نقطه ورود رسمی** `services/premium_resend_service.py` در
این نسخه اضافه شد (فقط نما/Re-export؛ بدون تکرار منطق تا منطق موجود خراب نشود).

### چک‌لیست spec مالک ↔ پیاده‌سازی موجود

| الزام spec | پیاده‌سازی | شاهد تست |
|---|---|---|
| فعال با PREMIUM_EMOJI_ENABLED + RESEND_MODE | `resend_enabled()`: زنجیره config + کانورتر + انتخاب پنل | `test_resend_disabled_when_config_hard_off`، `test_resend_disabled_when_converter_off`، `test_skip_when_panel_off_or_hard_off` |
| همه مقصدها (خصوصی/گروه/سوپرگروه/کانال/Saved) | هندلر `events.NewMessage(outgoing=True)` کانورتر → `handle_outgoing` | `test_6_group_delete_then_new_send`، `test_7_private_and_saved_delete_then_new_send` |
| پیام بدون ایموجی → هیچ کاری نکن | گارد `EMOJI_PATTERN` | `test_skip_when_no_mappable_emoji` |
| کپی کامل محتوا (متن/مدیا/کپشن/entity/reply) | `_copy_delete_resend` مرحله ۱ | `test_1_text...` تا `test_5_album...` |
| حذف فقط بعد از کپی موفق | کپی در حافظه → حذف → ارسال | `test_no_new_send_when_delete_fails` |
| حذف ناموفق → نسخه جدید ممنوع | `_delete_original` → False → return | `test_no_new_send_when_delete_fails` |
| ارسال ناموفق → ۳ تلاش + نجات بدون entity | `_send_copy` + rescue | `test_send_failure_retries_then_rescue_without_entity` |
| جلوگیری از loop | حافظه (chat_id,msg_id) + ignore + cooldown | `test_no_loop_for_resent_message` |
| شناسه پردازش هر پیام | بلوک [بررسی ایموجی ویژه] + `_mark_recent` | `test_premium_check_debug.py` |
| rate limit / flood | SEND_ATTEMPTS=3، RETRY=0.4s، STRIP_COOLDOWN=300s، قفل per-chat | `test_cooldown_blocks_resend`، `test_strip_condition_sets_cooldown` |
| آلبوم/media group یک‌جا | صف آلبوم + حذف/ارسال یک‌باره | `test_5_album_delete_then_new_send_as_one_album` |
| استقلال از Away | `AWAY_BYPASS_PREMIUM` | `test_away_bypass_premium.py` |
| بدون هیچ Edit | هیچ `EditMessageRequest` در جریان Premium | `test_final_audit_traces.py` |

پوشش مدیا: متن، عکس، ویدیو، گیف/انیمیشن، فایل، صدا، ویس، استیکر، video_note
(از طریق `send_file` با هندل مدیا) + آلبوم — استیکر/video_note بدون متن کپشن،
چیزی برای نگاشت ندارند و طبق قانون «بدون ایموجی → هیچ کاری نکن» دست‌نخورده می‌مانند.

## ۵) فایل‌های تغییر یافته

| فایل | تغییر |
|---|---|
| `config.py` | `get_tehran_time(fmt=None)` سازگار با عقب + بازسازی `CLOCK_FONTS` (۶ فونت) + محافظت `_NON_PERSISTENT_KEYS` |
| `services/premium_resend_service.py` | **جدید** — نقطه ورود رسمی سرویس (نما، بدون منطق تکراری) |
| `tests/test_tehran_time_api.py` | **جدید** — ۱۵ تست (زمان/فونت/نما) |
| `storage.py` | `APP_VERSION = '0.09.17'` |
| `migrate.sh` | پذیرش مسیر نسخه جدید به‌عنوان آرگومان اول: `bash migrate.sh /root/self/<نسخه>/PishroSelf` |

## ۶) Migration لازم

**هیچ مهاجرت دیتایی لازم نیست.**
- معماری Loader v0.09.16 پابرجاست: دیتا/اعتبارنامه همیشه از
  `/root/PishroSelfData` (DATA_DIR و DB_DIR پین) — هیچ فایل دیتایی کپی/حذف
  نمی‌شود و سشن‌ها دست‌نخورده می‌مانند.
- فایل پایدار `/root/PishroSelfData/config.py` بازنویسی نمی‌شود؛
  `CLOCK_FONTS` هم دیگر قابل بازنویسی از فایل پایدار نیست.
- فقط کافی است نسخه جدید جایگذاری و سرویس به آن نشانده شود (بند ۸).

## ۷) اجرای تست‌ها

```bash
cd /root/self/<نسخه>/PishroSelf && python3 -m pytest tests/ -q
# یا با venv پروژه:
.venv/bin/python -m pytest tests/ -q
```

نتیجه محلی این نسخه: **۸۸۶ پاس / ۲۱ شکست قدیمی‌ثابت (صفر رگرسیون)**
مقایسه دقیق لیست FAILED با اجرای baseline commit پایه: یکسان (comm خالی).
۱۵ تست جدید: `tests/test_tehran_time_api.py` — همگی پاس.

## ۸) Deploy بدون حذف دیتا و سشن‌ها

بسته کامل (ZIP) شامل `config.py` Loader و `migrate.sh` است؛ استخراج در
`/root/self` و سپس:

```bash
sudo bash /root/self/<پوشه-نسخه-جدید>/PishroSelf/migrate.sh \
  /root/self/<پوشه-نسخه-جدید>/PishroSelf
```

تضمین‌های migrate.sh: توقف سرویس → بکاپ timestamp دار (unit + tar دیتا) →
نشان دادن سرویس به نسخه جدید → venv + requirements → تست import با
assert مسیر دیتا/سشن → اجرا → لاگ ۱۰۰ خط → rollback خودکار در خطا.
دیتای `/root/PishroSelfData` فقط خواندنی است و هرگز حذف/overwrite نمی‌شود.
