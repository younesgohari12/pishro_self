"""Compatibility shim — منطق اصلی به services/emoji_resend_manager.py منتقل شد.

ماژول رسمی «ارسال دوباره ایموجی ویژه» اکنون
``services/emoji_resend_manager.py`` است؛ ماژولی که طبق spec مالک:
    - بررسی Entity واقعی از نمای سرور (client.get_messages)
    - کپی محتوا → حذف پیام اصلی → ارسال پیام جدید با Custom Emoji Entity
    - هیچ EditMessageRequest و هیچ edit_message ای در کل جریان نیست

این فایل فقط برای سازگاری با import های قدیمی (self.py، tests، tools)
نگه داشته شده و همه نمادها را از ماژول جدید بازنشر می‌کند.
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
    _has_custom_entity,
    _is_custom,
    _media_type,
    install_emoji_resend_manager,
    uninstall_emoji_resend_manager,
)

# نام‌های قدیمی برای سازگاری کامل
PremiumResendManager = EmojiResendManager
install_premium_resend = install_emoji_resend_manager
uninstall_premium_resend = uninstall_emoji_resend_manager

__all__ = [
    'EmojiResendManager', 'PremiumResendManager',
    'install_emoji_resend_manager', 'uninstall_emoji_resend_manager',
    'install_premium_resend', 'uninstall_premium_resend',
    'ALBUM_FLUSH_DELAY_SECONDS', 'MAX_ALBUM_PARTS', 'RECENT_SENT_LIMIT',
    'IGNORED_LIMIT', 'STRIP_COOLDOWN_SECONDS', 'VERIFY_DELAY_SECONDS',
    'SEND_ATTEMPTS', 'SEND_RETRY_DELAY_SECONDS', 'CUSTOM_ENTITY_NAME',
    '_has_custom_entity', '_is_custom', '_media_type',
]
