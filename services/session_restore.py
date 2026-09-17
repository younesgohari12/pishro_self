"""Recover account metadata only after Telegram verifies a saved session.

The session file is the connection credential; JSON/SQLite are application
metadata. Recovery never grants credit, renews trials or enables disabled users.
"""
import re

import db
from database import models
from services import balance_service


def session_user_id(name):
    if not isinstance(name, str) or not re.fullmatch(r'user_[1-9][0-9]*', name):
        return None
    return int(name[5:])


def restore_account(session_name, me):
    """Idempotently fill missing records, preserving authoritative balances."""
    uid = session_user_id(session_name)
    if uid is None or int(getattr(me, 'id', 0)) != uid or getattr(me, 'bot', False):
        raise ValueError('Saved session identity does not match its filename')

    with db._lock:
        settings = db.get_user_settings(uid)
        balance_service.ensure_user(
            uid,
            username=getattr(me, 'username', '') or '',
            first_name=getattr(me, 'first_name', '') or '',
            initial_balance=int(settings.get('diamonds') or 0),
        )
        # touch_user leaves feature settings, self_enabled and usage unchanged.
        db.touch_user(uid, username=getattr(me, 'username', '') or '',
                      first_name=getattr(me, 'first_name', '') or '')
        models.touch_profile(
            uid,
            username=getattr(me, 'username', '') or '',
            first_name=getattr(me, 'first_name', '') or '',
            last_name=getattr(me, 'last_name', '') or '',
            registered_at=db.get_user_settings(uid).get('registered_at') or None,
        )
    return uid
