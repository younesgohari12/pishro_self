"""Atomic SQLite helpers for the virtual-diamond ledger."""
from __future__ import annotations

import sqlite3
from typing import Any

from database import models


def connect() -> sqlite3.Connection:
    models.init_game_db()
    conn = sqlite3.connect(models.TABCHI_DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=30000')
    return conn


def begin_immediate(conn: sqlite3.Connection) -> None:
    """SQLite's equivalent of a write lock for the game transaction."""
    conn.execute('BEGIN IMMEDIATE')
    from services.usage_service import settle_due_in_transaction
    settle_due_in_transaction(conn)


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def ledger_entry(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    tx_type: str,
    amount: int,
    before: int,
    after: int,
    game_id: int | None,
    description: str,
    created_at: str,
) -> None:
    if tx_type not in models.DIAMOND_TRANSACTION_TYPES:
        raise ValueError('invalid diamond transaction type')
    if int(after) < 0:
        raise ValueError('negative diamond balance is forbidden')
    conn.execute(
        '''INSERT INTO diamond_transactions
           (user_id,type,amount,balance_before,balance_after,game_id,description,created_at)
           VALUES (?,?,?,?,?,?,?,?)''',
        (
            int(user_id), tx_type, int(amount), int(before), int(after),
            int(game_id) if game_id is not None else None,
            str(description or '')[:500], created_at,
        )
    )

    if not str(description).startswith('HALF_HOUR_USAGE:'):
        from services.usage_service import balance_changed
        balance_changed(conn, int(user_id), int(before), int(after))
