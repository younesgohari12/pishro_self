"""Authoritative integer wallet backed by SQLite and an append-only ledger."""
from __future__ import annotations

from typing import Any

from database import models
from services.transaction_service import begin_immediate, connect, ledger_entry, row_dict


def _now() -> str:
    return models._now_iso()


def ensure_user(
    telegram_id: int,
    *,
    username: str | None = None,
    first_name: str | None = None,
    initial_balance: int = 0,
) -> dict[str, Any]:
    uid = int(telegram_id)
    starting = max(0, int(initial_balance))
    now = _now()
    with models._lock, connect() as conn:
        begin_immediate(conn)
        conn.execute(
            '''INSERT OR IGNORE INTO users
               (telegram_id,username,first_name,diamonds,created_at,updated_at)
               VALUES (?,?,?,?,?,?)''',
            (uid, username or '', first_name or '', starting, now, now)
        )
        row = conn.execute('SELECT * FROM users WHERE telegram_id=?', (uid,)).fetchone()
        if row is None:
            raise RuntimeError('wallet user could not be created')
        current = dict(row)
        new_username = current.get('username', '') if username is None else (username or '')
        new_first_name = current.get('first_name', '') if first_name is None else (first_name or '')
        if new_username != current.get('username') or new_first_name != current.get('first_name'):
            conn.execute(
                'UPDATE users SET username=?,first_name=?,updated_at=? WHERE telegram_id=?',
                (new_username, new_first_name, now, uid)
            )
        conn.commit()
        return dict(conn.execute('SELECT * FROM users WHERE telegram_id=?', (uid,)).fetchone())


def get_user(telegram_id: int) -> dict[str, Any] | None:
    models.init_game_db()
    with models._lock, connect() as conn:
        begin_immediate(conn)
        conn.commit()
        return row_dict(conn.execute(
            'SELECT * FROM users WHERE telegram_id=?', (int(telegram_id),)
        ).fetchone())


def get_balance_if_exists(telegram_id: int) -> int | None:
    user = get_user(telegram_id)
    return int(user['diamonds']) if user is not None else None


def get_balance(telegram_id: int) -> int:
    user = get_user(telegram_id)
    return int(user['diamonds']) if user else 0


def set_balance_from_core(
    telegram_id: int,
    new_balance: int,
    *,
    username: str | None = None,
    first_name: str | None = None,
    description: str = 'Core wallet balance update',
) -> int:
    """Compatibility gateway for legacy wallet screens; every delta is logged."""
    uid = int(telegram_id)
    target = int(new_balance)
    if target < 0:
        raise ValueError('negative diamond balance is forbidden')
    # Runtime adjustments start from zero for a new wallet so the first change
    # is also present in the ledger. Legacy imports use ensure_user directly.
    ensure_user(uid, username=username, first_name=first_name, initial_balance=0)
    now = _now()
    with models._lock, connect() as conn:
        begin_immediate(conn)
        row = conn.execute('SELECT diamonds FROM users WHERE telegram_id=?', (uid,)).fetchone()
        before = int(row['diamonds'])
        if before == target:
            conn.commit()
            return target
        tx_type = 'ADMIN_ADD' if target > before else 'ADMIN_REMOVE'
        conn.execute(
            'UPDATE users SET diamonds=?,updated_at=? WHERE telegram_id=?',
            (target, now, uid)
        )
        ledger_entry(
            conn, user_id=uid, tx_type=tx_type, amount=target - before,
            before=before, after=target, game_id=None,
            description=description, created_at=now,
        )
        conn.commit()
    return target


def admin_adjust(user_id: int, amount: int, *, description: str = '', floor_zero: bool = False) -> int:
    delta = int(amount)
    ensure_user(int(user_id))
    now = _now()
    with models._lock, connect() as conn:
        begin_immediate(conn)
        row = conn.execute('SELECT diamonds FROM users WHERE telegram_id=?', (int(user_id),)).fetchone()
        before = int(row['diamonds'])
        after = before + delta
        if floor_zero and after < 0:
            after = 0
        actual_delta = after - before
        if after < 0:
            raise ValueError('insufficient balance')
        if actual_delta:
            conn.execute(
                'UPDATE users SET diamonds=?,updated_at=? WHERE telegram_id=?',
                (after, now, int(user_id))
            )
            ledger_entry(
                conn, user_id=int(user_id),
                tx_type='ADMIN_ADD' if actual_delta > 0 else 'ADMIN_REMOVE',
                amount=actual_delta, before=before, after=after, game_id=None,
                description=description or 'Admin wallet adjustment', created_at=now,
            )
        conn.commit()
        return after


def migrate_legacy_wallets() -> int:
    """Import JSON balances once; existing SQLite balances always win."""
    import db

    count = 0
    for uid in db.iter_user_ids():
        legacy = db._get_internal(uid)
        before = get_user(uid)
        ensure_user(
            uid,
            username=legacy.get('username') or '',
            first_name=legacy.get('first_name') or '',
            initial_balance=int(legacy.get('diamonds') or 0),
        )
        if before is None:
            count += 1
    return count
