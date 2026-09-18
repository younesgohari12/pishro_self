# گزارش ریشه‌ای v0.09.18 — بازسازی کامل سرویس Premium Emoji Resend

تاریخ: 2026-09-18 | نسخه: 0.09.18 | پایه: 02552cc (v0.09.17)

---

## ۱) ریشه‌های اصلی باگ (چرا بعضی پیام‌ها شناسایی/ارسال مجدد نمی‌شدند)

بررسی معماری قدیم (emoji_resend_manager.py) پنج ریشه واقعی پیدا کرد — نه
فقط یک شرط کمبود:

| # | ریشه | اثر | اصلاح ریشه‌ای |
|---|------|-----|----------------|
| ۱ | **ترتیب ناایمن حذف/ارسال**: پیام اصلی «قبل از» ارسال نسخه جدید حذف می‌شد؛ ارسال شکست بخورد → محتوا از بین می‌رفت (فقط با ارسال نجات‌دهنده جبران می‌شد) | resend ناموفق = پیام گم‌شده | ترتیب جدید spec مالک: **کپی → ارسال نسخه جدید → بعد از موفقیتِ ارسال حذف اصل**؛ شکست ارسال → اصل دست‌نخورده + retry محدود (۳ تلاش + ۱ نجات) + log |
| ۲ | **تشخیص فقط روی متن**: گارد اول `_handle_single` پیام‌های بدون متن را کامل رد می‌کرد؛ پیام‌های مدیا با Entity ویژه/استیکر ایموجی‌دار وارد سیستم نمی‌شدند | «بعضی پیام‌ها شناسایی نمی‌شوند» | تشخیص جامع `_detect_emoji()`: message.message + raw_text + entities (MessageEntityCustomEmoji) + attributes مدیا (DocumentAttributeCustomEmoji) + کپشن مدیا |
| ۳ | **پیام‌های دریافتی پردازش نمی‌شدند**: فقط هندلر outgoing وجود داشت | نصف جریان «ارسال یا دریافت» پوشش نداشت | ناظر مستقل incoming در سرویس (events.NewMessage(incoming=True)) — **فقط شناسایی/گزارش**؛ اقدام مخرب فقط برای پیام‌های خود اکانت (حذف پیام دیگران ناممکن/مخرب است) |
| ۴ | **حلقه در مسیر آلبوم**: حافظه ضد-loop فقط برای پیام‌های تکی چک می‌شد؛ آلبومِ ارسالی خودمان دوباره بافر می‌شد | loop بالقوه در آلبوم‌ها | گارد `_is_recent` در ابتدای مسیر آلبوم + بسته شدن window مسابقه با mark فوری بعد از ارسال |
| ۵ | **reply markup حفظ نمی‌شد** و مدیریت نوع مدیا ناقص بود (گیف = ویدیو، ویدیو نوت = ویدیو) | کپی ناقص + گزارش نادرست | reply_markup در کپی (ارسال با پارامتر buttons تلثون) + ترتیب درست تشخیص: استیکر → گیف → ویدیو/ویدیو نوت → ویس/صوت → فایل |

نکته: الگوی یونیکد ایموجی (EMOJI_PATTERN) از قبل پوشش کامل داشت (keycap،
پرچم، مودیفایر پوست، ZWJ، tag sequences) — مشکل آنجا نبود.

## ۲) معماری جدید

- **منطق کامل به `services/premium_resend_service.py` منتقل شد** (طبق spec
  مالک — سرویس مستقل و مسئول: detect emoji / detect custom emoji / clone /
  resend / safe delete / anti loop / flood control).
- `services/emoji_resend_manager.py` فقط **شیم سازگاری** است (بازنشر همه
  نمادها) — self.py و تست‌ها و ابزارها بدون تغییر کار می‌کنند.
- `services/premium_resend.py` (شیم قدیمی) از طریق زنجیره شیم همچنان سالم است.
- هندلر outgoing کانورتر (`install_premium_emoji_outgoing_injector`) فقط
  `handle_outgoing` سرویس را صدا می‌زند — تک‌نقطه ورود، بدون منطق تکراری.

## ۳) جریان نهایی (spec مالک v0.09.18)

```
پیام (هر چت: خصوصی/گروه/سوپرگروه/کانال/Saved)
   ├─ outgoing → handle_outgoing:
   │    گاردها (action/away/forward/via_bot/خودِ ما/خاموش)
   │    → تشخیص جامع ایموجی (متن/raw/entities/کپشن/attributes)
   │    → بدون ایموجی: هیچ کاری نکن (log)
   │    → Entity ویژه موجود: بدون تغییر (idempotent)
   │    → واکشی نمای واقعی سرور (client.get_messages)
   │    → کپی کامل (متن/entities/مدیا/کپشن/reply/silent/reply_markup)
   │    → ارسال نسخه جدید با Custom Emoji (۳ تلاش + ۱ نجات بدون entity)
   │    → فقط بعد از موفقیتِ ارسال: حذف پیام اصلی
   └─ incoming → handle_incoming: فقط شناسایی + [بررسی ایموجی ویژه] +
        [PREMIUM_RESEND] (direction=incoming) — هرگز delete/send ممنوع
```

- **ضد loop**: cache پردازش‌شده‌ها `(chat_id, message_id)` با TTL ۳۶۰۰ ثانیه
  (`processed_message_ids` + `PROCESSED_TTL_SECONDS`) + پیام‌های خود ما +
  گزارش‌های خود ما + cooldown کانورتر (۲ strip متوالی → ۳۰۰s توقف).
