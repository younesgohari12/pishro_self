# گزارش دور Debug — چرا Premium Resend در تلگرام واقعی کار نمی‌کرد؟

**نسخه:** PishroSelf v0.09.13 — PREMIUM_RESEND_DEBUG
**پایه:** PREMIUM_RESEND_CLOSE_AWAY
**قانون این دور:** هیچ قابلیت جدیدی اضافه نشد؛ فقط Debug و Fix.

---

## ۱) ریشه مشکل (تشخیص)

نسخه قبلی تصمیم Resend را با «اشیاء محلی Telethon» می‌گرفت:

- بعد از ارسال، `sent.entities` (همان چیزی که خودمان فرستادیم) بررسی می‌شد؛
- هیچ‌وقت از خود تلگرام پرسیده نمی‌شد که پیام «ذخیره‌شده روی سرور» واقعاً
  چه Entity ای دارد.

اگر سرور Entity را بی‌صدا حذف کند (یا نمای رویداد قدیمی باشد)، نسخه
ارسال‌مجدد هم بدون Entity ثبت می‌شد و پیام اصلی هم حذف می‌شد → نتیجه ظاهری:
«Resend کار نمی‌کند». بدون واکشی مجدد از سرور، هیچ‌راهی برای تشخیص این
وضعیت وجود نداشت — دقیقاً همان چیزی که این دور Debug اندازه‌گیری می‌کند.

## ۲) جریان جدید (Fix معماری تصمیم)

```
Send (Unified Pipeline)
  → [PREMIUM_CHECK]            ← قبل از هر تصمیم Resend
  → sleep 0.5s                 ← طبق spec
  → client.get_messages()      ← نمای واقعی سرور
  → Entity هست؟  ── بله → پیام سالم است؛ کاری نمی‌کنیم
       │
       └── خیر → کپی دقیق پیام (متن/Entities/Reply/مدیا/کپشن)
                → ارسال با Custom Emoji (از Pipeline)
                → حذف پیام اصلی (فقط بعد از ارسال موفق)
```

- اگر واکشی از سرور خطا بدهد، تصمیم با نمای event ادامه می‌یابد (رفتار
  قبلی حفظ می‌شود؛ پیام اصلی همیشه سالم می‌ماند).
- نسخه Resend شده هم تحت نظر است: رویداد خودش بررسی و در حالت Debug از
  سرور هم واکشی می‌شود تا معلوم شود تلگرام Entity آن را نگه داشته یا
  حذف کرده. بعد از ۲ حذف پیاپی، cooldown ۳۰۰ ثانیه جلوی دوبل‌شدن را
  می‌گیرد (قانون قبلی، بدون تغییر).

## ۳) بلوک [PREMIUM_CHECK] — دقیقاً مطابق spec

```
[PREMIUM_CHECK]
chat_id: -1001234567890
message_id: 123
has_entity: False
entities: none
media_type: photo
reply_to: 7
server_check: fetched after 0.5s → has_entity=False | mapping: 1 custom entity(ies)
```

- شش خط اول: همان قالب درخواستی مالک.
- خط `server_check`: نتیجه واکشی مجدد + نتیجه نگاشت (فقط در مسیر تصمیم).
- `entities` فشرده و دقیق است: `custom(doc=<document_id>@offset+length)` —
  اگر `has_entity: True` بود ولی کاربر ایموجی معمولی می‌بیند، مشکل از
  document_id است نه Entity؛ همین یک خط علت را مشخص می‌کند.
- ثبت: همیشه در `logs/premium_emoji.log`؛ ارسال تلگرامی با پرچم
  `PREMIUM_EMOJI_RESEND_DEBUG` (سپرده ضداسپم مستقل `premium_check`).

## ۴) صف آلبوم (یک ارسال مجدد برای کل آلبوم)

- قطعات با `grouped_id` بافر می‌شوند (۱ ثانیه سکوت = تکمیل آلبوم).
- بعد از تکمیل: `sleep 0.5s` → همه قطعات یک‌بار از تلگرام واکشی.
- اگر «هر» قطعه Entity نداشت و حداقل یک نگاشت موجود بود → «کل آلبوم یک
  بار» کپی و ارسال مجدد می‌شود (همه فایل‌ها در یک `send_file`) و بعد
  اصل‌ها حذف می‌شوند. هرگز هر پیام جداگانه Resend نمی‌شود.
- اگر سرور همه Entity ها را نگه داشته باشد یا هیچ نگاشتی نباشد → آلبوم
  دست‌نخورده می‌ماند.

## ۵) `.premium debug on` — گزارش زنده در Saved Messages

```
.premium debug on     → روشن
.premium debug off    → خاموش
.premium / .premium status → وضعیت + آمار
```

گزارش هر پیام (یک پیام جامع در Saved Messages):

```
🔧 Premium Debug — Resend کامل شد
chat: 555 | msg: 10 | media: none | reply: none
1) event: has_entity=False
2) server (0.5s): has_entity=False | mapping: 1 custom entity(ies)
3) action: resend
4) result: sent msg=901 | پیام اصلی حذف شد
```

- گزارش‌ها با bypass کانورتر ارسال می‌شوند (خودشان تبدیل نمی‌شوند) و
  message_id شان در لیست ignore می‌رود تا روی گزارش‌ها [PREMIUM_CHECK]
  یا Resend اجرا نشود.
