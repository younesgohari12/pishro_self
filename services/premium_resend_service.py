# -*- coding: utf-8 -*-
"""سرویس مستقل «ارسال دوباره ایموجی ویژه» — نقطه ورود رسمی سرویس (v0.09.17).

نام رسمی سرویس طبق spec مالک: ``services/premium_resend_service.py``

منطق اصلی و «تک‌منبع حقیقت» در ``services/emoji_resend_manager.py``
پیاده‌سازی شده است (از v0.09.13 تاکنون، تست‌شده با ۳۳+ تست) و این ماژول
نقطه ورود سرویس است؛ هیچ منطق تکراری‌ای ندارد تا منطق Premium Emoji موجود
دست‌نخورده بماند.

مشخصات سرویس (رعایت‌شده در موتور اصلی):
    - فعال‌سازی دوکلیدی: config.PREMIUM_EMOJI_ENABLED + PREMIUM_EMOJI_RESEND_MODE
      (+ انتخاب صریح پنل حساب؛ هر سه از طریق resend_enabled() زنجیر می‌شوند)
    - پوشش همه مقصدها: Saved، چت خصوصی، گروه، سوپرگروه، کانال — هندلر
      outgoing کانورتر (events.NewMessage(outgoing=True)) همه چت‌ها را
      می‌گیرد و به handle_outgoing() این سرویس می‌دهد.
    - شناسایی Emoji: الگوی EMOJI_PATTERN + تشخیص MessageEntityCustomEmoji
      (ایموجی‌های Premium/Custom تلگرام) بر اساس «نمای واقعی سرور»
      (client.get_messages) نه شیء محلی.
    - ترتیب الزامی: کپی کامل محتوا (متن/مدیا/کپشن/entity/reply/silent)
      → حذف پیام اصلی → ارسال نسخه جدید با entity. هیچ EditMessageRequest
      ای در هیچ مرحله‌ای وجود ندارد.
    - مدیا پشتیبانی‌شده: متن، عکس، ویدیو، گیف/انیمیشن، فایل، صدا، ویس،
      استیکر، video_note و آلبوم (media group یک‌جا حذف/ارسال می‌شود).
    - قواعد امنیتی:
          • بدون ایموجی قابل‌نگاشت → هیچ کاری انجام نمی‌شود.
          • حذف ناموفق → نسخه جدید هرگز ارسال نمی‌شود (پیام اصلی می‌ماند).
          • ارسال ناموفق → ۳ تلاش + ارسال نجات‌دهنده بدون entity؛ محتوا
            در گزارش خطا حفظ می‌شود.
          • جلوگیری از loop: حافظه (chat_id, message_id) پیام‌های خود
            سرویس + نادیده‌گرفتن گزارش‌های خود + cooldown کانورتر.
          • شناسه پردازش هر پیام در بلوک [بررسی ایموجی ویژه] ثبت و در
            حافظه loop-guard نگه‌داری می‌شود.
          • کنترل flood/rate: SEND_ATTEMPTS + SEND_RETRY_DELAY_SECONDS +
            STRIP_COOLDOWN_SECONDS + قفل per-chat + یک تلاش برای هر پیام.
"""
from __future__ import annotations

from services.emoji_resend_manager import (  # noqa: F401
    ALBUM_FLUSH_DELAY_SECONDS,
    CUSTOM_ENTITY_NAME,
    IGNORED_LIMIT,
    MAX_ALBUM_PARTS,
    RECENT_SENT_LIMIT,
    SEND_ATTEMPTS,
    SEND_RETRY_DELAY_SECONDS,
    STRIP_COOLDOWN_SECONDS,
    VERIFY_DELAY_SECONDS,
    EmojiResendManager,
    install_emoji_resend_manager,
    uninstall_emoji_resend_manager,
)

# نام‌های رسمی سرویس
PremiumResendService = EmojiResendManager
install_premium_resend_service = install_emoji_resend_manager
uninstall_premium_resend_service = uninstall_emoji_resend_manager


def get_service(client):
    """نمونه نصب‌شده سرویس روی کلاینت Self را برمی‌گرداند (یا None)."""
    return getattr(client, '_premium_resend_manager', None)


__all__ = [
    'PremiumResendService', 'EmojiResendManager',
    'install_premium_resend_service', 'uninstall_premium_resend_service',
    'install_emoji_resend_manager', 'uninstall_emoji_resend_manager',
    'get_service',
    'ALBUM_FLUSH_DELAY_SECONDS', 'MAX_ALBUM_PARTS', 'RECENT_SENT_LIMIT',
    'IGNORED_LIMIT', 'STRIP_COOLDOWN_SECONDS', 'VERIFY_DELAY_SECONDS',
    'SEND_ATTEMPTS', 'SEND_RETRY_DELAY_SECONDS', 'CUSTOM_ENTITY_NAME',
]
