"""Compatibility shim — منطق اصلی به services/premium_resend_service.py منتقل شد (v0.09.18).

ماژول رسمی «ارسال دوباره ایموجی ویژه» طبق spec مالک اکنون
``services/premium_resend_service.py`` است؛ ماژولی که:
    - تشخیص جامع ایموجی (متن/raw_text/entities/کپشن/attributes مدیا)
    - کپی کامل محتوا → ارسال نسخه جدید → بعد از موفقیتِ ارسال، حذف اصل
    - هیچ EditMessageRequest و هیچ edit_message ای در کل جریان نیست
    - ناظر incoming (فقط مشاهده) + ضد loop با TTL + کنترل flood

این فایل فقط برای سازگاری با import های قدیمی (self.py، tests، tools)
نگه داشته شده و همه نمادها را از ماژول رسمی بازنشر می‌کند.
"""
from __future__ import annotations

from services.premium_resend_service import (  # noqa: F401
    ALBUM_FLUSH_DELAY_SECONDS,
    CUSTOM_ENTITY_NAME,
    IGNORED_LIMIT,
    MAX_ALBUM_PARTS,
    PROCESSED_TTL_SECONDS,
    RECENT_SENT_LIMIT,
    SEND_ATTEMPTS,
    SEND_RETRY_DELAY_SECONDS,
    STRIP_COOLDOWN_SECONDS,
    VERIFY_DELAY_SECONDS,
    EmojiResendManager,
    PremiumResendService,
    _custom_emoji_in_media,
    _describe_entities,
    _detect_emoji,
    _has_custom_entity,
    _is_custom,
    _media_type,
    install_emoji_resend_manager,
    install_premium_resend_service,
    uninstall_emoji_resend_manager,
    uninstall_premium_resend_service,
)

# نام‌های قدیمی برای سازگاری کامل
PremiumResendManager = EmojiResendManager
install_premium_resend = install_emoji_resend_manager
uninstall_premium_resend = uninstall_emoji_resend_manager

__all__ = [
    'EmojiResendManager', 'PremiumResendManager', 'PremiumResendService',
    'install_emoji_resend_manager', 'uninstall_emoji_resend_manager',
    'install_premium_resend_service', 'uninstall_premium_resend_service',
    'install_premium_resend', 'uninstall_premium_resend',
    'ALBUM_FLUSH_DELAY_SECONDS', 'MAX_ALBUM_PARTS', 'RECENT_SENT_LIMIT',
    'PROCESSED_TTL_SECONDS', 'IGNORED_LIMIT', 'STRIP_COOLDOWN_SECONDS',
    'VERIFY_DELAY_SECONDS', 'SEND_ATTEMPTS', 'SEND_RETRY_DELAY_SECONDS',
    'CUSTOM_ENTITY_NAME',
    '_has_custom_entity', '_is_custom', '_media_type',
    '_custom_emoji_in_media', '_detect_emoji',
]
