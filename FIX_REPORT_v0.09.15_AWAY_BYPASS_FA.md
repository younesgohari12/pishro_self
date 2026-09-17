# گزارش اصلاح نهایی — جداسازی کامل Away از Premium Emoji + تست Presence

**پروژه:** PishroSelf — **نسخه پایه:** v0.09.14 (706a7c9) — **نسخه رلیز:** v0.09.15
**تاریخ:** 2026-09-18 | **محدوده:** AWAY_BYPASS_PREMIUM + تست Presence Manager + تست واقعی Chat A/B + گزارش زمان Online Restore

---

## 1) گزینه جدید: `AWAY_BYPASS_PREMIUM = True`

پیام عدم حضور دیگر **هرگز** وارد سیستم ایموجی ویژه نمی‌شود:

| مؤلفه | قبل | بعد |
|---|---|---|
| Custom Emoji Pipeline (کانورتر قبل از ارسال) | روی پاسخ Away فعال بود | ❌ کاملاً بای‌پس |
| Resend (مدیر ارسال دوباره) | رویداد outgoing پاسخ را می‌دید | ❌ 'skipped' در اولین بررسی |
| Delete / New Send | ممکن بود اجرا شود | ❌ غیرممکن |

- کلید: `AWAY_BYPASS_PREMIUM` (پیش‌فرض `True`؛ با `getattr` از config خوانده می‌شود — بدون کلید هم امن است).
- ماژول جدید پل خنثی: `services/away_bypass.py` — **هیچ وابستگی** به away.py یا ماژول‌های پریمیوم ندارد → استقلال کامل دو سیستم.
- دو مکانیزم مکمل (چون رویداد outgoing از task دیگری از سرور می‌آید و ContextVar به آنجا نمی‌رسد):
  1. **گارد Context قبل از ارسال** — `away_send_guard()`: کانورتر (`premium_emoji_converter`) و injector (`premium_emoji_injector`) پیام را دست‌نخورده عبور می‌دهند.
  2. **Registry پیام بعد از ارسال** — `mark_away_reply(sent)`: شناسه `(chat_id, message_id)` با TTL ۲۰ ثانیه ثبت می‌شود و `handle_outgoing` مدیر ارسال دوباره قبل از هر اقدامی (حتی صف آلبوم) آن را نادیده می‌گیرد.

## 2) تست Presence Manager — ثبت وضعیت قبل/بعد

- **قبل از ارسال پاسخ عدم حضور:** تابع جدید `report_pre_away_status(client)` بلوک `[مدیریت وضعیت]` صادر می‌کند:

  ```
  [مدیریت وضعیت]
  Online Trigger: away_reply
  منبع: send_message
  اقدام: قبل ارسال پاسخ عدم حضور: Offline
  ```

- ردیابی وضعیت واقعی: درخواست‌های `UpdateStatusRequest` عبوری و بازگردانی‌ها، برچسب وضعیت را به‌روز می‌کنند (نامشخص / Offline / Online / نصب نیست — `describe_status(client)`).
- `assert_offline` (آفلاین‌سازی فوری هنگام روشن کردن Away) هم اکنون وضعیت را ثبت می‌کند.

## 3) گزارش زمان Online Restore

- بعد از هر ارسال مجاز در حالت Away، یک‌بار `UpdateStatusRequest(offline=True)` با debounce ارسال می‌شود و بلوک گزارش زمان صادر می‌شود:

  ```
  [مدیریت وضعیت]
  Online Trigger: offline_restore
  منبع: presence_manager
  اقدام: Offline Restore انجام شد (1.2 ثانیه بعد از ارسال) — ساعت 12:34:56
  ```

- **زمان‌سنجی اندازه‌گیری‌شده در تست:** بازگردانی آفلاین دقیقاً بعد از `offline_delay` (پیش‌فرض **1.2 ثانیه**؛ در تست 0.05 ثانیه) + صفر ثانیه اضافه — یعنی اکانت حداکثر ~1.2 ثانیه پس از آخرین ارسال دوباره Offline نمایش داده می‌شود. در تست خودکار: `0.04 ≤ زمان بازیابی ≤ 0.5` ✔ (تأیید شد).
- هیچ `UpdateStatusRequest(offline=False)` در کل سناریو ارسال نشد (تأیید تست).

## 4) تست واقعی (سناریوی اجباری مالک) — با پایپ‌لاین کامل

