from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from database import models
from config import TRIAL_DURATION_HOURS



def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def refresh(user_id: int) -> dict[str, Any] | None:
    row = models.get_trial(user_id)
    if not row:
        return None
    if int(row.get('active') or 0):
        expires = _parse(row.get('expire_time'))
        if expires is None or expires <= _utcnow():
            models.set_trial_active(user_id, False)
            row = models.get_trial(user_id)
    return row


def has_used(user_id: int) -> bool:
    row = models.get_trial(user_id)
    return bool(row and int(row.get('used') or 0))


def is_active(user_id: int) -> bool:
    row = refresh(user_id)
    return bool(row and int(row.get('active') or 0))


def remaining_seconds(user_id: int) -> int:
    row = refresh(user_id)
    if not row or not int(row.get('active') or 0):
        return 0
    expires = _parse(row.get('expire_time'))
    if not expires:
        return 0
    return max(0, int((expires - _utcnow()).total_seconds()))


def activate_after_login(user_id: int, *, username: str = '', first_name: str = '',
                         last_name: str = '', phone: str = '') -> tuple[bool, dict[str, Any] | None]:
    if has_used(user_id):
        return False, models.get_trial(user_id)
    from services.usage_service import reconcile_user, sync_trial
    reconcile_user(user_id)
    expires = _utcnow() + timedelta(hours=TRIAL_DURATION_HOURS)
    ok = models.activate_trial(
        user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        expire_time=expires.isoformat(timespec='seconds'),
    )
    if ok:
        sync_trial(user_id)
        models.touch_profile(
            user_id, username=username, first_name=first_name,
            last_name=last_name, phone=phone,
        )
    return ok, models.get_trial(user_id)


def revoke(user_id: int) -> bool:
    # Revoking never resets `used`; the one-time trial rule remains enforced.
    from services.usage_service import reconcile_user, sync_trial
    reconcile_user(user_id)
    result = models.set_trial_active(user_id, False)
    sync_trial(user_id)
    return result


def grant_admin_trial(user_id: int, hours: int | None = None) -> bool:
    """Admin grant/extension. Does not erase historical `used` state."""
    from services.usage_service import reconcile_user, sync_trial
    reconcile_user(user_id)
    row = models.get_trial(user_id)
    now = _utcnow()
    expires = now + timedelta(hours=max(1, int(TRIAL_DURATION_HOURS if hours is None else hours)))
    if row is None:
        models.ensure_trial_row(user_id)
    with models._lock, models._conn() as conn:  # internal transaction, same module DB
        cur = conn.execute(
            '''UPDATE free_trial SET used=1,active=1,expire_time=?,activated_at=COALESCE(activated_at,?),updated_at=?
               WHERE user_id=?''',
            (expires.isoformat(timespec='seconds'), now.isoformat(timespec='seconds'),
             now.isoformat(timespec='seconds'), int(user_id))
        )
        conn.commit()
        result = cur.rowcount == 1
    sync_trial(user_id)
    return result


def expire_due_trials() -> list[int]:
    expired: list[int] = []
    for row in models.list_active_trials(limit=500):
        expires = _parse(row.get('expire_time'))
        if expires is None or expires <= _utcnow():
            uid = int(row['user_id'])
            if models.set_trial_active(uid, False):
                expired.append(uid)
    return expired
