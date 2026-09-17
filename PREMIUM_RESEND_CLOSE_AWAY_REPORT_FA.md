# گزارش نهایی — Premium Resend + بستن عملیات‌ها + Away Message
**PishroSelf v0.09.13 — PREMIUM_RESEND_CLOSE_AWAY**

---

## خلاصه اجرایی

هر سه بخش spec پیاده‌سازی و **۸۲۸ تست واقعی اجرا شد** (۶۵ تست جدید). سوئیت کامل: `828 passed / 6 failed` — هر ۶ خطا **قبل از تغییرات هم روی HEAD تمیز وجود داشتند** (محیطی، پیش‌عضو؛ با git stash راستی‌آزمایی شد). هیچ قابلیت قبلی حذف یا خراب نشد؛ کلاینت Bot و forward ها طبق قانون دست‌نخورده ماندند.

---

## بخش 1 — Premium Emoji ارسال مجدد هوشمند (Copy/Delete/Resend)

### نکته نام‌گذاری
در spec مالک `PREMIUM_EMOJI_MODE = ON` آمده؛ چون `PREMIUM_EMOJI_MODE` قبلاً برای «روش انتخاب شناسه» (round_robin/random) استفاده می‌شود، حالت جدید با نام **`PREMIUM_EMOJI_RESEND_MODE`** اضافه شد تا هیچ رفتار قبلی نشکند.

### Flow پیاده‌شده (دقیقاً مطابق spec)
1. پیام اصلی کاربر ارسال می‌شود (مسیر اصلی: تبدیل قبل از ارسال).
2. بلافاصله بررسی می‌شود: آیا متن ایموجی نگاشت‌پذیر دارد و آیا entity در **پیام نهایی تلگرام** واقعاً وجود دارد؟
3. اگر entity نبود (سرور حذف کرده یا پیام از گوشی رسیده):
   - پیام **دقیقاً کپی** می‌شود: متن، Markdown/Entities، Reply To، عکس، ویدیو، فایل، کپشن، **آلبوم (یک‌جا)**، silent.
   - کپی از **Unified Pipeline** عبور می‌کند → Custom Emoji واقعی از اولین بایت.
   - پیام اصلی فقط **بعد از ارسال موفق** حذف می‌شود.
4. نتیجه نهایی: فقط **یک پیام**: «🔥(Custom Emoji) سلام» — هرگز دو پیام.

### قواعد امنیتی
- **اگر ارسال مجدد یا حذف شکست بخورد، پیام اصلی باقی می‌ماند** (هیچ پیامی گم نمی‌شود).
- ضد race: قفل per-chat + حافظه پیام‌های خودمان (loop-guard) + احترام به cooldown موتور.
- اگر سرور دوباره entity را حذف کند: بعد از ۲ بار پیاپی، cooldown به مدت ۳۰۰ ثانیه (هیچ پیامی دوبل نمی‌شود).
- اگر نسخه ارسالی بدون entity برگردد (clean retry بعد از reject تلگرام): اصل حذف نمی‌شود و مسیر edit تلاش می‌کند.
- آلبوم‌ها با debounce ۱ ثانیه جمع شده و **یک‌جا** دوباره ارسال می‌شوند؛ اگر هیچ قطعه‌ای entity نگیرد، همه اصل‌ها حفظ می‌شوند.
- پوشش کامل: **Saved / Private / Group / Channel (با اجازه) / Reply / Photo / Video / Document / Caption / Album**.

### زنجیره فعال‌سازی (Production-Safe)
`PREMIUM_EMOJI_RESEND_MODE` (کلید سخت config، پیش‌فرض True) **و** کانورتر مؤثر (`PREMIUM_EMOJI_ENABLED` + پنل) **و** انتخاب پنل هر حساب `premium_emoji_resend` (None = لمس‌نشده → روشن؛ True/False = انتخاب مالک و اولویت دارد).
پنل: `.پنل` → دکمه «🔁 Resend هوشمند» (روشن/خاموش + هشدارهای راهنما).