نصب کامل و واقعی: کانورتر + مدیر ارسال دوباره + هندلر outgoing + Away + Presence Manager.

| گام | نتیجه |
|---|---|
| Chat A — پیام اول «سلام» | ✅ یک پیام Away با **متن خام** (بدون Entity ویژه، بدون تبدیل) |
| رویداد outgoing همان پیام | ✅ Resend نادیده گرفت — بدون get_messages، بدون حذف، بدون ارسال جدید |
| Chat A — پیام دوم «خوبی؟» | ✅ هیچ پاسخی ارسال نشد |
| Chat B — پیام اول «سلام» | ✅ یک پیام Away مستقل |
| دیتابیس | ✅ `away_sent_chats = {A: ts, B: ts}` (ساختار درخواستی مالک) |
| کنترل: پیام عادی «موفق شد 🔥» (Away خاموش) | ✅ کانورتر Entity ساخت — یعنی پایپ‌لاین پریمیوم سالم و فعال است |

## 5) تست‌ها — ۱۲ مورد جدید، صفر رگرسیون

فایل جدید: `tests/test_away_bypass_premium.py` (۱۲ تست، همه پاس):

1. پیش‌فرض `AWAY_BYPASS_PREMIUM = True` حتی بدون کلید config
2. پاسخ Away بدون Custom Emoji Pipeline (متن خام، بدون Entity)
3. کنترل متقابل: پیام عادی تبدیل می‌شود، پاسخ Away نه
4. پاسخ Away هرگز وارد Resend نمی‌شود (بدون حذف/ارسال جدید)
5. بای‌پس مسیر آلبوم هم پوشش داده شد
6. یونیت registry + guard + TTL
7. `AWAY_BYPASS_PREMIUM = False` → رفتار عادی Resend برمی‌گردد (کلید قابل کنترل)
8. ماتریس واقعی Chat A/B با پایپ‌لاین کامل
9. Presence: وضعیت قبل ارسال ثبت می‌شود
10. Presence: Offline Restore خودکار + زمان آن + بدون آنلاین‌سازی
11. گزارش وضعیت بدون Presence نصب = امن («نصب نیست»)
12. حذف کامل سیستم پریمیوم → Away بدون خطا و آفلاین‌سازی پابرجا

**نتیجه کل مجموعه:** 859 پاس / 23 شکست — هر ۲۳ شکست دقیقاً همان شکست‌های قدیمی baseline قبل از تغییر هستند (transfer/translation/storage/… نامرتبط) → **صفر رگرسیون**. قبلی: 847 پاس؛ اکنون +۱۲ تست جدید همه پاس.

## 6) فایل‌های تغییر یافته

| فایل | تغییر |
|---|---|
| `services/away_bypass.py` | **جدید** — پل خنثی بای‌پس (ContextVar + registry) |
| `services/away.py` | ارسال پاسخ داخل `away_send_guard` + `mark_away_reply` + `report_pre_away_status` |
| `services/premium_emoji_converter.py` | بررسی `away_bypass.active()` در اولین خط wrap |
| `services/premium_emoji_injector.py` | همان بررسی در wrap |
| `services/emoji_resend_manager.py` | 'skipped' در اولین بررسی `handle_outgoing` برای پیام‌های ثبت‌شده |
| `services/presence_manager.py` | ردیابی وضعیت + `report_pre_away_status` + `describe_status` + لاگ زمان Offline Restore |
| `tests/test_away_bypass_premium.py` | **جدید** — ۱۲ تست |
| `AGENTS.md` | قرارداد v0.09.14 final |
| `config.py` | (local/gitignored) `AWAY_BYPASS_PREMIUM = True` |

## 7) نتیجه نهایی

- **Away و Premium Emoji کاملاً مستقل** شدند: پاسخ عدم حضور متن ساده و دست‌نخورده است — بدون تبدیل، بدون Resend، بدون Delete/New Send.
- منطق per-chat Away و ریست دستی/سشن دست‌نخورده ماند.
- اکانت در حالت Away آفلاین می‌ماند؛ وضعیت قبل از هر پاسخ و زمان بازگردانی آفلاین (~1.2 ثانیه بعد از آخرین ارسال) در لاگ `[مدیریت وضعیت]` ثبت می‌شود.
- بدون loop، بدون اسپم، بدون وابستگی متقابل.
