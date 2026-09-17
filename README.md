# PishroSelf — v0.09.13 PREMIUM EMOJI STRICT FINAL

سلف‌بات حرفه‌ای تلگرام با معماری Self/Bot جداشده.

## 🎨 Premium Emoji Converter (Strict Mapping V2)
- هر ایموجی یونیکد فقط به Custom Emoji با `alt` دقیقاً برابر تبدیل می‌شود (تطبیق معنایی اجباری)
- منبع شناسه‌ها: فقط `messages.getCustomEmojiDocuments` و پک‌های عمومی `t.me/CustomEmojiPack`
- ۶۳۸ شناسه پروژه به‌صورت زنده resolve شده — ۴۳۳ شناسه تأییدشده، ۳۰ ایموجی فعال، 🚀 غیرفعال
- fallback عمومی ممنوع؛ `FALLBACK_DOCUMENT_ID` فقط برای Premium Prefix مستقل
- جزئیات کامل: `PREMIUM_EMOJI_STRICT_REPORT.md` و `PREMIUM_EMOJI_RESOLVED_MAPPING.json`

## 📁 ساختار
- `services/premium_emoji_converter.py` — موتور تبدیل (فقط Self؛ Bot هرگز)
- `premium_emoji_mapping.py` — نگاشت Strict تأییدشده
- `tools/resolve_premium_emoji_mapping.py` — ابزار رسمی resolve زنده
- `tools/premium_emoji_strict_smoke.py` — اسموک تست زنده
- `tests/` — بیش از ۷۶۰ تست خودکار

## ⚙️ نصب
مقادیر واقعی `API_ID/API_HASH/BOT_TOKEN` فقط در `PishroSelfData/config.py` نگه داشته می‌شوند و داخل این ریپو/ZIP وجود ندارند.