- **flood control**: قفل per-chat + بازبینی داخل قفل + یک تلاش برای هر پیام +
  `SEND_ATTEMPTS=3` / `SEND_RETRY_DELAY=0.4s` / `ALBUM_FLUSH_DELAY=1.0s`.
- **لاگ الزامی**: بلوک انگلیسی `[PREMIUM_RESEND]` با فیلدهای chat_id /
  message_id / emoji_detected / emoji_type=custom|unicode / media_type /
  direction=outgoing|incoming / resend_success (+note) — در
  `telegram_logger.py` با اسلات ضد-اسپم مستقل اضافه شد؛ بلوک‌های فارسی
  [بررسی ایموجی ویژه] و [ارسال دوباره] حفظ شدند.
- **قانون طلایی حفظ شد**: هیچ EditMessageRequest و هیچ edit_message ای در
  هیچ مسیری صدا زده نمی‌شود (تست‌های audit همان قرارداد را اجرا می‌کنند).

## ۴) فایل‌های تغییر یافته

| فایل | تغییر |
|---|---|
| `services/premium_resend_service.py` | **بازنویسی کامل** — منطق رسمی سرویس: تشخیص جامع، ترتیب send→delete، ناظر incoming، گارد ضد-loop آلبوم، reply_markup، بلوک [PREMIUM_RESEND] |
| `services/emoji_resend_manager.py` | شیم سازگاری (بازنشر از سرویس رسمی) |
| `services/telegram_logger.py` | + `format_premium_resend_block` و `send_premium_resend_block` (افزودنی؛ چیزی تغییر نکرد) |
| `tests/test_premium_resend.py` | هدف پچ‌ها به ماژول رسمی + ۳ تست به ترتیب جدید v0.09.18 به‌روز شد + گارد async |
| `tests/test_premium_check_debug.py` | هدف پچ‌ها به ماژول رسمی |
| `tests/test_final_audit_traces.py` | هدف پچ VERIFY به ماژول رسمی (۳ مورد) |
| `tests/test_premium_resend_service_v0918.py` | **جدید** — ۲۴ تست: ماتریس ۱۱ حالته مالک + تشخیص + incoming + بلوک لاگ + cache |
| `storage.py` | `APP_VERSION = '0.09.18'` |

تغییر نکرد: `config.py` (هیچ فلگ/مقداری)، `handlers/`، `db.py`، دیتابیس،
سشن‌ها، `self.py` (نقطه نصب همان امضای قدیمی را صدا می‌زند).

## ۵) تست‌های اجرا شده

```bash
.venv/bin/python -m pytest tests/ -q
# 910 passed / 21 failed (همه شکست‌ها pre-existing: transfer/translation/streaming)
# مقایسه دقیق لیست FAILED با baseline v0.09.17 (886/21): هر دو جهت comm خالی → صفر رگرسیون
```

پوشش ماتریس مالک (نام تست‌ها در `test_premium_resend_service_v0918.py` و
`test_premium_resend.py`):
1. متن با emoji معمولی → `test_case_1_text_with_unicode_emoji` / `test_1_...`
2. متن با custom emoji → `test_case_2_...` / `test_skip_when_custom_entity_already_present`
3. عکس با caption → `test_case_3_...` / `test_3_...`
4. ویدیو با caption → `test_case_4_...` / `test_4_...`
5. فایل با caption → `test_case_5_document_with_caption_emoji`
6. آلبوم → `test_case_6_...` / `test_5_...` + گارد loop آلبوم در `test_case_11`
7. کانال → `test_case_7_channel_post_emoji`
8. گروه → `test_case_8_...` / `test_6_...`
9. private/Saved → `test_case_9_...` / `test_7_...`
10. resend failure → `test_case_10_...` + `test_send_failure_retries_then_rescue_without_entity` + `test_delete_failure_after_resend_keeps_content`
11. anti loop → `test_case_11_anti_loop_cache_ttl_and_album_guard` / `test_no_loop_for_resent_message`

به‌علاوه: تشخیص جامع (۵ تست)، ناظر incoming فقط-مشاهده (۳ تست)، بلوک
[PREMIUM_RESEND] (۲ تست)، reply_markup (۲ تست)، هویت سرویس/shim (۲ تست).

## ۶) Migration لازم

**هیچ مهاجرتی لازم نیست.**
- دیتابیس کاربران، سشن‌ها و `/root/PishroSelfData` کاملاً دست‌نخورده.
- `config.py` و APIها تغییری نکرده‌اند (همه فلگ‌ها همان‌هاست:
  `PREMIUM_EMOJI_ENABLED` و `PREMIUM_EMOJI_RESEND_MODE`).
- رفتار قدیمی‌ای که تغییر کرد فقط ترتیب حذف/ارسال است (به سفارش مالک) و
  در فایل پایدار چیزی برای آن ذخیره نمی‌شود.

## ۷) Deploy بدون حذف دیتا

بسته ZIP کامل (با config.py Loader و migrate.sh) را در `/root/self` استخراج
کنید و سپس:

```bash
sudo bash /root/self/<پوشه-نسخه-جدید>/PishroSelf/migrate.sh \
  /root/self/<پوشه-نسخه-جدید>/PishroSelf
```

تضمین‌های migrate.sh: بکاپ timestamp دار → توقف سرویس → نشانه‌گذاری نسخه
جدید در systemd → venv + requirements → تست import (با assert مسیر دیتا/
سشن) → اجرا → لاگ ۱۰۰ خط → rollback خودکار در خطا. دیتا فقط خواندنی است.
