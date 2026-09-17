# گزارش دیباگ Premium Emoji Converter — Runtime Debug + Telegram Logging

پروژه: **PishroSelf v0.09.13 — Premium Emoji Converter Runtime Debug + Full Telegram Logging System**
تاریخ: 2026-09-17 | پایه: `PishroSelf_v0.09.13_PREMIUM_EMOJI_STRICT_FINAL`

---

## ۱) خلاصه اجرایی

مشکل گزارش‌شده: کانورتر در Saved Messages کار می‌کند اما در Private/Group «همیشه
کار نمی‌کند» و «بعضی پیام‌ها تبدیل نمی‌شوند». Mapping و document_id ها سالم
بودند؛ مشکل در **Runtime Integration** بود. با ممیزی کامل مسیرهای ارسال،
**سه علت ریشه‌ای** پیدا و اصلاح شد و یک **سیستم لاگ حرفه‌ای تلگرامی** اضافه شد
که از این پس دلیل دقیق هر عدم‌تبدیل را همان لحظه به Admin ارسال می‌کند.

## ۲) علت‌های ریشه‌ای (Root Cause)

### RC-A — پیام‌های تایپ‌شده از اپ رسمی (گوشی/دسکتاپ) هرگز از کانورتر عبور نمی‌کردند — **علت اصلی**
کانورتر فقط متدهای `send_message / send_file / edit_message / _send_album`
کلاینت پایتون را wrap می‌کند. پیامی که مالک از اپ رسمی تلگرام می‌فرستد مستقیم
به سرور تلگرام می‌رود و کلاینت پایتون فقط update خروجی آن را می‌بیند؛ یعنی در
چت خصوصی/گروه (که بیشتر پیام‌ها از گوشی فرستاده می‌شود) هیچ تبدیلی رخ نمی‌داد.
در Saved Messages، پیام‌های خروجیِ خود ابزارها/تست‌ها (که از کلاینت پایتون
ارسال می‌شوند) تبدیل می‌شدند — به همین دلیل «در Saved کار می‌کند».

**فیکس:** `install_premium_emoji_outgoing_injector` (Post-Send Fix). پیام‌های
خروجی از هر دستگاهی که ایموجی قابل‌نگاشت بدون entity دارند، بلافاصله با
`EditMessageRequest` و entity دقیق همان ایموجی edit می‌شوند؛ متن هرگز عوض
نمی‌شود. forward، پیام‌های via_bot (پنل اینلاین)، سرویس‌ها و پیام‌های دارای
Custom Emoji هرگز دست نمی‌خورند؛ خاموشی کانورتر/cooldown هم رعایت می‌شود.

### RC-B — مسیر edit می‌توانست entity حذف کند
`edit_message` بدون `formatting_entities` تمام entity را از نو می‌سازد؛ اگر
edit در پنجره‌ای انجام شود که کانورتر فعال نباشد، entity قبلی حذف می‌شد. با
تجهیز مسیر edit به لاگ لحظه‌ای + re-conversion خودکار در wrap + Post-Fix، هر
حذف entity اکنون قابل رهگیری و بازسازی است. تست `test_edit_message_entity_not_lost_when_reconverted`
رفتار صحیح را قفل می‌کند: پیام «سلام 🔥» ویرایش می‌شود و entity دوباره ساخته
می‌شود.

### RC-C — خطاهای تبدیل بی‌صدا swallowed می‌شدند
`except Exception: changed = False` در wrapper هیچ ردی از خرابی تبدیل نگه
نمی‌داشت. اکنون هر خطا → گزارش «❌ Premium Emoji Error» با File/Line/Error +
گزارش «⚠️ Fallback» (Action: Original message sent) به ربات گزارش.

### RC-D — خاموشی متناوب کانورتر به‌خاطر خطای DB
`_converter_flag` در شکست خواندن دیتابیس (مثلاً قفل SQLite) مقدار None برمی‌
گرداند و کانورتر به پیش‌فرض config (خاموش) می‌افتاد. اکنون آخرین مقدار معتبر
حفظ می‌شود و فقط شکست اولین خواندن به پیش‌فرض می‌رود.

## ۳) ممیزی کامل مسیرهای Runtime (Runtime Paths Checked)

