# گزارش Audit نهایی — نسخه v0.09.15

**PishroSelf — Audit نهایی (بدون قابلیت جدید)**

این گزارش طبق دستور مالک، فقط یک **Audit** است: اثبات «اتصال واقعی» دو سیستم
«پیام عدم حضور (Away)» و «ایموجی ویژه (Premium Emoji)» با ردیابی واقعی،
بدون افزودن هیچ قابلیت جدیدی. نسخه بررسی‌شده: **v0.09.15**
(tag `v0.09.15-away-bypass-premium-fa`، commit `34db5c7`).

---

## ۱) [AWAY_TRACE] — ردیابی هر ارسال پیام عدم حضور

برای «هر» ارسال پاسخ عدم حضور، یک بلوک `[AWAY_TRACE]` صادر می‌شود. قالب دقیقاً
مطابق spec مالک است (نام فیلدها انگلیسی، مقادیر فارسی):

```
[AWAY_TRACE]

chat_id: 1001
trigger: پیام خصوصی ورودی (فرستنده=1001, پیام=5)
bypass_active: بله (AWAY_BYPASS_PREMIUM=True)
premium_pipeline_entered: خیر — هرگز (گارد تایید کرد: converter)
resend_entered: خیر — هرگز (مدیر ارسال دوباره این پیام را دید و بدون هیچ اقدامی رد کرد (registry))
final_sender: client.send_message (بدون تبدیل — متن خام)
```

### معنی هر فیلد (مقدار واقعی، نه ادعا)

| فیلد | منبع مقدار | اثبات |
|---|---|---|
| `chat_id` | چتی که پاسخ عدم حضور به آن ارسال شد | away.py |
| `trigger` | رویدادی که این ارسال را آغاز کرد (پیام خصوصی ورودی + فرستنده/پیام) | away.py `_handle_incoming` |
| `bypass_active` | آیا `away_send_guard` واقعاً فعال بود؟ (خروجی خود guard) | away_bypass.py |
| `premium_pipeline_entered` | **خیر — هرگز**؛ تأیید «گارد واقعی» کانورتر که در همان لحظه ارسال برخورد کرده (نه یک فرض) | converter wrap → `note_pipeline_guard('converter')` |
| `resend_entered` | **خیر — هرگز**؛ پیام در registry ثبت می‌شود و مدیر ارسال دوباره در اولین بررسی آن را رد می‌کند | resend manager → `note_resend_away_skip(message)` |
| `final_sender` | فرستنده واقعی: `client.send_message` خام، بدون تبدیل، بدون entity | away.py |

**هدف spec مالک برآورده شد:** اثبات اینکه Away هیچ وقت وارد Premium نمی‌شود —
از **سه نقطه مستقل** ثبت می‌شود (خودِ ارسال، گارد کانورتر، رد شدن از مدیر ارسال
دوباره) نه فقط از یک نقطه.

### کجا صادر می‌شود؟

- **محلی:** همیشه در لاگ فایل سیستم (`logs/system.log`).
- **تلگرام:** با پرچم `AWAY_DEBUG` (پیش‌فرض روشن) با سپرده ضد-اسپم مستقل
  (`away_trace`) تا بلوک `[پیام عدم حضور]` سرکوب نشود.

---

## ۲) [PREMIUM_TRACE] — ردیابی تصمیم Premium Resend

برای هر تصمیم مدیر ارسال دوباره، یک بلوک `[PREMIUM_TRACE]` صادر می‌شود:

```
[PREMIUM_TRACE]

message_id: 701
is_away: خیر
entity_check: انجام شد (نمای سرور)
delete_called: بله
new_send_called: بله
```

### قواعد صدور