- `.premium status` وضعیت زنجیره فعال‌سازی (config/پنل/موتور)، cooldown
  فعال و آمار `resent/deleted/kept/failed` را نشان می‌دهد.

## ۶) Audit مسیرهای outgoing واقعی

### از Unified Pipeline عبور می‌کنند (روی کلاینت Self؛ wrapper نصب شده):
| مسیر | نقش |
|---|---|
| `self.py` (۳۹ نقطه ارسال، شامل `event.reply`) | همه خروجی‌های دستور/پاسخ |
| `services/sender.py` | فرستنده مرکزی |
| `services/media_sender.py` | مدیا + کپشن |
| `services/copy_protected.py` | کپی محتوای محافظت‌شده |
| `services/away.py` | پاسخ خودکار Away |
| `services/deleted_handler.py` | ضدحذف |
| `services/custom_emoji_service.py` | ارسال‌های ایموجی سفارشی |
| `services/premium_resend.py` | نسخه‌های Resend + گزارش‌های Debug |
| `services/scheduler.py` و تسک‌های زمان‌بندی | خروجی‌های خودکار |

همه این‌ها از `send_message`/`send_file`/`_send_album`/`edit_message`
پوشیده‌شده عبور می‌کنند؛ Injector رویداد outgoing آن‌ها را می‌بیند.

### Bypass (عمدی / خارج از محدوده):
| مسیر | دلیل |
|---|---|
| `login_manager.py:525` | پیام «سلف روشن شد» با کلاینت موقتی لاگین؛ کانورتر هنوز نصب نشده (ریسک پایین، ثبت‌شده) |
| `bot/core.py` و `handlers/*` | کلاینت Bot — طبق spec هرگز دست‌نخورده |
| `tools/premium_emoji_live_test.py:199` | RPC خام فقط در ابزار تست |
| `forward_messages` | طبق قانون، فوروارد بایت‌به‌بایت حفظ می‌شود |
| `services/premium_emoji_converter.py:762` | `EditMessageRequest` خام مسیر edit-fallback (عمدی: جلوگیری از تبدیل دوگانه) |

## ۷) تغییر فایل‌ها

| فایل | تغییر |
|---|---|
| `services/premium_resend.py` | بازطراحی کامل جریان تصمیم: [PREMIUM_CHECK]، `sleep 0.5s` + `get_messages`، تصمیم بر اساس نمای سرور، صف آلبوم با واکشی، گزارش Debug + ignore set، ردیابی task ها برای uninstall تمیز |
| `services/telegram_logger.py` | `format_premium_check` + `send_premium_check` + سپرده مستقل `premium_check` |
| `self.py` | دستور `.premium` (debug on/off/status) |
| `tests/test_premium_check_debug.py` | ۱۸ تست جدید (بلوک لاگ، واکشی سرور، آلبوم، گزارش Debug، گیت‌ها) |
| `tests/test_premium_resend.py` | سه تست آلبوم به تأخیر جدید واکشی (`VERIFY_DELAY_SECONDS`) هم‌راستا شدند |
| `AGENTS.md` | قرارداد دور Debug |

هیچ ماژول دیگری تغییر نکرد؛ Bot Client، Away، .بستن و همه قابلیت‌های
قبلی دست‌نخورده‌اند. هیچ ایموجی ثابتی اضافه نشده و Prefix وجود ندارد.

## ۸) نتایج تست واقعی (اجرا شده — نه ادعا)

```
tests/test_premium_check_debug.py   18 passed
tests/test_premium_resend.py        27 passed
سوئیت کامل پروژه:                   846 passed / 6 failed
```

۶ تست ناموفق، روی «کد دست‌نخورده پایه» (بدون تغییرات این دور) هم دقیقاً
همان‌ها هستند (`test_admin_user_pagination`، `test_hardening_v097`،
۲ تست `test_memory_streaming_lock_pagination`، `test_release_regression`،
`test_user_streaming`) — پیش‌عضو محیطی و مرتبط با این دور نیستند.

**محدودیت صادقانه:** تست لایو با اکانت واقعی تلگرام در این محیط (بدون
session) قابل اجرا نبود؛ ابزار `tools/premium_emoji_live_test.py` آماده
است. برای تأیید نهایی روی سرور خودتان:

1. `.premium debug on` بفرستید.
2. در Saved/Private/Group یک پیام با ایموجی نگاشت‌شده (مثلاً `🔥 سلام`)
   بفرستید.
3. بلوک‌های `[PREMIUM_CHECK]` را ببینید: اگر `has_entity: True` بود ولی
   ایموجی معمولی نمایش داده شد → مشکل document_id است (خروجی `entities:`
   شناسه را نشان می‌دهد)؛ اگر سرور `has_entity=False` داد و Resend انجام
   شد → خط `server_check` نسخه جدید را نشان می‌دهد.
4. لاگ کامل در `logs/premium_emoji.log` می‌ماند.

## ۹) قواعد امنیتی حفظ‌شده

- پیام اصلی فقط بعد از ارسال موفق نسخه جدید حذف می‌شود؛ هر شکست → پیام
  اصلی می‌ماند.
- پیام‌های فوروارد، via_bot (پنل) و سرویس هرگز دست نمی‌خورند.
- هیچ ایموجی ثابت / Placeholder / Prefix در هیچ مسیری اضافه نمی‌شود.
- حلقه ارسال مجدد غیرممکن است (recent LRU + cooldown + ignore گزارش‌ها).