### اولویت Resend بر edit
هندلر outgoing موجود (`install_premium_emoji_outgoing_injector`) ابتدا Resend را صدا می‌زند؛ فقط اگر Resend پیام را نگرفت، مسیر edit قبلی (رفتار پیشین) اجرا می‌شود → **قابلیت قبلی حفظ شده**.

---

## بخش 2 — دستور `.بستن`

یک دستور، همه چیز در همان لحظه (در همه چت‌ها، همیشه فعال حتی وقتی سلف خاموش است):

| چیزی که بسته می‌شود | جزئیات |
|---|---|
| پنل تنظیمات | پیام‌های via_bot ربات اینلاین در همان چت شناسایی و حذف می‌شوند |
| wizard ایموجی | استخراج/تست Custom Emoji → state پاک |
| ورودی متن | تغییر متن Away از پنل → capture لغو |
| حالت انتظار فایل/مقصد | مقصد «سیو پیام» و مقصد «کپی محتوا» → لغو |
| انتخاب صدا TTS | همه چت‌ها + حذف پیام انتخابگر |
| عملیات نیمه‌کاره | تسک‌های اسپم/پاک‌سازی همه چت‌ها cancel |
| تایید عملیات | state پنل ادمین (حافظه + دیتابیس) پاک |

- پاسخ: «✅ عملیات بسته شد» — **هیچ state باقی نمی‌ماند**.
- نکته: دستور `.کنسل` قبلی دست‌نخورده ماند (per-chat)؛ `.بستن` مکمل سراسری آن است.
- اگر ربات اینلاین در دسترس نباشد، بقیه state ها باز هم بسته می‌شوند.

---

## بخش 3 — پیام خودکار آفلاین (Away Message)

### رفتار
- **فقط چت خصوصی**؛ گروه/کانال/بات‌ها/پیام سرویس/چت‌های سکوت و دشمن: هرگز.
- **برای هر کاربر فقط یک بار** تا: برگشت مالک آنلاین (هر پیام خروجی مالک = ریست لیست) یا ریست دستی یا TTL اختیاری (`AWAY_RESET_HOURS`، پیش‌فرض 0=خاموش).
- پاسخ‌های خود Away هرگز «نشانه برگشت» حساب نمی‌شوند (لیست چشم‌پوشی اختصاصی) → قانون «یک بار» همیشه برقرار است.
- پاسخ از `client.send_message` عبور می‌کند → Unified Pipeline و Premium Emoji روی آن فعال است؛ هیچ ارسال مستقیمی وجود ندارد.

### کنترل‌ها
- دستورها: `.away` (وضعیت) / `.away on` / `.away off` / `.away text <متن>` / `.away reset`
- پنل: `.پنل` → «💤 Away Message» → روشن/خاموش، ✏️ تغییر متن (ارسال متن بعدی)، 🧹 ریست لیست
- متن پیش‌فرض: «سلام، الان در دسترس نیستم. به محض آنلاین شدن جواب می‌دهم.» (حداکثر ۵۰۰ کاراکتر، اعتبارسنجی دارد)

---

## لاگ‌های استاندارد (سه بلوک جدید)

```
[PremiumResend]          [AWAY]                  [STATE]
chat: Group (-777)       user: 123456789         closed: wizard, panel, tts_voice_selector
message_id: 123          sent: True              chat: -100555
converted: True          reason: first_message   old_state: extract, tts@1 chats, admin_state
deleted: True
resent: True
```
- `[PremiumResend]` → `logs/premium_emoji.log` + کانال تلگرام (`PREMIUM_EMOJI_RESEND_DEBUG`، کانال `PREMIUM_EMOJI_LOG_LEVEL`)
- `[AWAY]` و `[STATE]` → `logs/system_events.log` (جدید، rotation 10MB×5) + کانال (`AWAY_DEBUG`/`STATE_DEBUG`، کانال `AWAY_LOG_LEVEL`)
- رویدادهای پرتکرار (مثل already_notified و ریست) فقط لاگ محلی می‌شوند تا کانال اسپم نشود.