| حالت | is_away | entity_check | delete_called | new_send_called |
|---|---|---|---|---|
| **پیام عدم حضور** (غیرمشروط — حتی با Resend خاموش) | **بله** | انجام نشد (پاسخ عدم حضور — بدون بررسی سرور) | خیر | خیر |
| جریان کامل ارسال دوباره | خیر | انجام شد (نمای سرور) | بله | بله |
| Entity سالم روی سرور | خیر | انجام شد (نمای سرور) — Entity موجود | خیر | خیر |
| Entity در event موجود | خیر | Entity در event موجود است | خیر | خیر |
| بدون ایموجی قابل‌نگاشت | خیر | انجام نشد (ایموجی قابل‌نگاشت ندارد) | خیر | خیر |
| پیام روی سرور نیست | خیر | انجام شد (نمای سرور) — پیام غایب | خیر | خیر |
| نگاشت پیدا نشد | خیر | انجام شد (نمای سرور) — نگاشت پیدا نشد | خیر | خیر |
| ارسال دوباره خاموش | خیر | انجام شد (نمای سرور) — ارسال دوباره خاموش | خیر | خیر |
| حذف ناموفق | خیر | انجام شد (نمای سرور) | خیر | خیر |
| ارسال جدید ناموفق | خیر | انجام شد (نمای سرور) | بله | خیر |
| آلبوم (عکس/ویدیو) | خیر | انجام شد (نمای سرور) — آلبوم | بله/خیر | بله/خیر |

نکته کلیدی Audit: بررسی `is_away_reply` **اولین و غیرمشروط‌ترین** بررسی
`handle_outgoing` است؛ حتی وقتی «ارسال دوباره» از پنل خاموش باشد، پیام Away
دیده و رد می‌شود و همین را با `[PREMIUM_TRACE] (is_away: بله)` گزارش می‌کند.

---

## ۳) لیست کامل مسیرهای ارسال پروژه + طبقه‌بندی

متدهای اسکن‌شده: `client.send_message`، `client.send_file`، `reply`، `respond`
(و برای شفافیت: `edit_message` و `delete_messages`). اسکن با AST روی کل
کد runtime (بدون tests/tools). نتیجه: **۳۰۰ نقطه ارسال**.

### خلاصه طبقه‌بندی

| طبقه | تعداد | توضیح |
|---|---:|---|
| 🚦 **Away Bypass** | **۱** | فقط `_handle_incoming` در `services/away.py` — تنها ارسالی که داخل `away_send_guard` است و هرگز Premium را نمی‌بیند |
| 🔁 **Premium Pipeline (خروجی Resend)** | ۴ | `_send_once` (متن/مدیا) و `_send_album_copy` در `emoji_resend_manager.py` — خروجی خودِ مدیر ارسال دوباره با entity صریح + loop-guard |
| 🧪 **مستقیم (گزارش داخلی)** | ۱ | `_debug_report` — با `engine.bypass` فعال، بدون تبدیل |
| 🤖 **مستقیم (کلاینت بات)** | ۶۰ | تمام ارسال‌های `bot/core.py` و مسیرهای بات — کانورتر **هرگز** روی کلاینت بات نصب نمی‌شود (نگهبان `account.bot`) |
| ✨ **Premium Pipeline (ورودی سلف)** | ۲۳۴ | تمام `send_message`/`send_file`/`reply`/`respond` کلاینت سلف — از wrap کانورتر قبل از ارسال عبور می‌کنند (در حالت Away: گارد خام عبور می‌دهد) |
| **جمع** | **۳۰۰** | |

### جدول کامل (۳۰۰ نقطه)

