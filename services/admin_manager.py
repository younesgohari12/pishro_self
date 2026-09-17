from __future__ import annotations

from typing import Iterable

from config import ADMIN_ID
from database import models

ALL_PERMISSIONS = tuple(models.ADMIN_PERMISSION_KEYS)
PERMISSION_LABELS = {
    'view_users': '👥 مشاهده کاربران',
    'manage_users': '🛠 مدیریت کاربران',
    'answer_tickets': '✅ پاسخ تیکت',
    'manage_tickets': '🎫 مدیریت تیکت‌ها',
    'manage_support': '🎧 مدیریت پشتیبانی',
    'manage_trial': '🎁 مدیریت تست رایگان',
    'manage_admins': '🔐 مدیریت ادمین‌ها',
    'system_settings': '⚙️ تغییر تنظیمات سیستم',
    'broadcast': '📣 ارسال پیام همگانی',
    'view_stats': '📊 مشاهده آمار',
    'manage_games': '🎮 مدیریت بازی',
}


def is_owner(user_id: int) -> bool:
    return int(user_id) == int(ADMIN_ID)


def note_identity(user_id: int, username: str | None) -> None:
    """Numeric identity is authoritative; usernames cannot claim admin rows."""
    return None


def get_admin(user_id: int, username: str | None = None):
    if is_owner(user_id):
        return {
            'id': 0,
            'user_id': int(user_id),
            'username': username or '',
            'role': 'owner',
            'permissions_list': list(ALL_PERMISSIONS),
        }
    return models.get_admin_by_user(user_id, username)


def is_admin(user_id: int, username: str | None = None) -> bool:
    return get_admin(user_id, username) is not None


def permissions_for(user_id: int, username: str | None = None) -> set[str]:
    row = get_admin(user_id, username)
    if not row:
        return set()
    if row.get('role') in {'owner', 'full'}:
        return set(ALL_PERMISSIONS)
    return {p for p in (row.get('permissions_list') or []) if p in ALL_PERMISSIONS}


def has_permission(user_id: int, permission: str, username: str | None = None) -> bool:
    return permission in permissions_for(user_id, username)


def can_any(user_id: int, permissions: Iterable[str], username: str | None = None) -> bool:
    mine = permissions_for(user_id, username)
    return any(p in mine for p in permissions)


def ticket_recipient_ids() -> list[int]:
    ids = {int(ADMIN_ID)}
    for row in models.list_admins():
        uid = row.get('user_id')
        if uid is None:
            continue
        perms = set(row.get('permissions_list') or [])
        if row.get('role') == 'full' or 'answer_tickets' in perms or 'manage_tickets' in perms:
            ids.add(int(uid))
    return sorted(ids)
