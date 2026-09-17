"""Durable half-hour billing. All clock and wallet changes share one transaction.

Only verified sessions are enrolled. Server/process downtime is chargeable;
manual pauses are not. No historical downtime is invented on first enrollment.
"""
from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime, timezone

import db
from database import models
from services.transaction_service import connect, ledger_entry

INTERVAL = 1800
CHARGE = 1


def _time(value=None):
    value = time.time() if value is None else value
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
        raise ValueError('time must be finite and nonnegative')
    return float(value)


def _carry(conn, uid, fallback=0):
    row = conn.execute('SELECT seconds FROM wallet_usage WHERE user_id=?', (uid,)).fetchone()
    value = row['seconds'] if row else fallback
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
        return 0.0
    return float(value) % 3600  # legacy hourly remainder, consumed once at enrollment


def _save(conn, uid, seconds, checkpoint, enabled, free_until, balance):
    due = max(checkpoint, free_until) + INTERVAL - seconds if enabled and balance > 0 else None
    conn.execute('INSERT INTO wallet_usage(user_id,seconds) VALUES (?,?) '
                 'ON CONFLICT(user_id) DO UPDATE SET seconds=excluded.seconds', (uid, seconds))
    conn.execute('UPDATE billing_clocks SET checkpoint=?,enabled=?,free_until=?,next_due=? WHERE user_id=?',
                 (checkpoint, int(enabled), free_until, due, uid))