| فایل | خط | متد | تابع دربرگیرنده | طبقه‌بندی |
|---|---|---|---|---|
| `services/away.py` | 229 | `send_message()` | `_handle_incoming` | 🚦 Away Bypass |
| `services/away.py` | 287 | `send_message()` | `_away_text_input_handler` | ✨ Premium Pipeline (ورودی سلف) |
| `services/away.py` | 280 | `send_message()` | `_away_text_input_handler` | ✨ Premium Pipeline (ورودی سلف) |
| `services/copy_protected.py` | 203 | `send_message()` | `_send_location` | ✨ Premium Pipeline (ورودی سلف) |
| `services/copy_protected.py` | 221 | `send_message()` | `_send_contact` | ✨ Premium Pipeline (ورودی سلف) |
| `services/copy_protected.py` | 235 | `send_message()` | `copy_message_to` | ✨ Premium Pipeline (ورودی سلف) |
| `services/copy_protected.py` | 242 | `send_message()` | `copy_message_to` | ✨ Premium Pipeline (ورودی سلف) |
| `services/copy_protected.py` | 249 | `send_message()` | `copy_message_to` | ✨ Premium Pipeline (ورودی سلف) |
| `services/copy_protected.py` | 269 | `send_message()` | `copy_message_to` | ✨ Premium Pipeline (ورودی سلف) |
| `services/copy_protected.py` | 380 | `send_message()` | `_handle_destination_input` | ✨ Premium Pipeline (ورودی سلف) |
| `services/copy_protected.py` | 419 | `send_message()` | `_handle_destination_input` | ✨ Premium Pipeline (ورودی سلف) |
| `services/copy_protected.py` | 283 | `send_file()` | `copy_message_to` | ✨ Premium Pipeline (ورودی سلف) |
| `services/copy_protected.py` | 285 | `send_file()` | `copy_message_to` | ✨ Premium Pipeline (ورودی سلف) |
| `services/copy_protected.py` | 404 | `send_message()` | `_handle_destination_input` | ✨ Premium Pipeline (ورودی سلف) |
| `services/copy_protected.py` | 441 | `send_message()` | `_handle_destination_input` | ✨ Premium Pipeline (ورودی سلف) |
| `services/copy_protected.py` | 298 | `send_file()` | `copy_message_to` | ✨ Premium Pipeline (ورودی سلف) |
| `services/copy_protected.py` | 300 | `send_file()` | `copy_message_to` | ✨ Premium Pipeline (ورودی سلف) |
| `services/custom_emoji_service.py` | 146 | `send_message()` | `send_custom_emoji` | ✨ Premium Pipeline (ورودی سلف) |
| `services/deleted_handler.py` | 244 | `send_message()` | `_handle_destination_input` | ✨ Premium Pipeline (ورودی سلف) |
| `services/deleted_handler.py` | 343 | `send_message()` | `_deliver_ttl_media` | ✨ Premium Pipeline (ورودی سلف) |
| `services/deleted_handler.py` | 350 | `send_file()` | `_deliver_ttl_media` | ✨ Premium Pipeline (ورودی سلف) |
| `services/deleted_handler.py` | 250 | `send_message()` | `_handle_destination_input` | ✨ Premium Pipeline (ورودی سلف) |
| `services/emoji_resend_manager.py` | 721 | `send_message()` | `_send_once` | 🔁 Premium Pipeline (خروجی Resend) |
| `services/emoji_resend_manager.py` | 926 | `send_file()` | `_send_album_copy` | 🔁 Premium Pipeline (خروجی Resend) |
| `services/emoji_resend_manager.py` | 719 | `send_file()` | `_send_once` | 🔁 Premium Pipeline (خروجی Resend) |
| `services/emoji_resend_manager.py` | 726 | `delete_messages()` | `_delete_original` | 🔁 Premium Pipeline (خروجی Resend) |
| `services/emoji_resend_manager.py` | 408 | `send_message()` | `_debug_report` | 🧪 مستقیم (گزارش داخلی — engine.bypass) |
| `services/media_sender.py` | 130 | `send_message()` | `_send_location` | ✨ Premium Pipeline (ورودی سلف) |
| `services/media_sender.py` | 146 | `send_message()` | `_send_contact` | ✨ Premium Pipeline (ورودی سلف) |
| `services/media_sender.py` | 184 | `send_message()` | `send_deleted_message` | ✨ Premium Pipeline (ورودی سلف) |
| `services/media_sender.py` | 206 | `send_message()` | `send_deleted_message` | ✨ Premium Pipeline (ورودی سلف) |
| `services/media_sender.py` | 178 | `send_file()` | `_send_cached_file` | ✨ Premium Pipeline (ورودی سلف) |
| `services/media_sender.py` | 191 | `send_message()` | `send_deleted_message` | ✨ Premium Pipeline (ورودی سلف) |
| `services/media_sender.py` | 167 | `send_file()` | `_send_cached_file` | ✨ Premium Pipeline (ورودی سلف) |
| `services/sender.py` | 140 | `send_message()` | `send_banner_to_target` | ✨ Premium Pipeline (ورودی سلف) |
| `services/sender.py` | 134 | `send_message()` | `send_banner_to_target` | ✨ Premium Pipeline (ورودی سلف) |
| `services/state_closer.py` | 85 | `delete_messages()` | `_close_panel_messages` | ✨ Premium Pipeline (ورودی سلف) |
| `services/state_closer.py` | 147 | `delete_messages()` | `close_all_pending` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 807 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 808 | `send_message()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 595 | `reply()` | `handle_emoji_curation` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 598 | `reply()` | `handle_emoji_curation` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 603 | `reply()` | `handle_emoji_curation` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 608 | `reply()` | `handle_emoji_curation` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 612 | `reply()` | `handle_emoji_curation` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 619 | `reply()` | `handle_emoji_curation` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 641 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 654 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 667 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 681 | `send_message()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 716 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 717 | `send_message()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 744 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 755 | `send_message()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 782 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 626 | `reply()` | `handle_emoji_curation` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 651 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 660 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 687 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 692 | `send_message()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 696 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 705 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 742 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 754 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 794 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 446 | `send_message()` | `handle_callback` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 450 | `send_message()` | `handle_callback` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 488 | `send_message()` | `handle_callback` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 670 | `send_message()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 675 | `send_message()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 698 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 740 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 771 | `send_message()` | `sender` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 797 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 737 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 695 | `send_message()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 713 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 734 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin.py` | 803 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin_game.py` | 202 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin_game.py` | 204 | `send_message()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin_game.py` | 199 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin_game.py` | 194 | `send_message()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/admin_game.py` | 191 | `send_message()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/ai.py` | 129 | `reply()` | `_send_ai_answer` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/ai.py` | 142 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/ai.py` | 164 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/ai.py` | 148 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/ai.py` | 161 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/balance.py` | 30 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/crypto.py` | 191 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/crypto.py` | 197 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/crypto.py` | 204 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/crypto.py` | 210 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/crypto.py` | 234 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/crypto.py` | 216 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/crypto.py` | 226 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/crypto.py` | 229 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/game.py` | 51 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/game.py` | 33 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/game.py` | 36 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/game.py` | 162 | `edit_message()` | `run_expiration_scheduler` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/premium_emoji.py` | 107 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/premium_emoji.py` | 114 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/premium_emoji.py` | 118 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/premium_emoji.py` | 128 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/premium_emoji.py` | 132 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/premium_emoji.py` | 143 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/premium_emoji.py` | 147 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/premium_emoji.py` | 152 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/premium_emoji.py` | 157 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/support.py` | 124 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/support.py` | 80 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/support.py` | 112 | `send_message()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/support.py` | 117 | `send_message()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 47 | `send_message()` | `show_menu` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 52 | `send_message()` | `send_menu` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 65 | `send_message()` | `start_create` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 727 | `send_message()` | `_send_draft_targets` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 731 | `send_message()` | `_show_draft_confirmation` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 854 | `send_message()` | `_send_manage` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 858 | `send_message()` | `_send_blacklist` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 866 | `reply()` | `_message_denied` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 57 | `send_message()` | `start_create` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 122 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 190 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 210 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 224 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 242 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 273 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 275 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 294 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 751 | `send_message()` | `_send_preview` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 820 | `edit_message()` | `_edit_source_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 118 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 131 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 142 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 152 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 165 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 175 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 185 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 200 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 220 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 232 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 252 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 280 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 761 | `send_message()` | `_send_preview` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 836 | `edit_message()` | `_prepare_forward_source` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 205 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 286 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 399 | `send_message()` | `handle_callback` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 757 | `send_message()` | `_send_preview` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 759 | `send_message()` | `_send_preview` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 266 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/tabchi.py` | 270 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/transfer.py` | 26 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/transfer.py` | 79 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/transfer.py` | 65 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `handlers/transfer.py` | 68 | `reply()` | `handle_message` | ✨ Premium Pipeline (ورودی سلف) |
| `bot/core.py` | 770 | `send_message()` | `start` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 789 | `send_message()` | `admin_panel` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 608 | `send_message()` | `process_referral` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 618 | `send_message()` | `process_referral` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 712 | `reply()` | `start` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 759 | `send_message()` | `start` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 856 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 961 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 967 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1120 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1193 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1222 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1259 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 861 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 888 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 895 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 949 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 986 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 988 | `send_message()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 999 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1001 | `send_message()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1024 | `send_message()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1037 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1061 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1089 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1105 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1110 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1127 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1136 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1177 | `send_file()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1208 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1234 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1245 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1413 | `send_message()` | `cb_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1948 | `send_message()` | `cb_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 647 | `send_message()` | `maybe_send_alerts` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 659 | `send_message()` | `maybe_send_alerts` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 682 | `send_message()` | `billing_loop` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 892 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 901 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 907 | `send_message()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 979 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 993 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1009 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1017 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1030 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1047 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1070 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1141 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1155 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1214 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1240 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 955 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1013 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1075 | `send_file()` | `sender` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1077 | `send_message()` | `sender` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 1187 | `send_message()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 921 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 934 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `bot/core.py` | 938 | `reply()` | `msg_handler` | 🤖 مستقیم (کلاینت بات — بدون wrap) |
| `self.py` | 152 | `send_message()` | `_temp_message` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 680 | `send_message()` | `away_text_fa_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 741 | `send_message()` | `premium_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 873 | `send_message()` | `premium_status_fa_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 900 | `send_message()` | `crypto_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 916 | `send_message()` | `crypto_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 932 | `send_message()` | `translate_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 1017 | `send_message()` | `custom_emoji_manager_input` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 1071 | `send_message()` | `info_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 1097 | `send_message()` | `ping_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 1247 | `send_message()` | `speech_to_text_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 1443 | `send_message()` | `text_to_speech_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 1684 | `send_message()` | `copy_protected_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 1792 | `send_message()` | `_run_connected_self` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 157 | `send_message()` | `_temp_message` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 547 | `send_message()` | `away_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 567 | `send_message()` | `away_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 620 | `send_message()` | `away_fa_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 635 | `send_message()` | `away_fa_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 669 | `send_message()` | `away_text_fa_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 702 | `send_message()` | `premium_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 771 | `send_message()` | `premium_fa_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 784 | `send_message()` | `premium_fa_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 791 | `send_message()` | `premium_fa_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 798 | `send_message()` | `premium_fa_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 818 | `send_message()` | `premium_debug_fa_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 826 | `send_message()` | `premium_debug_fa_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 859 | `send_message()` | `premium_status_fa_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 918 | `send_message()` | `crypto_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 941 | `send_message()` | `translate_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 944 | `send_message()` | `translate_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 962 | `send_message()` | `ai_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 964 | `send_message()` | `ai_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 988 | `send_message()` | `custom_emoji_manager_input` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 1002 | `send_message()` | `custom_emoji_manager_input` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 1283 | `send_message()` | `speech_to_text_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 1347 | `send_message()` | `tts_voice_choice` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 1354 | `send_file()` | `tts_voice_choice` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 1492 | `send_message()` | `spam_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 249 | `send_message()` | `spam_task` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 287 | `delete_messages()` | `delete_task` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 572 | `send_message()` | `away_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 641 | `send_message()` | `away_fa_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 677 | `send_message()` | `away_text_fa_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 713 | `send_message()` | `premium_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 834 | `send_message()` | `premium_debug_fa_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 840 | `send_message()` | `premium_debug_fa_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 1008 | `send_message()` | `custom_emoji_manager_input` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 1037 | `send_message()` | `custom_emoji_manager_input` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 1039 | `send_message()` | `custom_emoji_manager_input` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 1045 | `send_message()` | `custom_emoji_manager_input` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 1051 | `send_message()` | `custom_emoji_manager_input` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 1362 | `send_file()` | `tts_voice_choice` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 1440 | `delete_messages()` | `text_to_speech_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 489 | `reply()` | `incoming_handler` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 588 | `send_message()` | `away_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 646 | `send_message()` | `away_fa_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 651 | `send_message()` | `away_fa_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 721 | `send_message()` | `premium_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 727 | `send_message()` | `premium_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 1568 | `delete_messages()` | `cancel_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 294 | `delete_messages()` | `delete_task` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 481 | `delete_messages()` | `incoming_handler` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 577 | `send_message()` | `away_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 593 | `send_message()` | `away_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 598 | `send_message()` | `away_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 1295 | `send_message()` | `speech_to_text_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `self.py` | 585 | `send_message()` | `away_cmd` | ✨ Premium Pipeline (ورودی سلف) |
| `login_manager.py` | 141 | `send_message()` | `start_login` | ✨ Premium Pipeline (ورودی سلف) |
| `login_manager.py` | 145 | `send_message()` | `start_login` | ✨ Premium Pipeline (ورودی سلف) |
| `login_manager.py` | 149 | `send_message()` | `start_login` | ✨ Premium Pipeline (ورودی سلف) |
| `login_manager.py` | 181 | `send_message()` | `start_login` | ✨ Premium Pipeline (ورودی سلف) |
| `login_manager.py` | 205 | `send_message()` | `send_code_request` | ✨ Premium Pipeline (ورودی سلف) |
| `login_manager.py` | 212 | `send_message()` | `send_code_request` | ✨ Premium Pipeline (ورودی سلف) |
| `login_manager.py` | 232 | `send_message()` | `send_code_request` | ✨ Premium Pipeline (ورودی سلف) |
| `login_manager.py` | 201 | `send_message()` | `send_code_request` | ✨ Premium Pipeline (ورودی سلف) |
| `login_manager.py` | 251 | `send_message()` | `send_code_request` | ✨ Premium Pipeline (ورودی سلف) |
| `login_manager.py` | 286 | `send_message()` | `handle_code_button` | ✨ Premium Pipeline (ورودی سلف) |
| `login_manager.py` | 525 | `send_message()` | `complete_login` | ✨ Premium Pipeline (ورودی سلف) |
| `login_manager.py` | 569 | `send_message()` | `complete_login` | ✨ Premium Pipeline (ورودی سلف) |
| `login_manager.py` | 584 | `send_message()` | `complete_login` | ✨ Premium Pipeline (ورودی سلف) |

### خلاصه شمارش
- 🚦 Away Bypass: **1** نقطه
- 🔁 Premium Pipeline (خروجی Resend): **4** نقطه
- 🧪 مستقیم (گزارش داخلی — engine.bypass): **1** نقطه
- 🤖 مستقیم (کلاینت بات — بدون wrap): **60** نقطه
- ✨ Premium Pipeline (ورودی سلف): **234** نقطه
- **جمع کل (runtime):** 300 نقطه ارسال

### نکته Audit درباره Edit

- سه نقطه `client.edit_message` در کد runtime وجود دارد: `handlers/game.py:162`
  (زمان‌سنج انقضای بازی) و `handlers/tabchi.py:820,836` (ویرایش پیام منبع تبچی).
  این‌ها **خارج از سیستم ایموجی ویژه**اند، از ریلیزهای قبل وجود دارند و ربطی به
  قاعده «حذف Edit از Premium Resend» ندارند (آن قاعده مربوط به جریان
  ارسال دوباره ایموجی ویژه است).
- کانورتر عمداً `edit_message` را wrap **نمی‌کند**؛ یعنی هیچ پیام ویرایش‌شده‌ای
  وارد Pipeline ایموجی ویژه نمی‌شود.
- ماژول قدیمی `premium_emoji_injector.py` (که `edit_message` را هم wrap می‌کند)
  **فقط در تست‌ها** استفاده می‌شود و در `self.py` نصب **نمی‌شود** — در production
  بی‌اثر است (تأیید با جستجوی install).
- عبارت صحیح و نهایی (اصلاح‌شده طبق دستور مالک): **«هیچ `EditMessageRequest`
  در جریان Premium Resend وجود ندارد»** — یعنی دامنه این ادعا فقط جریان
  ارسال دوباره ایموجی ویژه است، نه کل سیستم. در `tests/test_final_audit_traces.py`
  یک تست runtime این ادعا را به‌صورت زنده تأیید می‌کند
  (`delete=True, new_send=True, edit=False`).

---

## ۴) بررسی نام‌های UI و پنل — هیچ اسم انگلیسی باقی نمانده باشد

اسکن خودکار همه متن‌های قابل‌نمایش (دکمه‌های اینلاین، عنوان پنل‌ها، پیام‌های
راهنما) در `inline.py`، `self.py`، `self_panel_bot.py`، `services/away.py`،
`handlers/premium_emoji.py`، `handlers/admin.py`:

| ناحیه | نتیجه |
|---|---|
| پنل اصلی (`.پنل`) | ✅ تماماً فارسی |
| پنل «💤 پیام عدم حضور» | ✅ (روشن/خاموش کردن، تغییر متن، پاک کردن لیست، بازگشت) |
| پنل «✨ ایموجی ویژه» | ✅ (ارسال دوباره ایموجی ویژه، بررسی ایموجی ویژه، ...) |
| دکمه‌های اینلاین همه پنل‌ها | ✅ صفر برچسب انگلیسی |
| دستورات | ✅ `.عدم_حضور`، `.متن_عدم_حضور`، `.ایموجی_ویژه`، `.بررسی_ایموجی`، `.وضعیت_ایموجی`، `.بستن` (+ نام‌های قدیمی به‌عنوان سازگاری) |
| بلوک‌های لاگ فارسی | ✅ `[پیام عدم حضور]`، `[ارسال دوباره]`، `[بررسی ایموجی ویژه]`، `[مدیریت وضعیت]` |

موارد لاتینِ باقی‌مانده و «مجاز» (طبق خود spec مالک):

1. **نام فیلدهای Trace** (`chat_id`, `trigger`, `bypass_active`,
   `premium_pipeline_entered`, `resend_entered`, `final_sender`,
   `message_id`, `is_away`, `entity_check`, `delete_called`,
   `new_send_called`) — عیناً همان spec این Audit است.
2. **`Online Trigger:`** در بلوک `[مدیریت وضعیت]` — عیناً spec قبلی مالک بود.
3. **شناسه‌های داخلی** (callback data مثل `away_toggle`، کلیدهای config مثل
   `PREMIUM_EMOJI_RESEND_MODE`، متغیرهای کد) — UI نیستند و کاربر نمی‌بیند.

---

## ۵) یافته‌های Audit و اصلاحات انجام‌شده (فقط اتصال/ثبت — بدون قابلیت جدید)

| # | یافته | اقدام |
|---|---|---|
| ۱ | نسخه داخل `storage.py` (`APP_VERSION`) از v0.09.13 به‌روز نشده بود | ✅ به `0.09.15` اصلاح شد |
| ۲ | نبود اثبات قابل‌حمل برای «Away هرگز وارد Premium نمی‌شود» | ✅ `[AWAY_TRACE]` با تأیید سه‌نقطه‌ای (ارسال + گارد کانورتر + رد شدن Resend) |
| ۳ | نبود ردیابی تصمیم‌های Premium Resend | ✅ `[PREMIUM_TRACE]` در همه نقاط تصمیم + مسیر Away-skip غیرمشروط |
| ۴ | UI: هیچ اسم انگلیسی باقی‌مانده نبود | ✅ بدون تغییر (تأیید شده) |
| ۵ | گارد کانورتر/اینجکتور قبلاً `or`-ترکیبی بود | ✅ گارد Away جدا و اولویت‌دار شد؛ رفتار ارسال تغییری نکرد (فقط ثبت برخورد اضافه شد) |

هیچ رفتار ارسالی تغییر نکرد: همه تغییرات «ثبت/ردیابی» هستند (`note_*` و
`_emit_trace` داخل try/except با مهار کامل؛ هرگز مسیر ارسال را نمی‌شکنند).

---

## ۶) تست‌ها

فایل: `tests/test_final_audit_traces.py` (۱۰ تست، همه پاس):

1. فرمت دقیق `[AWAY_TRACE]` — ۶ فیلد spec + خط خالی + مقادیر فارسی + حالت ناموفق.
2. فرمت دقیق `[PREMIUM_TRACE]` — ۵ فیلد spec + خط خالی + هر دو حالت is_away.
3. صدور `[AWAY_TRACE]` واقعی برای ارسال Away با تأیید گارد کانورتر.
4. به‌روزرسانی Trace بعد از رویداد outgoing (یادداشت «دیدم و رد کردم»).
5. `[PREMIUM_TRACE] (is_away: بله)` غیرمشروط — حتی با Resend خاموش.
6. `[PREMIUM_TRACE]` جریان کامل Resend: entity_check انجام شد، Delete بله، New Send بله.
7. `[PREMIUM_TRACE]` Entity سالم سرور: هر دو اقدام خیر.
8. ماتریس واقعی Chat A/B با Trace: A اول → یک Trace | A دوم → Trace جدید نیست | B اول → Trace مستقل.
9. استقلال کامل: حذف پریمیوم → AWAY_TRACE همچنان صادر می‌شود.
10. **تست runtime نهایی (دستور مالک):** Premium: ارسال پیام → `delete=True`,
    `new_send=True`, `edit=False` (با ثبت‌کننده `client.edit_message` + بررسی
    `EditMessageRequest` در سطح شبکه) | Away: ارسال پیام عدم حضور →
    `premium_entered=False`, `resend_entered=False` (در بلوک Trace و در record
    runtime) + رد شدن پیام Away از Resend بدون هیچ اقدامی.

نتیجه کل مجموعه: **۸۶۹ پاس / ۲۳ شکست** — دقیقاً برابر baseline قبل از Audit
(۸۵۹ پاس + ۱۰ تست جدید؛ همان ۲۳ شکست قدیمیِ نامرتبط: transfer/flags/translation/
storage/streaming) → **صفر رگرسیون**.

---

## ۷) نتیجه نهایی Audit

- ✅ **Away و Premium Emoji کاملاً مستقل‌اند** — با اثبات سه‌نقطه‌ای در
  `[AWAY_TRACE]` و اثبات از سمت Premium در `[PREMIUM_TRACE] (is_away: بله)`.
- ✅ **همه ۳۰۰ مسیر ارسال** شناسایی و طبقه‌بندی شدند (Away Bypass: فقط ۱ نقطه).
- ✅ **UI و پنل**: هیچ اسم انگلیسی باقی‌مانده نیست.
- ✅ **هیچ قابلیت جدیدی اضافه نشد** — فقط ثبت واقعی اتصال سیستم‌ها.