---

## فایل‌های تغییر یافته / جدید

| فایل | نوع | دلیل |
|---|---|---|
| `services/premium_resend.py` | **جدید** | موتور Copy/Delete/Resend (تک‌ماژول طبق spec) |
| `services/state_closer.py` | **جدید** | موتور بستن همه عملیات‌های در انتظار (.بستن) |
| `services/away.py` | **جدید** | ماژول مستقل Away (هندلرها، تنظیمات، capture متن پنل) |
| `services/premium_emoji_converter.py` | ویرایش | نقطه اتصال Resend در هندلر outgoing (اولویت Resend → edit) |
| `services/telegram_logger.py` | ویرایش | سه بلوک جدید + فایل لاگ system_events.log + anti-spam kind ها |
| `config.py` | ویرایش | `PREMIUM_EMOJI_RESEND_MODE`، `PREMIUM_EMOJI_RESEND_DEBUG`، `AWAY_DEBUG`، `AWAY_LOG_LEVEL`، `STATE_DEBUG`، `AWAY_RESET_HOURS` + `resend_mode_config_updates()` |
| `db.py` | ویرایش | کلیدهای `premium_emoji_resend`/`away_enabled`/`away_text`/`away_sent_users` + نرمال‌سازی + `DEFAULT_AWAY_TEXT` |
| `self.py` | ویرایش | نصب Resend + Away، دستورهای `.بستن` و `.away`، uninstall تمیز |
| `inline.py` | ویرایش | دکمه «Resend هوشمند» + صفحه پنل Away + callback ها |
| `handlers/admin.py` | ویرایش | `clear_pending_state(uid)` برای بستن state های ادمین با .بستن |
| `main.py` | ویرایش | marker ارتقا `v0.09.13-premium-resend-away` (کلید جدید به config نصب‌های موجود اضافه می‌شود؛ تغییر دستی مالک حفظ می‌شود) |
| `tools/premium_emoji_live_test.py` | ویرایش | سناریوی لایو ۷: Resend واقعی (پیام خام گوشی‌مانند → انتظار کپی/حذف) |
| `AGENTS.md` | ویرایش | قرارداد سه قابلیت جدید |
| `tests/test_premium_resend.py` | **جدید** | ۲۷ تست |
| `tests/test_state_closer.py` | **جدید** | ۵ تست |
| `tests/test_away.py` | **جدید** | ۲۱ تست |
| `tests/test_resend_away_settings.py` | **جدید** | ۱۲ تست |

---

## معماری جدید

```
                         ┌────────────────────────────────────────────┐
 کاربر (گوشی/سلف) ──────►│  Unified Pipeline (کلاینت Self)             │
                         │  send_message / send_file / _send_album /   │
                         │  edit_message  ← تبدیل قبل از ارسال         │
                         └───────────────┬────────────────────────────┘
                                         ▼
                          Telegram Server (شاید entity را حذف کند)
                                         │
                                         ▼
                    ┌─────────────────────────────────────────────┐
                    │ outgoing injector (fallback مالک: edit)      │
                    │  1) 🔁 Premium Resend (اولویت)               │
                    │     verify → کپی دقیق → ارسال با entity      │
                    │     → حذف اصل  (شکست ⇒ اصل می‌ماند)          │
                    │  2) edit fallback (رفتار قبلی، حفظ شده)      │
                    └─────────────────────────────────────────────┘
  موازی و مستقل:
  💤 Away (incoming خصوصی، یک‌بار/کاربر، ریست با فعالیت مالک) → send_message → Pipeline
  📌 .بستن → state_closer (پنل/wizard/TTS/capture/تسک/ادمین) → [STATE]
```