def _settle(conn, clock, now):
    uid = clock['user_id']
    row = conn.execute('SELECT diamonds FROM users WHERE telegram_id=?', (uid,)).fetchone()
    before = int(row['diamonds'])
    now = max(now, clock['checkpoint'])  # backwards wall-clock changes cannot replay time
    seconds = _carry(conn, uid)
    if clock['enabled'] and before > 0:
        seconds += max(0, now - max(clock['checkpoint'], clock['free_until']))
        periods = int(seconds // INTERVAL)
        cost = min(before, periods * CHARGE)
        seconds %= INTERVAL
    else:
        cost = 0
    after = before - cost
    if after == 0 and cost:
        seconds = 0.0  # service stops at zero; no debt accumulates against a future top-up
    if cost:
        stamp = datetime.fromtimestamp(now, timezone.utc).isoformat(timespec='seconds')
        conn.execute('UPDATE users SET diamonds=?,updated_at=? WHERE telegram_id=?', (after, stamp, uid))
        ledger_entry(conn, user_id=uid, tx_type='ADMIN_REMOVE', amount=-cost, before=before, after=after,
                     game_id=None, description=f'HALF_HOUR_USAGE:{cost}:through={now:.6f}', created_at=stamp)
    _save(conn, uid, seconds, now, clock['enabled'], clock['free_until'], after)
    return {'user_id': uid, 'deducted': cost, 'balance': after, 'seconds': seconds,
            'enabled': bool(clock['enabled'] and (after > 0 or clock['free_until'] > now))}


def settle_due_in_transaction(conn, now=None):
    """Called before wallet mutations, and by the independent scheduler; no commits."""
    now = _time(now)
    clocks = conn.execute('SELECT * FROM billing_clocks WHERE next_due<=? ORDER BY next_due,user_id', (now,)).fetchall()
    return [_settle(conn, clock, now) for clock in clocks]


def balance_changed(conn, uid, before, after):
    """Keep zero/top-up transitions exact, including transfers, games and payments."""
    if (before > 0) == (after > 0):
        return
    clock = conn.execute('SELECT * FROM billing_clocks WHERE user_id=?', (uid,)).fetchone()
    if clock is not None:
        now = max(_time(), clock['checkpoint'])
        seconds = _carry(conn, uid)
        if before > 0 and clock['enabled']:
            seconds += max(0, now - max(clock['checkpoint'], clock['free_until']))
        # A wallet transaction already settled all completed periods on BEGIN.
        # Preserve its partial period through zero/top-up transitions.
        seconds = min(seconds, INTERVAL - 0.000001)
        _save(conn, uid, seconds, now, clock['enabled'], clock['free_until'], after)


def reconcile_all(now=None):
    now = _time(now)
    with models._lock, connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        result = settle_due_in_transaction(conn, now)
        conn.commit()
        return result


def reconcile_user(uid, now=None):
    now = _time(now)
    with models._lock, connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        clock = conn.execute('SELECT * FROM billing_clocks WHERE user_id=?', (int(uid),)).fetchone()
        result = _settle(conn, clock, now) if clock else None
        conn.commit()
        return result


def configure(uid, *, enabled=None, free_until=None, enroll=False, now=None):
    """Settle the old state before changing a verified account's billing state."""
    uid, now = int(uid), _time(now)
    with db._lock, models._lock, connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        clock = conn.execute('SELECT * FROM billing_clocks WHERE user_id=?', (uid,)).fetchone()
        if clock is None:
            if not enroll:
                return
            settings = db._get_internal(uid)
            if conn.execute('SELECT 1 FROM users WHERE telegram_id=?', (uid,)).fetchone() is None:
                raise ValueError('cannot enroll a missing wallet')
            carry = _carry(conn, uid, settings.get('bill_seconds', 0))
            active = bool(settings.get('self_enabled', False) if enabled is None else enabled)
            free = max(0.0, float(free_until or 0))
            if free > now:
                carry = 0.0
            conn.execute('INSERT INTO billing_clocks(user_id,checkpoint,enabled,free_until) VALUES (?,?,?,?)',
                         (uid, now, int(active), free))
            conn.execute('INSERT INTO wallet_usage(user_id,seconds) VALUES (?,?) '
                         'ON CONFLICT(user_id) DO UPDATE SET seconds=excluded.seconds', (uid, carry))
            clock = conn.execute('SELECT * FROM billing_clocks WHERE user_id=?', (uid,)).fetchone()
        result = _settle(conn, clock, now)
        checkpoint = max(now, clock['checkpoint'])
        active = clock['enabled'] if enabled is None else bool(enabled)
        free = clock['free_until'] if free_until is None else _time(free_until)
        carry = result['seconds']
        if free_until is not None and free > checkpoint:
            carry = 0.0
        _save(conn, uid, carry, checkpoint, active, free, result['balance'])
        conn.commit()
        return result


def sync_trial(uid, *, enroll=False, now=None):
    from services.trial_manager import _parse
    row = models.get_trial(uid)
    expires = _parse(row.get('expire_time')) if row and row.get('active') else None
    return configure(uid, free_until=expires.timestamp() if expires else 0, enroll=enroll, now=now)


def enroll_verified(uid):
    """Called only after get_me/filename identity verification, never from a bot /start."""
    sync_trial(uid, enroll=True)
    configure(uid, enabled=db._get_internal(uid).get('self_enabled', False))


async def run_billing_loop():
    # Independent of network connections and notifications. SQLite is authoritative.
    while True:
        reconcile_all()
        await asyncio.sleep(1)


def accrue(user_id, elapsed):
    """Legacy explicit-runtime API; production uses durable clocks exclusively."""
    elapsed = _time(elapsed)
    uid = int(user_id)
    from services import trial_manager
    trial = trial_manager.is_active(uid)
    with db._lock, models._lock, connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute('SELECT 1 FROM billing_clocks WHERE user_id=?', (uid,)).fetchone():
            raise ValueError('enrolled accounts must use durable reconciliation')
        settings = db._get_internal(uid)
        row = conn.execute('SELECT diamonds FROM users WHERE telegram_id=?', (uid,)).fetchone()
        if not row:
            return {'deducted': 0, 'balance': 0, 'seconds': 0, 'enabled': False}
        before = row['diamonds']
        enabled = settings.get('self_enabled', False)
        seconds = 0.0 if trial else _carry(conn, uid, settings.get('bill_seconds', 0))
        if enabled and before > 0 and not trial:
            seconds += elapsed
        cost = min(before, int(seconds // INTERVAL)) if enabled and not trial else 0
        seconds %= INTERVAL
        stamp = models._now_iso()
        if cost:
            conn.execute('UPDATE users SET diamonds=?,updated_at=? WHERE telegram_id=?', (before-cost, stamp, uid))
            ledger_entry(conn, user_id=uid, tx_type='ADMIN_REMOVE', amount=-cost, before=before, after=before-cost,
                         game_id=None, description='HALF_HOUR_USAGE:legacy', created_at=stamp)
        conn.execute('INSERT INTO wallet_usage(user_id,seconds) VALUES (?,?) '
                     'ON CONFLICT(user_id) DO UPDATE SET seconds=excluded.seconds', (uid, seconds))
        conn.commit()
        return {'deducted': cost, 'balance': before-cost, 'seconds': seconds,
                'enabled': bool(enabled and (trial or before-cost > 0))}