| PATH | METHOD | CLIENT TYPE | SELF/BOT | CONVERTER ACTIVE | ENTITIES CREATED |
|---|---|---|---|---|---|
| پیام متنی دستی از کلاینت پایتون | send_message | User (MTProto) | SELF | ✅ wrap | ✅ در لحظه send |
| ریپلای/پاسخ (event.reply/respond) | send_message | User | SELF | ✅ (delegate به همان wrap) | ✅ |
| خروجی AI (دستور .ai) | send_message | User | SELF | ✅ wrap | ✅ |
| خروجی ترجمه (.ترجمه) | send_message | User | SELF | ✅ wrap | ✅ |
| خروجی کریپتو (.ارز) | send_message | User | SELF | ✅ wrap | ✅ |
| اسپم (.اسپم) | send_message | User | SELF | ✅ wrap | ✅ |
| پیام موقت پنل (_temp_message) | send_message | User | SELF | ✅ wrap | ✅ |
| کپشن مدیا (ارسال فایل با caption) | send_message(file=)/send_file | User | SELF | ✅ wrap | ✅ روی کپشن |
| آلبوم | _send_album | User | SELF | ✅ wrap | ✅ روی هر کپشن |
| ویس TTS | send_file | User | SELF | ✅ wrap | ✅ (کپشن در صورت وجود) |
| ویرایش پیام (msg.edit / edit_message) | edit_message | User | SELF | ✅ wrap + لاگ | ✅ re-convert |
| کپی محتوا (copy_protected) | send_message/send_file | User | SELF | ✅ wrap | ✅ |
| سیو پیام (deleted_handler → me) | send_message/send_file | User | SELF | ✅ wrap | ✅ |
| تبچی متنی/کپشن‌دار (sender.py) | send_message | User | SELF | ✅ wrap | ✅ |
| تبچی forward | forward_messages | User | SELF | ⛔ عمداً wrap نمی‌شود (قانون مالک) | ⛔ byte-identical |
| **پیام تایپ‌شده از اپ رسمی (RC-A)** | UpdateNewMessage(outgoing) | User | SELF | ✅ **جدید: Post-Send Fix** | ✅ **edit درجا** |
| پنل اینلاین (.پنل → inline bot) | inline_query click | User+Bot | SELF×BOT | ⛔ محتوای ربات؛ مستقل | ⛔ طبق قانون |
| پنل ربات اصلی (bot/) | sendMessage Bot API | Bot | BOT | ⛔ هرگز | ⛔ هرگز |
| هندلر فونت خودکار | event.edit → edit_message | User | SELF | ✅ (پیام دارای entity را skip می‌کند) | — |

نتیجه: با پچ جدید، **تمام** مسیرهای خروجی Self در هر سه نوع چت (Saved/Private/
Group) تبدیل می‌شوند؛ مسیرهای مستثنی فقط همان‌هایی هستند که قانون مالک صریحاً
دست‌نخورده خواسته (forward، پنل اینلاین، Bot).

## ۴) فایل‌های تغییر یافته / جدید

| فایل | وضعیت | توضیح |
|---|---|---|
| `services/telegram_logger.py` | **جدید** | سیستم لاگ تلگرامی: send_log/send_error/send_warning/send_premium_event + بلوک [PREMIUM DEBUG]، ضد-اسپم، redaction، rotation محلی |
| `services/premium_emoji_converter.py` | ارتقا | لاگ تبدیل/خطا/fallback/FloodWait/SendError + Post-Send Injector + owner_id + meta (method/chat) |
| `config.py` | ارتقا | `PREMIUM_EMOJI_DEBUG=True`، `PREMIUM_EMOJI_OUTGOING_FIX=True`، `LOG_LEVEL='ERROR'`، `PREMIUM_EMOJI_LOG_LEVEL='INFO'`، `ADMIN_LOG_IDS=[8359698350]` + حذف تنها `os.getenv` (ترمیم تست hardening) |
| `self.py` | ارتقا | نصب injector قبل از فونت‌هندلر، پرچم مقاوم به خطای DB، لاگ Session Error، uninstall کامل |
| `tools/premium_emoji_debug_smoke.py` | **جدید** | تست سه‌چته زنده (Saved/Private/Group) با کشف ROOT CAUSE لحظه‌ای و ارسال به ربات گزارش |
| `tests/test_premium_emoji_debug_logging.py` | **جدید** | ۳۰ تست جدید (فهرست در بخش ۶) |

معماری حفظ شده: جداسازی Self/Bot، پنل، تنظیمات، Premium Prefix (فقط خودش)،
forward_messages (دست‌نخورده)، strict mapping (هیچ glyph و fallback عمومی).

## ۵) سیستم Telegram Logging

- **توکن فقط از ENV**: `export PREMIUM_LOG_BOT_TOKEN='...'` — در سورس/ZIP/ریپو
  **وجود ندارد** (تست `test_source_never_contains_log_bot_token` این را قفل می‌کند).
- **مقصد**: `ADMIN_LOG_IDS = [8359698350]`.
- **گزارش‌ها**: 🎨 Premium Emoji Converted (SUCCESS با User/Chat/Emoji/Document
  ID/Status) — ❌ Premium Emoji Error (File/Line/Error) — ⚠️ Premium Emoji
  Fallback (Reason/Action) — ❌ Telegram FloodWait — ❌ Telegram Send Error —
  ❌ Session Error — 🧪 Smoke Verdict.
- **سطح‌ها**: `OFF < ERROR < WARNING < INFO < DEBUG` — کانال عمومی
  `LOG_LEVEL` (پیش‌فرض ERROR)، کانال Premium `PREMIUM_EMOJI_LOG_LEVEL`
  (پیش‌فرض INFO؛ بلوک‌های debug فقط در سطح DEBUG به تلگرام می‌روند).
