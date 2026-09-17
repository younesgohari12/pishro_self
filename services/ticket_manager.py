from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from database import models
from services import trial_manager


def detect_message_type(message) -> str:
    checks = (
        ('photo', 'photo'), ('video', 'video'), ('voice', 'voice'), ('audio', 'audio'),
        ('document', 'file'), ('gif', 'gif'), ('sticker', 'sticker'),
        ('geo', 'location'), ('contact', 'contact'),
    )
    for attr, label in checks:
        try:
            if getattr(message, attr, None):
                return label
        except Exception:
            pass
    return 'text' if (getattr(message, 'raw_text', '') or getattr(message, 'text', '')) else 'other'


def _trial_info(user_id: int) -> tuple[str, str]:
    row = trial_manager.refresh(user_id)
    if not row:
        return 'استفاده نشده', '-'
    if int(row.get('active') or 0):
        return 'فعال', row.get('expire_time') or '-'
    if int(row.get('used') or 0):
        return 'استفاده شده / غیرفعال', row.get('expire_time') or '-'
    return 'استفاده نشده', '-'


def user_info_text(user_id: int) -> str:
    p = models.get_profile(user_id) or {}
    ticket_count = models.ticket_count_for_user(user_id)
    username = p.get('username') or ''
    trial_status, trial_expiry = _trial_info(user_id)
    return (
        f"👤 نام: {(p.get('first_name') or '-') } {(p.get('last_name') or '')}".rstrip() + "\n"
        f"Username: @{username if username else 'ندارد'}\n"
        f"ID: {int(user_id)}\n"
        f"تاریخ عضویت: {p.get('registered_at') or '-'}\n"
        f"وضعیت Trial: {trial_status}\n"
        f"زمان انقضا: {trial_expiry}\n"
        f"آخرین فعالیت: {p.get('last_activity') or '-'}\n"
        f"تعداد تیکت‌ها: {ticket_count}"
    )


def ticket_header(ticket_id: int, user_id: int) -> str:
    return f"🎫 **تیکت جدید #{ticket_id}**\n\n{user_info_text(user_id)}\n\n━━━━━━━━━━━━━━"


def admin_reply_header(ticket_id: int) -> str:
    return f"🎧 **پاسخ پشتیبانی — تیکت #{ticket_id}**\n\n"