---

## سناریوهای تست

### اجرای واقعی در همین محیط (pytest — ۶۵ تست جدید، همه PASS)
**Premium Resend (۲۷):** متن ساده (entity + حذف اصل) • Reply + Entities حفظ • عکس+کپشن با send_file • گروه و سیو • skip وقتی entity هست • skip بدون ایموجی نگاشت‌پذیر • skip با پنل خاموش/config خاموش • skip فوروارد/via_bot/سرویس • شکست ارسال → اصل می‌ماند • شکست حذف → اصل می‌ماند • ضد حلقه برای پیام خودمان • strip-دو-بار → cooldown • cooldown مسدود می‌کند • آلبوم یک‌جا (کپشن+reply حفظ، حذف هر دو قطعه) • آلبوم تبدیل‌شده → بدون resend • نصب/حذف نصب • رد کلاینت بدون کانورتر • رد کلاینت Bot • فرمت بلوک [PremiumResend] • یکپارچگی injector (resend برنده بر edit) • fallback edit وقتی resend خاموش • guard نسخه-بدون-entity (تک + آلبوم)

**بستن (۵):** همه state ها همزمان (wizard/capture/TTS/تسک/ادمین/پنل) و هیچ state باقی نمی‌ماند • حالت خالی → old_state=none • فرمت بلوک [STATE] • ناپدید شدن ربات اینلاین → بقیه بسته می‌شوند • لغو تسک‌های اسپم/پاک‌سازی

**Away (۲۱):** پیام اول → ارسال • پیام دوم همان کاربر → هیچ • چند کاربر → هر کدام یک بار • خاموش → هیچ • روشن/خاموش چرخه • گروه/کانال → هیچ • بات → هیچ • پیام سرویس → هیچ • چت سکوت → هیچ • ریست با فعالیت مالک • پاسخ Away خودش ریست نمی‌کند • ریست دستی • اعتبارسنجی متن • متن سفارشی ارسال می‌شود • capture پنل (ذخیره/دستور/لغو) • TTL انقضا • ثبت یک‌باره هندلرها • فرمت [AWAY]

**تنظیمات/پنل (۱۲):** کلیدهای دیتابیس و نرمال‌سازی • زنجیره effective (config سخت/کانورتر/پنل) • دکمه‌های پنل اصلی • صفحه Away • marker config • وجود کلیدهای config • الگوی دستورهای `.بستن`/`.away`

### تست لایو تلگرام (ابزار، بدون جعل)
`tools/premium_emoji_live_test.py` ارتقا یافت (سناریوی ۷: Resend واقعی). بدون سشن معتبر، ابزار **واقعاً اجرا و در همین محیط با exit code 2 خارج شد** — هیچ نتیجه‌ای جعل نشد. روی سرور با سشن:
```
export PREMIUM_LIVE_SESSION='<StringSession>'
python -m tools.premium_emoji_live_test --private <id> --group <id> --channel <id> --file f.jpg --file2 f2.jpg
```
(طبق قاعده مالک: هر نتیجه‌ای که در این گزارش از تست لایو تلگرام نیامده، اجرا نشده است؛ ۸۲۸ نتیجه pytest بالا واقعاً اجرا شده‌اند.)

---

## تضمین‌ها
1. **هیچ قابلیت قبلی حذف نشد** — edit fallback، `.کنسل`، تبچی، AI، ترجمه و ... همه سر جایشان.
2. **هیچ ایموجی ثابت یا Prefix اضافه نمی‌شود** — فقط نگاشت دقیق مرکزی (Strict)؛ متن هرگز تغییر نمی‌کند.
3. **Bot Client ۱۰۰٪ دست‌نخورده** — resend/away/state_closer فقط روی کلاینت Self نصب می‌شوند؛ forward ها هرگز.
4. هیچ پیامی در هیچ شکستی گم نمی‌شود؛ حذف همیشه آخرین قدم است.