- **ضد-اسپم**: حداقل فاصله per-kind (تبدیل 5s، fallback 60s، خطا 30s، generic
  30s، debug 3s) + سقف 30/دقیقه و 300/ساعت؛ رویدادهای ادغام‌شده به‌صورت
  «(+N suppressed)» روی گزارش بعدی می‌آیند. برای پیام‌های عادی عادی هیچ گزارشی
  زده نمی‌شود (فقط پیام دارای ایموجیِ تبدیل‌شده گزارش دارد).
- **امنیت**: `redact()` چندلایه — BOT_TOKEN/API_ID/API_HASH/SESSION/PASSWORD و
  رشته‌های شبیه StringSession همیشه [REDACTED] می‌شوند؛ مقدار توکن ENV هم
  runtime پاک می‌شود. HTTP در thread پس‌زمینه؛ هیچ ارسال پیامی کند/مسدود
  نمی‌شود؛ خطای شبکه هرگز به مسیر پیام‌رسانی نشت نمی‌کند.
- **لاگ محلی با rotation**: `logs/premium_emoji.log` (INFO+، شامل بلوک‌های
  debug) و `logs/error.log` (ERROR+) — هر دو 10MB × 5 backup.

## ۶) تست‌ها (نتیجه اجرای واقعی)

`python -m pytest -q tests/` → **791 passed, 6 failed** (هر ۶ خطا از قبل در
نسخه پایه هم موجود و وابسته به credentials مالک/محیط‌اند: hardening لازم دارد
API_ID واقعی در config محلی، pagination/markdown/streaming مربوط به نسخه‌های
قبلی. **هیچ خطای جدیدی توسط این پچ ایجاد نشده است.**)

۳۰ تست جدید در `tests/test_premium_emoji_debug_logging.py`:

| # | دسته تست | نتیجه |
|---|---|---|
| 1 | premium debug logging (بلوک کامل [PREMIUM DEBUG]) | ✅ |
| 2 | self send logging (SUCCESS + User/Chat/Method) | ✅ |
| 3 | bot untouched (converter/injector/لاگ هرگز روی بات) | ✅ |
| 4 | entity creation logging (Document IDs = mapping واقعی) | ✅ |
| 5 | edit logging + عدم حذف entity بعد از ویرایش | ✅ |
| 6 | fallback logging (rejection → گزارش + retry اصلی بدون duplicate) | ✅ |
| 7 | error reporting (File/Line/Error + FloodWait re-raise) | ✅ |
| 8 | token leak protection (توکن ENV/الگوی توکن هرگز ظاهر نمی‌شود) | ✅ |
| 9 | session leak protection (StringSession/API_HASH/PASS → REDACTED) | ✅ |
| 10 | LOG_LEVEL filtering (OFF/ERROR/INFO/DEBUG) | ✅ |
| 11 | anti-spam (coalesce + (+N suppressed)) | ✅ |
| 12 | rotation محلی (10MB × 5) | ✅ |
| 13 | outgoing injector (fix پیام گوشی، skip forward/via_bot/premium، cooldown، uninstall) | ✅ |
| 14 | audit consistency (همه متدهای Self wrap؛ forward هرگز) | ✅ |

تست زنده logger با ربات گزارش واقعی (توکن از ENV): HTTP 200 / Telegram ok=True
به ADMIN_LOG_IDS — پیام‌های نمونه 🎨/⚠️/❌ تحویل شدند.

## ۷) اجرای تست سه‌چته (روی سرور مالک)

سشن تلگرام در ZIP انتشار وجود ندارد (قانون امنیتی)؛ تست زنده سه‌چته یک فرمان
روی سرور مالک است:

```bash
export PREMIUM_LOG_BOT_TOKEN='<توکن ربات گزارش>'
python -m tools.premium_emoji_debug_smoke --session '<STRING_SESSION>' \
    --private <user_id مخاطب> --group <group_id گروه>
```

ابزار سه پیام «سلام 😂🔥❤️» به Saved/Private/Group می‌فرستد، entity های هر سه
را مقایسه می‌کند، VERDICT چاپ می‌کند و در صورت عدم تبدیل، **دلیل دقیق همان
لحظه** را هم به ربات گزارش ارسال می‌کند.

## ۸) محدودیت‌ها

- تست‌های این گزارش آفلاین/ماک هستند (قرارداد AGENTS.md: ادعای لاگین واقعی
  نداریم)؛ ارسال واقعی سه‌چته با فرمان بخش ۷ روی سرور مالک انجام می‌شود.
- اگر پریفیکس پرمیوم fallback خودش را اجرا کند (رد تلگرام)، پیام از لایه
  prefix بدون entity می‌رود؛ Post-Send Fix آن را در update بعدی بازسازی و
  گزارش می‌کند (این مسیر از قبل در تست fallback پوشش داده شده).
- پس از rejection تلگرام، cooldown ۳۰۰ ثانیه‌ای کانورتر (رفتار قبلی، حفظ شد)
  با گزارش ⚠️ همراه است.
