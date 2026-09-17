"""Persistent SQLite storage for the Tabchi system.

Security rule: every mutable/read operation is scoped by ``user_id``.  A
banner/blacklist row can never be accessed only by its numeric id.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any

from config import DB_DIR

TABCHI_DB_PATH = os.path.join(DB_DIR, 'tabchi.sqlite3')
_lock = threading.RLock()
_VALID_TARGETS = {'group', 'private'}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(TABCHI_DB_PATH, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=15000')
    return conn


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(r['name']) for r in conn.execute(f'PRAGMA table_info({table})').fetchall()}


def normalize_send_targets(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            decoded = json.loads(stripped)
        except Exception:
            decoded = [p.strip() for p in stripped.split(',') if p.strip()]
        value = decoded
    if not isinstance(value, (list, tuple, set)):
        value = [value]
    seen: set[str] = set()
    for item in value:
        key = str(item).strip().lower()
        if key in _VALID_TARGETS:
            seen.add(key)
    return [key for key in ('group', 'private') if key in seen]


def encode_send_targets(value: Any) -> str:
    return json.dumps(normalize_send_targets(value), ensure_ascii=False, separators=(',', ':'))


def get_banner_send_targets(banner: dict[str, Any] | None) -> list[str]:
    if not banner:
        return []
    return normalize_send_targets(banner.get('send_target'))


def normalize_blacklist_target(target: str) -> str:
    value = (target or '').strip()
    if value.startswith('@'):
        return '@' + value[1:].strip().lower()
    return value.lower()


def init_tabchi_db() -> None:
    os.makedirs(os.path.dirname(TABCHI_DB_PATH), exist_ok=True)
    with _lock, _conn() as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.executescript(
            '''
            CREATE TABLE IF NOT EXISTS banners (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                file_id TEXT,
                text TEXT,
                caption TEXT,
                send_mode TEXT NOT NULL CHECK(send_mode IN ('forward','normal')),
                send_target TEXT NOT NULL DEFAULT '[]',
                interval INTEGER NOT NULL CHECK(interval >= 1),
                status TEXT NOT NULL DEFAULT 'paused' CHECK(status IN ('active','paused')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                source_peer TEXT,
                source_message_id INTEGER,
                last_sent_at REAL,
                next_send_at REAL
            );

            CREATE INDEX IF NOT EXISTS idx_banners_owner
            ON banners(user_id, id);

            CREATE INDEX IF NOT EXISTS idx_banners_due
            ON banners(status, next_send_at);

            CREATE TABLE IF NOT EXISTS tabchi_whitelist_settings (
                user_id INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1))
            );
            CREATE TABLE IF NOT EXISTS tabchi_whitelist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                peer_id INTEGER NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('group','private')),
                title TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(user_id,peer_id)
            );
            CREATE INDEX IF NOT EXISTS idx_tabchi_whitelist_owner ON tabchi_whitelist(user_id,id);

            CREATE TABLE IF NOT EXISTS blacklist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                target TEXT NOT NULL,
                target_type TEXT NOT NULL CHECK(target_type IN ('user','group')),
                created_at TEXT NOT NULL,
                UNIQUE(user_id, target)
            );

            CREATE INDEX IF NOT EXISTS idx_blacklist_owner
            ON blacklist(user_id, id);

            -- Kept for backward compatibility with v0.05. New v0.06 sends by
            -- the banner's group/private category selection instead.
            CREATE TABLE IF NOT EXISTS banner_targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                banner_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                raw_id INTEGER NOT NULL,
                access_hash INTEGER,
                username TEXT,
                title TEXT NOT NULL,
                kind TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                FOREIGN KEY(banner_id) REFERENCES banners(id) ON DELETE CASCADE,
                UNIQUE(banner_id, chat_id)
            );

            CREATE TABLE IF NOT EXISTS send_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                banner_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                target_chat_id INTEGER,
                target_title TEXT,
                status TEXT NOT NULL,
                error TEXT,
                telegram_message_id INTEGER,
                sent_at TEXT NOT NULL,
                FOREIGN KEY(banner_id) REFERENCES banners(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_logs_banner
            ON send_logs(banner_id, user_id, id DESC);
            '''
        )

        # Upgrade an existing v0.05 database in place.
        cols = _column_names(conn, 'banners')
        if 'send_target' not in cols:
            conn.execute("ALTER TABLE banners ADD COLUMN send_target TEXT NOT NULL DEFAULT '[\"group\"]'")
        conn.commit()


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def create_banner(*, user_id: int, name: str, type: str, file_id: str | None,
                  text: str | None, caption: str | None, send_mode: str,
                  send_target: Any = ('group',), interval: int = 5, status: str = 'paused',
                  source_peer: str | None, source_message_id: int | None) -> int:
    if send_mode not in {'forward', 'normal'}:
        raise ValueError('invalid send_mode')
    targets = normalize_send_targets(send_target)
    if not targets:
        raise ValueError('at least one send_target is required')
    now = _now_iso()
    next_send = time.time() + int(interval) * 60 if status == 'active' else None
    with _lock, _conn() as conn:
        cur = conn.execute(
            '''INSERT INTO banners
               (user_id,name,type,file_id,text,caption,send_mode,send_target,interval,status,
                created_at,updated_at,source_peer,source_message_id,next_send_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (int(user_id), name.strip(), type, file_id, text, caption, send_mode,
             encode_send_targets(targets), int(interval), status, now, now,
             source_peer, source_message_id, next_send)
        )
        conn.commit()
        return int(cur.lastrowid)


def list_banners(user_id: int) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            'SELECT * FROM banners WHERE user_id=? ORDER BY id DESC', (int(user_id),)
        ).fetchall()
    return [dict(r) for r in rows]


def get_banner(banner_id: int, user_id: int) -> dict[str, Any] | None:
    with _conn() as conn:
        return _row(conn.execute(
            'SELECT * FROM banners WHERE id=? AND user_id=?',
            (int(banner_id), int(user_id))
        ).fetchone())


def update_banner(banner_id: int, user_id: int, **fields: Any) -> bool:
    allowed = {
        'name', 'type', 'file_id', 'text', 'caption', 'send_mode', 'send_target',
        'interval', 'status', 'source_peer', 'source_message_id', 'last_sent_at',
        'next_send_at'
    }
    patch = {k: v for k, v in fields.items() if k in allowed}
    if not patch:
        return False
    if 'interval' in patch:
        patch['interval'] = max(1, int(patch['interval']))
    if 'send_mode' in patch and patch['send_mode'] not in {'forward', 'normal'}:
        raise ValueError('invalid send_mode')
    if 'status' in patch and patch['status'] not in {'active', 'paused'}:
        raise ValueError('invalid status')
    if 'send_target' in patch:
        targets = normalize_send_targets(patch['send_target'])
        if not targets:
            raise ValueError('at least one send_target is required')
        patch['send_target'] = encode_send_targets(targets)
    patch['updated_at'] = _now_iso()
    sql = ', '.join(f'{k}=?' for k in patch)
    values = list(patch.values()) + [int(banner_id), int(user_id)]
    with _lock, _conn() as conn:
        cur = conn.execute(f'UPDATE banners SET {sql} WHERE id=? AND user_id=?', values)
        conn.commit()
        return cur.rowcount == 1


def set_banner_status(banner_id: int, user_id: int, status: str) -> bool:
    if status == 'active':
        banner = get_banner(banner_id, user_id)
        if not banner:
            return False
        next_send = time.time() + max(1, int(banner['interval'])) * 60
        return update_banner(banner_id, user_id, status='active', next_send_at=next_send)
    if status == 'paused':
        return update_banner(banner_id, user_id, status='paused', next_send_at=None)
    raise ValueError('invalid status')


def delete_banner(banner_id: int, user_id: int) -> bool:
    with _lock, _conn() as conn:
        cur = conn.execute(
            'DELETE FROM banners WHERE id=? AND user_id=?',
            (int(banner_id), int(user_id))
        )
        conn.commit()
        return cur.rowcount == 1


# ---------------------------------------------------------------------------
# Per-user blacklist
# ---------------------------------------------------------------------------
def add_blacklist(*, user_id: int, target: str, target_type: str) -> int:
    target_type = str(target_type).strip().lower()
    if target_type not in {'user', 'group'}:
        raise ValueError('invalid target_type')
    normalized = normalize_blacklist_target(target)
    if not normalized:
        raise ValueError('empty target')
    with _lock, _conn() as conn:
        conn.execute(
            '''INSERT INTO blacklist(user_id,target,target_type,created_at)
               VALUES (?,?,?,?)
               ON CONFLICT(user_id,target) DO UPDATE SET target_type=excluded.target_type''',
            (int(user_id), normalized, target_type, _now_iso())
        )
        row = conn.execute(
            'SELECT id FROM blacklist WHERE user_id=? AND target=?',
            (int(user_id), normalized)
        ).fetchone()
        conn.commit()
        return int(row['id'])


def list_blacklist(user_id: int) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            'SELECT * FROM blacklist WHERE user_id=? ORDER BY id DESC',
            (int(user_id),)
        ).fetchall()
    return [dict(r) for r in rows]


def delete_blacklist(entry_id: int, user_id: int) -> bool:
    with _lock, _conn() as conn:
        cur = conn.execute(
            'DELETE FROM blacklist WHERE id=? AND user_id=?',
            (int(entry_id), int(user_id))
        )
        conn.commit()
        return cur.rowcount == 1


def blacklist_count(user_id: int) -> int:
    with _conn() as conn:
        row = conn.execute('SELECT COUNT(*) AS c FROM blacklist WHERE user_id=?', (int(user_id),)).fetchone()
    return int(row['c']) if row else 0


def blacklist_values(user_id: int) -> set[str]:
    return {normalize_blacklist_target(r['target']) for r in list_blacklist(user_id)}


def target_is_blacklisted(user_id: int, *, entity_id: int | None = None,
                          peer_id: int | None = None, username: str | None = None) -> bool:
    blocked = blacklist_values(user_id)
    candidates: set[str] = set()
    if entity_id is not None:
        candidates.add(str(int(entity_id)).lower())
    if peer_id is not None:
        candidates.add(str(int(peer_id)).lower())
    if username:
        uname = username.strip().lstrip('@').lower()
        if uname:
            candidates.add(uname)
            candidates.add('@' + uname)
    return bool(blocked.intersection(candidates))


# ---------------------------------------------------------------------------
# v0.05 explicit target compatibility helpers
# ---------------------------------------------------------------------------
def add_target(*, banner_id: int, user_id: int, chat_id: int, raw_id: int,
               access_hash: int | None, username: str | None, title: str,
               kind: str) -> int:
    if not get_banner(banner_id, user_id):
        raise PermissionError('banner not found or not owned by user')
    with _lock, _conn() as conn:
        conn.execute(
            '''INSERT INTO banner_targets
               (banner_id,user_id,chat_id,raw_id,access_hash,username,title,kind,enabled,created_at)
               VALUES (?,?,?,?,?,?,?,?,1,?)
               ON CONFLICT(banner_id,chat_id) DO UPDATE SET
                 raw_id=excluded.raw_id, access_hash=excluded.access_hash,
                 username=excluded.username, title=excluded.title,
                 kind=excluded.kind, enabled=1''',
            (int(banner_id), int(user_id), int(chat_id), int(raw_id), access_hash,
             username, title[:200], kind, _now_iso())
        )
        row = conn.execute(
            'SELECT id FROM banner_targets WHERE banner_id=? AND chat_id=?',
            (int(banner_id), int(chat_id))
        ).fetchone()
        conn.commit()
        return int(row['id'])


def list_targets(banner_id: int, user_id: int, enabled_only: bool = False) -> list[dict[str, Any]]:
    if not get_banner(banner_id, user_id):
        return []
    sql = 'SELECT * FROM banner_targets WHERE banner_id=? AND user_id=?'
    params: list[Any] = [int(banner_id), int(user_id)]
    if enabled_only:
        sql += ' AND enabled=1'
    sql += ' ORDER BY id'
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def delete_target(target_id: int, banner_id: int, user_id: int) -> bool:
    with _lock, _conn() as conn:
        cur = conn.execute(
            'DELETE FROM banner_targets WHERE id=? AND banner_id=? AND user_id=?',
            (int(target_id), int(banner_id), int(user_id))
        )
        conn.commit()
        return cur.rowcount == 1


# ---------------------------------------------------------------------------
# Scheduler / send log helpers
# ---------------------------------------------------------------------------
def list_due_banners(now_ts: float | None = None, limit: int = 50) -> list[dict[str, Any]]:
    now_ts = time.time() if now_ts is None else float(now_ts)
    with _conn() as conn:
        rows = conn.execute(
            '''SELECT * FROM banners
               WHERE status='active' AND next_send_at IS NOT NULL AND next_send_at<=?
               ORDER BY next_send_at ASC LIMIT ?''',
            (now_ts, int(limit))
        ).fetchall()
    return [dict(r) for r in rows]


def mark_banner_cycle(banner_id: int, user_id: int, interval: int) -> None:
    now = time.time()
    update_banner(
        banner_id, user_id,
        last_sent_at=now,
        next_send_at=now + max(1, int(interval)) * 60,
    )


def defer_banner(banner_id: int, user_id: int, seconds: int = 60) -> None:
    update_banner(banner_id, user_id, next_send_at=time.time() + max(5, int(seconds)))


def log_send(*, banner_id: int, user_id: int, target_chat_id: int | None,
             target_title: str | None, status: str, error: str | None = None,
             telegram_message_id: int | None = None) -> None:
    if not get_banner(banner_id, user_id):
        return
    with _lock, _conn() as conn:
        conn.execute(
            '''INSERT INTO send_logs
               (banner_id,user_id,target_chat_id,target_title,status,error,telegram_message_id,sent_at)
               VALUES (?,?,?,?,?,?,?,?)''',
            (int(banner_id), int(user_id), target_chat_id, target_title,
             status[:32], (error or '')[:1000] or None, telegram_message_id, _now_iso())
        )
        conn.commit()


def list_send_logs(banner_id: int, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    if not get_banner(banner_id, user_id):
        return []
    with _conn() as conn:
        rows = conn.execute(
            '''SELECT * FROM send_logs WHERE banner_id=? AND user_id=?
               ORDER BY id DESC LIMIT ?''',
            (int(banner_id), int(user_id), max(1, min(100, int(limit))))
        ).fetchall()
    return [dict(r) for r in rows]

# ---------------------------------------------------------------------------
# Deleted-message saver / Anti Delete
# ---------------------------------------------------------------------------
def init_message_saver_db() -> None:
    os.makedirs(os.path.dirname(TABCHI_DB_PATH), exist_ok=True)
    with _lock, _conn() as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS deleted_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                sender_id INTEGER,
                sender_username TEXT,
                message_id INTEGER NOT NULL,
                message_type TEXT NOT NULL,
                text TEXT,
                caption TEXT,
                media_id TEXT,
                saved_at TEXT NOT NULL,
                UNIQUE(owner_id, chat_id, message_id)
            );

            CREATE INDEX IF NOT EXISTS idx_deleted_messages_owner
            ON deleted_messages(owner_id, id DESC);

            CREATE TABLE IF NOT EXISTS save_settings (
                user_id INTEGER PRIMARY KEY,
                status INTEGER NOT NULL DEFAULT 0 CHECK(status IN (0,1)),
                destination TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS message_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                sender_id INTEGER,
                sender_username TEXT,
                message_id INTEGER NOT NULL,
                message_type TEXT NOT NULL,
                text TEXT,
                caption TEXT,
                media_id TEXT,
                media_path TEXT,
                chat_title TEXT,
                message_date TEXT,
                extra_json TEXT,
                cached_at TEXT NOT NULL,
                UNIQUE(owner_id, chat_id, message_id)
            );

            CREATE INDEX IF NOT EXISTS idx_message_cache_lookup
            ON message_cache(owner_id, message_id, chat_id);
            """
        )
        conn.commit()


def _ensure_save_settings(user_id: int) -> None:
    init_message_saver_db()
    with _lock, _conn() as conn:
        conn.execute(
            '''INSERT OR IGNORE INTO save_settings(user_id,status,destination,created_at)
               VALUES (?,0,NULL,?)''',
            (int(user_id), _now_iso())
        )
        conn.commit()


def get_save_settings(user_id: int) -> dict[str, Any]:
    _ensure_save_settings(user_id)
    with _conn() as conn:
        row = conn.execute(
            'SELECT user_id,status,destination,created_at FROM save_settings WHERE user_id=?',
            (int(user_id),)
        ).fetchone()
    return dict(row) if row else {
        'user_id': int(user_id), 'status': 0, 'destination': None, 'created_at': _now_iso()
    }


def set_save_status(user_id: int, enabled: bool) -> None:
    _ensure_save_settings(user_id)
    with _lock, _conn() as conn:
        conn.execute(
            'UPDATE save_settings SET status=? WHERE user_id=?',
            (1 if enabled else 0, int(user_id))
        )
        conn.commit()


def set_save_destination(user_id: int, destination: str) -> None:
    value = (destination or '').strip()
    if not value:
        raise ValueError('empty destination')
    _ensure_save_settings(user_id)
    with _lock, _conn() as conn:
        conn.execute(
            'UPDATE save_settings SET destination=? WHERE user_id=?',
            (value, int(user_id))
        )
        conn.commit()


def cache_message_record(*, owner_id: int, chat_id: int, sender_id: int | None,
                         sender_username: str | None, message_id: int,
                         message_type: str, text: str | None, caption: str | None,
                         media_id: str | None, media_path: str | None,
                         chat_title: str | None, message_date: str | None,
                         extra_json: str | None) -> None:
    init_message_saver_db()
    with _lock, _conn() as conn:
        conn.execute(
            '''INSERT INTO message_cache
               (owner_id,chat_id,sender_id,sender_username,message_id,message_type,
                text,caption,media_id,media_path,chat_title,message_date,extra_json,cached_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(owner_id,chat_id,message_id) DO UPDATE SET
                 sender_id=excluded.sender_id,
                 sender_username=excluded.sender_username,
                 message_type=excluded.message_type,
                 text=excluded.text,
                 caption=excluded.caption,
                 media_id=excluded.media_id,
                 media_path=COALESCE(excluded.media_path,message_cache.media_path),
                 chat_title=excluded.chat_title,
                 message_date=excluded.message_date,
                 extra_json=excluded.extra_json''',
            (int(owner_id), int(chat_id), sender_id, sender_username, int(message_id),
             message_type, text, caption, media_id, media_path, chat_title,
             message_date, extra_json, _now_iso())
        )
        conn.commit()


def update_message_cache_media(owner_id: int, chat_id: int, message_id: int,
                               media_path: str) -> None:
    with _lock, _conn() as conn:
        conn.execute(
            '''UPDATE message_cache SET media_path=?
               WHERE owner_id=? AND chat_id=? AND message_id=?''',
            (media_path, int(owner_id), int(chat_id), int(message_id))
        )
        conn.commit()


def find_cached_deleted_candidates(*, owner_id: int, message_id: int,
                                   chat_id: int | None = None) -> list[dict[str, Any]]:
    init_message_saver_db()
    with _conn() as conn:
        if chat_id is None:
            rows = conn.execute(
                '''SELECT * FROM message_cache
                   WHERE owner_id=? AND message_id=? ORDER BY id DESC''',
                (int(owner_id), int(message_id))
            ).fetchall()
        else:
            rows = conn.execute(
                '''SELECT * FROM message_cache
                   WHERE owner_id=? AND chat_id=? AND message_id=? ORDER BY id DESC''',
                (int(owner_id), int(chat_id), int(message_id))
            ).fetchall()
    return [dict(row) for row in rows]


def deleted_message_exists(owner_id: int, chat_id: int, message_id: int) -> bool:
    init_message_saver_db()
    with _conn() as conn:
        row = conn.execute(
            '''SELECT 1 FROM deleted_messages
               WHERE owner_id=? AND chat_id=? AND message_id=? LIMIT 1''',
            (int(owner_id), int(chat_id), int(message_id))
        ).fetchone()
    return row is not None


def record_deleted_message(cache_row: dict[str, Any]) -> int | None:
    owner_id = int(cache_row['owner_id'])
    chat_id = int(cache_row['chat_id'])
    message_id = int(cache_row['message_id'])
    init_message_saver_db()
    with _lock, _conn() as conn:
        conn.execute(
            '''INSERT OR IGNORE INTO deleted_messages
               (owner_id,chat_id,sender_id,sender_username,message_id,message_type,
                text,caption,media_id,saved_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (owner_id, chat_id, cache_row.get('sender_id'), cache_row.get('sender_username'),
             message_id, cache_row.get('message_type') or 'other', cache_row.get('text'),
             cache_row.get('caption'), cache_row.get('media_id'), _now_iso())
        )
        row = conn.execute(
            '''SELECT id FROM deleted_messages
               WHERE owner_id=? AND chat_id=? AND message_id=?''',
            (owner_id, chat_id, message_id)
        ).fetchone()
        conn.commit()
    return int(row['id']) if row else None


def delete_message_cache(owner_id: int, chat_id: int, message_id: int) -> bool:
    with _lock, _conn() as conn:
        cur = conn.execute(
            '''DELETE FROM message_cache
               WHERE owner_id=? AND chat_id=? AND message_id=?''',
            (int(owner_id), int(chat_id), int(message_id))
        )
        conn.commit()
        return cur.rowcount > 0


def clear_message_cache(owner_id: int) -> int:
    init_message_saver_db()
    with _lock, _conn() as conn:
        cur = conn.execute('DELETE FROM message_cache WHERE owner_id=?', (int(owner_id),))
        conn.commit()
        return int(cur.rowcount or 0)


def list_deleted_messages(owner_id: int, limit: int = 50) -> list[dict[str, Any]]:
    init_message_saver_db()
    with _conn() as conn:
        rows = conn.execute(
            '''SELECT * FROM deleted_messages
               WHERE owner_id=? ORDER BY id DESC LIMIT ?''',
            (int(owner_id), max(1, min(500, int(limit))))
        ).fetchall()
    return [dict(row) for row in rows]


def get_deleted_message_stats(owner_id: int) -> dict[str, Any]:
    init_message_saver_db()
    stats: dict[str, Any] = {
        'total': 0, 'text': 0, 'photo': 0, 'video': 0, 'gif': 0,
        'voice': 0, 'audio': 0, 'file': 0, 'sticker': 0, 'location': 0,
        'contact': 0, 'other': 0, 'last_saved_at': None,
    }
    with _conn() as conn:
        total = conn.execute(
            'SELECT COUNT(*) AS c, MAX(saved_at) AS last_saved FROM deleted_messages WHERE owner_id=?',
            (int(owner_id),)
        ).fetchone()
        rows = conn.execute(
            '''SELECT message_type, COUNT(*) AS c FROM deleted_messages
               WHERE owner_id=? GROUP BY message_type''',
            (int(owner_id),)
        ).fetchall()
    if total:
        stats['total'] = int(total['c'] or 0)
        stats['last_saved_at'] = total['last_saved']
    for row in rows:
        key = str(row['message_type'] or 'other')
        if key not in stats:
            key = 'other'
        stats[key] = int(row['c'] or 0)
    return stats

# ---------------------------------------------------------------------------
# Trial / support / admin system (v0.09)
# ---------------------------------------------------------------------------
ADMIN_PERMISSION_KEYS = (
    'view_users',
    'manage_users',
    'answer_tickets',
    'manage_tickets',
    'manage_support',
    'manage_trial',
    'manage_admins',
    'system_settings',
    'broadcast',
    'view_stats',
    'manage_games',
)


def init_membership_support_db() -> None:
    os.makedirs(os.path.dirname(TABCHI_DB_PATH), exist_ok=True)
    with _lock, _conn() as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.executescript(
            '''
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                phone TEXT,
                registered_at TEXT NOT NULL,
                last_activity TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS free_trial (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                phone TEXT,
                used INTEGER NOT NULL DEFAULT 0 CHECK(used IN (0,1)),
                active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0,1)),
                expire_time TEXT,
                created_at TEXT NOT NULL,
                activated_at TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_free_trial_active
            ON free_trial(active, expire_time);

            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')),
                assigned_admin_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                closed_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_tickets_user
            ON tickets(user_id, id DESC);
            CREATE INDEX IF NOT EXISTS idx_tickets_status
            ON tickets(status, id DESC);

            CREATE TABLE IF NOT EXISTS ticket_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL,
                sender_role TEXT NOT NULL CHECK(sender_role IN ('user','admin')),
                sender_id INTEGER NOT NULL,
                message_type TEXT NOT NULL,
                text TEXT,
                telegram_message_id INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY(ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_ticket_messages_ticket
            ON ticket_messages(ticket_id, id);

            CREATE TABLE IF NOT EXISTS support_contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target TEXT NOT NULL UNIQUE,
                target_id INTEGER,
                username TEXT,
                display_name TEXT,
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE,
                username TEXT UNIQUE,
                role TEXT NOT NULL CHECK(role IN ('full','limited')),
                permissions TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            '''
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Diamond games / authoritative SQLite wallet
# ---------------------------------------------------------------------------
GAME_OPEN = 'OPEN'
GAME_LOCKED = 'LOCKED'
GAME_FINISHED = 'FINISHED'
GAME_CANCELLED = 'CANCELLED'
GAME_EXPIRED = 'EXPIRED'
GAME_STATUSES = (GAME_OPEN, GAME_LOCKED, GAME_FINISHED, GAME_CANCELLED, GAME_EXPIRED)

DIAMOND_TRANSACTION_TYPES = (
    'GAME_STAKE', 'GAME_WIN', 'GAME_REFUND', 'ADMIN_ADD', 'ADMIN_REMOVE'
)


def init_game_db() -> None:
    """Create the game schema in-place without touching existing tables/data."""
    os.makedirs(os.path.dirname(TABCHI_DB_PATH), exist_ok=True)
    with _lock, _conn() as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.executescript(
            '''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL UNIQUE,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                diamonds INTEGER NOT NULL DEFAULT 0 CHECK(diamonds >= 0),
                banned INTEGER NOT NULL DEFAULT 0 CHECK(banned IN (0,1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_game_users_username
            ON users(username COLLATE NOCASE);

            CREATE TABLE IF NOT EXISTS wallet_usage (
                user_id INTEGER PRIMARY KEY REFERENCES users(telegram_id),
                seconds REAL NOT NULL CHECK(seconds >= 0 AND seconds < 3600)
            );
            CREATE TABLE IF NOT EXISTS billing_clocks (
                user_id INTEGER PRIMARY KEY REFERENCES users(telegram_id),
                checkpoint REAL NOT NULL CHECK(checkpoint >= 0),
                enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                free_until REAL NOT NULL DEFAULT 0,
                next_due REAL
            );
            CREATE INDEX IF NOT EXISTS idx_billing_due ON billing_clocks(next_due);
            CREATE TABLE IF NOT EXISTS payment_migrations (
                name TEXT PRIMARY KEY, value INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS wallet_payments (
                id INTEGER PRIMARY KEY,
                uid INTEGER NOT NULL,
                diamonds INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected')),
                created_at TEXT NOT NULL,
                created_ts REAL NOT NULL,
                reviewed_at TEXT NOT NULL DEFAULT '',
                reviewer_id INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_wallet_payments_status ON wallet_payments(uid,status);

            CREATE TABLE IF NOT EXISTS wallet_transfers (
                request_id TEXT PRIMARY KEY,
                sender_id INTEGER NOT NULL REFERENCES users(telegram_id),
                recipient_id INTEGER NOT NULL REFERENCES users(telegram_id),
                amount INTEGER NOT NULL CHECK(amount > 0),
                fee INTEGER NOT NULL CHECK(fee >= 0),
                received INTEGER NOT NULL CHECK(received > 0),
                sender_after INTEGER NOT NULL CHECK(sender_after >= 0),
                recipient_after INTEGER NOT NULL CHECK(recipient_after >= 0),
                created_at TEXT NOT NULL,
                CHECK(sender_id != recipient_id), CHECK(amount = fee + received)
            );
            CREATE TABLE IF NOT EXISTS referral_claims (
                invitee_id INTEGER PRIMARY KEY,
                referrer_id INTEGER NOT NULL REFERENCES users(telegram_id),
                reward INTEGER NOT NULL CHECK(reward > 0),
                created_at TEXT NOT NULL,
                CHECK(invitee_id != referrer_id)
            );

            CREATE TABLE IF NOT EXISTS game_settings (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                main_game_group TEXT,
                main_game_group_title TEXT,
                game_enabled INTEGER NOT NULL DEFAULT 0 CHECK(game_enabled IN (0,1)),
                game_tax INTEGER NOT NULL DEFAULT 10 CHECK(game_tax BETWEEN 0 AND 30),
                min_bet INTEGER NOT NULL DEFAULT 100 CHECK(min_bet >= 1),
                max_bet INTEGER NOT NULL DEFAULT 1000000 CHECK(max_bet >= min_bet),
                expiration_seconds INTEGER NOT NULL DEFAULT 600 CHECK(expiration_seconds BETWEEN 60 AND 86400),
                prevent_multiple_open INTEGER NOT NULL DEFAULT 1 CHECK(prevent_multiple_open IN (0,1)),
                winner_method TEXT NOT NULL DEFAULT 'secrets' CHECK(winner_method = 'secrets'),
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                creator_id INTEGER NOT NULL,
                creator_username TEXT,
                opponent_id INTEGER,
                opponent_username TEXT,
                bet_amount INTEGER NOT NULL CHECK(bet_amount > 0),
                tax_percent INTEGER NOT NULL CHECK(tax_percent BETWEEN 0 AND 30),
                tax_amount INTEGER NOT NULL DEFAULT 0 CHECK(tax_amount >= 0),
                reward_amount INTEGER NOT NULL DEFAULT 0 CHECK(reward_amount >= 0),
                winner_id INTEGER,
                loser_id INTEGER,
                status TEXT NOT NULL CHECK(status IN ('OPEN','LOCKED','FINISHED','CANCELLED','EXPIRED')),
                created_at TEXT NOT NULL,
                joined_at TEXT,
                finished_at TEXT,
                expires_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_games_open_expiry
            ON games(status, expires_at);
            CREATE INDEX IF NOT EXISTS idx_games_creator_status
            ON games(creator_id, status);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_games_message
            ON games(chat_id, message_id) WHERE message_id > 0;

            CREATE TABLE IF NOT EXISTS diamond_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('GAME_STAKE','GAME_WIN','GAME_REFUND','ADMIN_ADD','ADMIN_REMOVE')),
                amount INTEGER NOT NULL,
                balance_before INTEGER NOT NULL CHECK(balance_before >= 0),
                balance_after INTEGER NOT NULL CHECK(balance_after >= 0),
                game_id INTEGER,
                description TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(game_id) REFERENCES games(id)
            );

            CREATE INDEX IF NOT EXISTS idx_diamond_tx_user
            ON diamond_transactions(user_id, id DESC);
            CREATE INDEX IF NOT EXISTS idx_diamond_tx_game
            ON diamond_transactions(game_id, id);
            '''
        )
        user_cols = _column_names(conn, 'users')
        if 'banned' not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN banned INTEGER NOT NULL DEFAULT 0")
        if 'last_name' not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN last_name TEXT")

        conn.execute(
            '''INSERT OR IGNORE INTO game_settings
               (id,game_enabled,game_tax,min_bet,max_bet,expiration_seconds,
                prevent_multiple_open,winner_method,updated_at)
               VALUES (1,0,10,100,1000000,600,1,'secrets',?)''',
            (_now_iso(),)
        )
        conn.commit()


def get_game_settings() -> dict[str, Any]:
    init_game_db()
    with _conn() as conn:
        row = conn.execute('SELECT * FROM game_settings WHERE id=1').fetchone()
    return dict(row) if row else {}


def update_game_settings(**patch: Any) -> dict[str, Any]:
    allowed = {
        'main_game_group', 'main_game_group_title', 'game_enabled', 'game_tax',
        'min_bet', 'max_bet', 'expiration_seconds', 'prevent_multiple_open'
    }
    clean = {key: value for key, value in patch.items() if key in allowed}
    if not clean:
        return get_game_settings()
    if 'game_enabled' in clean:
        clean['game_enabled'] = 1 if clean['game_enabled'] else 0
    if 'prevent_multiple_open' in clean:
        clean['prevent_multiple_open'] = 1 if clean['prevent_multiple_open'] else 0
    if 'game_tax' in clean:
        clean['game_tax'] = int(clean['game_tax'])
        if not 0 <= clean['game_tax'] <= 30:
            raise ValueError('game_tax must be between 0 and 30')
    if 'min_bet' in clean:
        clean['min_bet'] = int(clean['min_bet'])
        if clean['min_bet'] < 1:
            raise ValueError('min_bet must be positive')
    if 'max_bet' in clean:
        clean['max_bet'] = int(clean['max_bet'])
        if clean['max_bet'] < 1:
            raise ValueError('max_bet must be positive')
    if 'expiration_seconds' in clean:
        clean['expiration_seconds'] = int(clean['expiration_seconds'])
        if not 60 <= clean['expiration_seconds'] <= 86400:
            raise ValueError('expiration_seconds must be between 60 and 86400')
    current = get_game_settings()
    minimum = int(clean.get('min_bet', current.get('min_bet', 100)))
    maximum = int(clean.get('max_bet', current.get('max_bet', 1000000)))
    if maximum < minimum:
        raise ValueError('max_bet cannot be less than min_bet')
    clean['updated_at'] = _now_iso()
    columns = ','.join(f'{key}=?' for key in clean)
    with _lock, _conn() as conn:
        conn.execute(f'UPDATE game_settings SET {columns} WHERE id=1', tuple(clean.values()))
        conn.commit()
    return get_game_settings()


def get_game(game_id: int) -> dict[str, Any] | None:
    init_game_db()
    with _conn() as conn:
        return _row(conn.execute('SELECT * FROM games WHERE id=?', (int(game_id),)).fetchone())


def list_games(limit: int = 20) -> list[dict[str, Any]]:
    init_game_db()
    with _conn() as conn:
        rows = conn.execute('SELECT * FROM games ORDER BY id DESC LIMIT ?', (max(1, min(100, int(limit))),)).fetchall()
    return [dict(row) for row in rows]


def game_statistics() -> dict[str, int]:
    init_game_db()
    with _conn() as conn:
        rows = conn.execute('SELECT status,COUNT(*) AS c FROM games GROUP BY status').fetchall()
        totals = conn.execute(
            '''SELECT COALESCE(SUM(CASE WHEN status='FINISHED' THEN bet_amount*2 ELSE 0 END),0) AS volume,
                      COALESCE(SUM(CASE WHEN status='FINISHED' THEN tax_amount ELSE 0 END),0) AS tax
               FROM games'''
        ).fetchone()
    out = {status: 0 for status in GAME_STATUSES}
    out.update({str(row['status']): int(row['c']) for row in rows})
    out['TOTAL'] = sum(out[status] for status in GAME_STATUSES)
    out['VOLUME'] = int(totals['volume'] or 0)
    out['TAX'] = int(totals['tax'] or 0)
    return out


def find_game_user(token: str) -> dict[str, Any] | None:
    init_game_db()
    value = (token or '').strip()
    with _conn() as conn:
        if value.lstrip('-').isdigit():
            row = conn.execute('SELECT * FROM users WHERE telegram_id=?', (int(value),)).fetchone()
        else:
            row = conn.execute(
                'SELECT * FROM users WHERE username=? COLLATE NOCASE',
                (value.lstrip('@'),)
            ).fetchone()
    return _row(row)


def search_users(identifier: str) -> list[dict[str, Any]]:
    init_game_db()
    value = (identifier or '').strip()
    if not value:
        return []
    with _conn() as conn:
        like = '%' + value.lstrip('@') + '%'
        if value.lstrip('-').isdigit():
            rows = conn.execute('SELECT * FROM users WHERE telegram_id=? ORDER BY id DESC', (int(value),)).fetchall()
        else:
            rows = conn.execute(
                '''SELECT * FROM users WHERE username LIKE ? COLLATE NOCASE
                   OR first_name LIKE ? COLLATE NOCASE
                   OR last_name LIKE ? COLLATE NOCASE ORDER BY id DESC LIMIT 20''',
                (like, like, like)
            ).fetchall()
    return [dict(r) for r in rows]


def get_user_details(user_id: int) -> dict[str, Any] | None:
    init_game_db()
    with _conn() as conn:
        row = conn.execute('SELECT * FROM users WHERE telegram_id=?', (int(user_id),)).fetchone()
        if not row:
            return None
        data = dict(row)
        ref = conn.execute('SELECT COUNT(*) AS c FROM referral_claims WHERE referrer_id=?', (int(user_id),)).fetchone()
        data['referral_count'] = int(ref['c'] or 0)
    return data


def ban_user(user_id: int) -> bool:
    init_game_db()
    with _lock, _conn() as conn:
        cur = conn.execute('UPDATE users SET banned=1, updated_at=? WHERE telegram_id=?', (_now_iso(), int(user_id)))
        conn.commit()
    return cur.rowcount == 1


def unban_user(user_id: int) -> bool:
    init_game_db()
    with _lock, _conn() as conn:
        cur = conn.execute('UPDATE users SET banned=0, updated_at=? WHERE telegram_id=?', (_now_iso(), int(user_id)))
        conn.commit()
    return cur.rowcount == 1


def is_user_banned(user_id: int) -> bool:
    init_game_db()
    with _conn() as conn:
        row = conn.execute('SELECT banned FROM users WHERE telegram_id=?', (int(user_id),)).fetchone()
    return bool(row and int(row['banned'] or 0))


def list_diamond_transactions(user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    init_game_db()
    with _conn() as conn:
        rows = conn.execute(
            'SELECT * FROM diamond_transactions WHERE user_id=? ORDER BY id DESC LIMIT ?',
            (int(user_id), max(1, min(100, int(limit))))
        ).fetchall()
    return [dict(row) for row in rows]


def touch_profile(user_id: int, *, username: str | None = None,
                  first_name: str | None = None, last_name: str | None = None,
                  phone: str | None = None, registered_at: str | None = None) -> dict[str, Any]:
    init_membership_support_db()
    now = _now_iso()
    with _lock, _conn() as conn:
        row = conn.execute('SELECT * FROM user_profiles WHERE user_id=?', (int(user_id),)).fetchone()
        if row is None:
            conn.execute(
                '''INSERT INTO user_profiles
                   (user_id,username,first_name,last_name,phone,registered_at,last_activity)
                   VALUES (?,?,?,?,?,?,?)''',
                (int(user_id), username or '', first_name or '', last_name or '', phone or '', registered_at or now, now)
            )
        else:
            cur = dict(row)
            conn.execute(
                '''UPDATE user_profiles SET username=?,first_name=?,last_name=?,phone=?,last_activity=?
                   WHERE user_id=?''',
                (
                    cur.get('username', '') if username is None else (username or ''),
                    cur.get('first_name', '') if first_name is None else (first_name or ''),
                    cur.get('last_name', '') if last_name is None else (last_name or ''),
                    cur.get('phone', '') if phone is None else (phone or ''),
                    now,
                    int(user_id),
                )
            )
        conn.commit()
        out = conn.execute('SELECT * FROM user_profiles WHERE user_id=?', (int(user_id),)).fetchone()
    return dict(out)


def get_profile(user_id: int) -> dict[str, Any] | None:
    init_membership_support_db()
    with _conn() as conn:
        row = conn.execute('SELECT * FROM user_profiles WHERE user_id=?', (int(user_id),)).fetchone()
    return dict(row) if row else None


def list_profiles(limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    init_membership_support_db()
    with _conn() as conn:
        rows = conn.execute(
            'SELECT * FROM user_profiles ORDER BY last_activity DESC LIMIT ? OFFSET ?',
            (max(1, min(500, int(limit))), max(0, int(offset)))
        ).fetchall()
    return [dict(r) for r in rows]


def profile_count() -> int:
    init_membership_support_db()
    with _conn() as conn:
        row = conn.execute('SELECT COUNT(*) AS c FROM user_profiles').fetchone()
    return int(row['c'] or 0)


def get_trial(user_id: int) -> dict[str, Any] | None:
    init_membership_support_db()
    with _conn() as conn:
        row = conn.execute('SELECT * FROM free_trial WHERE user_id=?', (int(user_id),)).fetchone()
    return dict(row) if row else None


def ensure_trial_row(user_id: int, *, username: str = '', first_name: str = '',
                     last_name: str = '', phone: str = '') -> dict[str, Any]:
    init_membership_support_db()
    now = _now_iso()
    with _lock, _conn() as conn:
        conn.execute(
            '''INSERT OR IGNORE INTO free_trial
               (user_id,username,first_name,last_name,phone,used,active,expire_time,created_at,activated_at,updated_at)
               VALUES (?,?,?,?,?,0,0,NULL,?,NULL,?)''',
            (int(user_id), username or '', first_name or '', last_name or '', phone or '', now, now)
        )
        conn.execute(
            '''UPDATE free_trial SET username=?,first_name=?,last_name=?,phone=?,updated_at=?
               WHERE user_id=?''',
            (username or '', first_name or '', last_name or '', phone or '', now, int(user_id))
        )
        conn.commit()
        row = conn.execute('SELECT * FROM free_trial WHERE user_id=?', (int(user_id),)).fetchone()
    return dict(row)


def activate_trial(user_id: int, *, username: str = '', first_name: str = '',
                   last_name: str = '', phone: str = '', expire_time: str) -> bool:
    row = ensure_trial_row(user_id, username=username, first_name=first_name, last_name=last_name, phone=phone)
    if int(row.get('used') or 0):
        return False
    now = _now_iso()
    with _lock, _conn() as conn:
        cur = conn.execute(
            '''UPDATE free_trial SET used=1,active=1,expire_time=?,activated_at=?,updated_at=?,
               username=?,first_name=?,last_name=?,phone=?
               WHERE user_id=? AND used=0''',
            (expire_time, now, now, username or '', first_name or '', last_name or '', phone or '', int(user_id))
        )
        conn.commit()
        return cur.rowcount == 1


def set_trial_active(user_id: int, active: bool, *, expire_time: str | None = None) -> bool:
    init_membership_support_db()
    fields = ['active=?', 'updated_at=?']
    values: list[Any] = [1 if active else 0, _now_iso()]
    if expire_time is not None:
        fields.append('expire_time=?')
        values.append(expire_time)
    values.append(int(user_id))
    with _lock, _conn() as conn:
        cur = conn.execute(f"UPDATE free_trial SET {', '.join(fields)} WHERE user_id=?", values)
        conn.commit()
        return cur.rowcount == 1


def list_active_trials(limit: int = 100) -> list[dict[str, Any]]:
    init_membership_support_db()
    with _conn() as conn:
        rows = conn.execute(
            '''SELECT * FROM free_trial WHERE active=1 ORDER BY expire_time ASC LIMIT ?''',
            (max(1, min(500, int(limit))),)
        ).fetchall()
    return [dict(r) for r in rows]


def trial_counts() -> dict[str, int]:
    init_membership_support_db()
    with _conn() as conn:
        row = conn.execute(
            '''SELECT COUNT(*) AS total,
                      SUM(CASE WHEN used=1 THEN 1 ELSE 0 END) AS used,
                      SUM(CASE WHEN active=1 THEN 1 ELSE 0 END) AS active
               FROM free_trial'''
        ).fetchone()
    return {'total': int(row['total'] or 0), 'used': int(row['used'] or 0), 'active': int(row['active'] or 0)}


def create_ticket(user_id: int) -> int:
    init_membership_support_db()
    now = _now_iso()
    with _lock, _conn() as conn:
        cur = conn.execute(
            'INSERT INTO tickets(user_id,status,created_at,updated_at) VALUES (?,\'open\',?,?)',
            (int(user_id), now, now)
        )
        conn.commit()
        return int(cur.lastrowid)


def get_ticket(ticket_id: int) -> dict[str, Any] | None:
    init_membership_support_db()
    with _conn() as conn:
        row = conn.execute('SELECT * FROM tickets WHERE id=?', (int(ticket_id),)).fetchone()
    return dict(row) if row else None


def list_tickets(*, status: str | None = None, user_id: int | None = None,
                 limit: int = 100) -> list[dict[str, Any]]:
    init_membership_support_db()
    where = []
    params: list[Any] = []
    if status in {'open', 'closed'}:
        where.append('status=?'); params.append(status)
    if user_id is not None:
        where.append('user_id=?'); params.append(int(user_id))
    sql = 'SELECT * FROM tickets'
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY id DESC LIMIT ?'; params.append(max(1, min(500, int(limit))))
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def ticket_count_for_user(user_id: int) -> int:
    init_membership_support_db()
    with _conn() as conn:
        row = conn.execute('SELECT COUNT(*) AS c FROM tickets WHERE user_id=?', (int(user_id),)).fetchone()
    return int(row['c'] or 0)


def ticket_counts() -> dict[str, int]:
    init_membership_support_db()
    with _conn() as conn:
        row = conn.execute(
            '''SELECT COUNT(*) AS total,
                      SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) AS open,
                      SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) AS closed
               FROM tickets'''
        ).fetchone()
    return {'total': int(row['total'] or 0), 'open': int(row['open'] or 0), 'closed': int(row['closed'] or 0)}


def add_ticket_message(ticket_id: int, *, sender_role: str, sender_id: int,
                       message_type: str, text: str | None,
                       telegram_message_id: int | None = None) -> int:
    if sender_role not in {'user', 'admin'}:
        raise ValueError('invalid sender_role')
    ticket = get_ticket(ticket_id)
    if not ticket:
        raise ValueError('ticket not found')
    with _lock, _conn() as conn:
        cur = conn.execute(
            '''INSERT INTO ticket_messages
               (ticket_id,sender_role,sender_id,message_type,text,telegram_message_id,created_at)
               VALUES (?,?,?,?,?,?,?)''',
            (int(ticket_id), sender_role, int(sender_id), message_type, text,
             telegram_message_id, _now_iso())
        )
        conn.execute('UPDATE tickets SET updated_at=? WHERE id=?', (_now_iso(), int(ticket_id)))
        conn.commit()
        return int(cur.lastrowid)


def close_ticket(ticket_id: int, admin_id: int) -> bool:
    now = _now_iso()
    with _lock, _conn() as conn:
        cur = conn.execute(
            '''UPDATE tickets SET status='closed',assigned_admin_id=?,updated_at=?,closed_at=?
               WHERE id=? AND status='open' ''',
            (int(admin_id), now, now, int(ticket_id))
        )
        conn.commit()
        return cur.rowcount == 1


def assign_ticket(ticket_id: int, admin_id: int) -> bool:
    with _lock, _conn() as conn:
        cur = conn.execute(
            'UPDATE tickets SET assigned_admin_id=?,updated_at=? WHERE id=?',
            (int(admin_id), _now_iso(), int(ticket_id))
        )
        conn.commit()
        return cur.rowcount == 1


def add_support_contact(*, target: str, target_id: int | None, username: str | None,
                        display_name: str | None, created_by: int) -> int:
    init_membership_support_db()
    normalized = (target or '').strip()
    if not normalized:
        raise ValueError('empty target')
    with _lock, _conn() as conn:
        conn.execute(
            '''INSERT INTO support_contacts(target,target_id,username,display_name,created_by,created_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(target) DO UPDATE SET target_id=excluded.target_id,username=excluded.username,
               display_name=excluded.display_name,created_by=excluded.created_by''',
            (normalized, target_id, (username or '').lstrip('@') or None,
             display_name or None, int(created_by), _now_iso())
        )
        row = conn.execute('SELECT id FROM support_contacts WHERE target=?', (normalized,)).fetchone()
        conn.commit()
        return int(row['id'])


def list_support_contacts() -> list[dict[str, Any]]:
    init_membership_support_db()
    with _conn() as conn:
        rows = conn.execute('SELECT * FROM support_contacts ORDER BY id').fetchall()
    return [dict(r) for r in rows]


def delete_support_contact(contact_id: int) -> bool:
    with _lock, _conn() as conn:
        cur = conn.execute('DELETE FROM support_contacts WHERE id=?', (int(contact_id),))
        conn.commit()
        return cur.rowcount == 1


def _permissions_json(permissions: Any) -> str:
    if permissions == '*':
        values = list(ADMIN_PERMISSION_KEYS)
    elif isinstance(permissions, str):
        try:
            values = json.loads(permissions)
        except Exception:
            values = [permissions]
    else:
        values = list(permissions or [])
    clean = [p for p in ADMIN_PERMISSION_KEYS if p in set(map(str, values))]
    return json.dumps(clean, ensure_ascii=False, separators=(',', ':'))


def upsert_admin(*, user_id: int | None, username: str | None, role: str,
                 permissions: Any) -> int:
    init_membership_support_db()
    if role not in {'full', 'limited'}:
        raise ValueError('invalid role')
    uname = (username or '').strip().lstrip('@').lower() or None
    if type(user_id) is not int or not 0 < user_id < (1 << 52):
        raise ValueError('a verified positive numeric admin ID is required')
    perm_json = _permissions_json('*' if role == 'full' else permissions)
    with _lock, _conn() as conn:
        # Prefer stable numeric ID when available; otherwise username-only row.
        row = None
        if user_id is not None:
            row = conn.execute('SELECT id FROM admins WHERE user_id=?', (int(user_id),)).fetchone()
        if uname:
            # A recycled username must never move another numeric ID's role.
            conn.execute('UPDATE admins SET username=NULL WHERE username=? AND user_id IS NOT NULL AND user_id!=?', (uname,user_id))
        if row is None and uname:
            row = conn.execute('SELECT id FROM admins WHERE username=? AND user_id IS NULL', (uname,)).fetchone()
        if row:
            conn.execute(
                'UPDATE admins SET user_id=?,username=?,role=?,permissions=? WHERE id=?',
                (user_id, uname, role, perm_json, int(row['id']))
            )
            admin_id = int(row['id'])
        else:
            cur = conn.execute(
                '''INSERT INTO admins(user_id,username,role,permissions,created_at)
                   VALUES (?,?,?,?,?)''',
                (user_id, uname, role, perm_json, _now_iso())
            )
            admin_id = int(cur.lastrowid)
        conn.commit()
        return admin_id


def list_admins() -> list[dict[str, Any]]:
    init_membership_support_db()
    with _conn() as conn:
        rows = conn.execute('SELECT * FROM admins ORDER BY id').fetchall()
    out = []
    for row in rows:
        d = dict(row)
        try:
            d['permissions_list'] = json.loads(d.get('permissions') or '[]')
        except Exception:
            d['permissions_list'] = []
        out.append(d)
    return out


def get_admin_by_user(user_id: int, username: str | None = None) -> dict[str, Any] | None:
    init_membership_support_db()
    with _conn() as conn:
        row = conn.execute('SELECT * FROM admins WHERE user_id=?', (int(user_id),)).fetchone()
        # Username is display metadata, never authorization. Legacy unbound
        # rows remain inactive until the owner re-adds a numeric user ID.
    if not row:
        return None
    d = dict(row)
    try:
        d['permissions_list'] = json.loads(d.get('permissions') or '[]')
    except Exception:
        d['permissions_list'] = []
    return d


def delete_admin(admin_id: int) -> bool:
    with _lock, _conn() as conn:
        cur = conn.execute('DELETE FROM admins WHERE id=?', (int(admin_id),))
        conn.commit()
        return cur.rowcount == 1


# Whitelist entries always use stable, signed Telegram peer IDs.
def whitelist_policy(user_id: int) -> tuple[bool, set[int]]:
    init_tabchi_db()
    with _conn() as conn:
        conn.execute('BEGIN')
        setting = conn.execute('SELECT enabled FROM tabchi_whitelist_settings WHERE user_id=?',(int(user_id),)).fetchone()
        allowed = {int(r[0]) for r in conn.execute('SELECT peer_id FROM tabchi_whitelist WHERE user_id=?',(int(user_id),))}
        return bool(setting and setting['enabled']), allowed


def set_whitelist_enabled(user_id: int, enabled: bool) -> None:
    if type(enabled) is not bool:
        raise ValueError('enabled must be boolean')
    init_tabchi_db()
    with _lock, _conn() as conn:
        conn.execute('INSERT INTO tabchi_whitelist_settings(user_id,enabled) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled',
                     (int(user_id),int(enabled)))


def add_whitelist_entries(user_id: int, entries: list[dict]) -> None:
    if not entries or len(entries)>20:
        raise ValueError('between 1 and 20 destinations are required')
    for entry in entries:
        peer_id=entry['peer_id']; kind=entry['kind']
        if type(peer_id) is not int or not 0<abs(peer_id)<(1<<63) or kind not in {'group','private'}:
            raise ValueError('invalid whitelist destination')
        if (kind=='private') != (peer_id>0):
            raise ValueError('destination kind does not match canonical peer ID')
    init_tabchi_db()
    with _lock, _conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        for entry in entries:
            conn.execute('''INSERT INTO tabchi_whitelist(user_id,peer_id,kind,title,created_at)
                VALUES (?,?,?,?,?) ON CONFLICT(user_id,peer_id) DO UPDATE SET title=excluded.title,kind=excluded.kind''',
                (int(user_id),entry['peer_id'],entry['kind'],str(entry.get('title') or '')[:200],_now_iso()))
        conn.execute('INSERT INTO tabchi_whitelist_settings(user_id,enabled) VALUES (?,1) ON CONFLICT(user_id) DO UPDATE SET enabled=1',(int(user_id),))


def list_whitelist(user_id: int) -> list[dict]:
    init_tabchi_db()
    with _conn() as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM tabchi_whitelist WHERE user_id=? ORDER BY id DESC',(int(user_id),))]


def delete_whitelist(entry_id: int, user_id: int) -> bool:
    init_tabchi_db()
    with _lock, _conn() as conn:
        result=conn.execute('DELETE FROM tabchi_whitelist WHERE id=? AND user_id=?',(int(entry_id),int(user_id)))
        # Removing the last destination never turns the filter off.
        return result.rowcount==1


# Each account owns its own saved collection, including shared document IDs.
def init_custom_emojis_db() -> None:
    from contextlib import closing
    os.makedirs(os.path.dirname(TABCHI_DB_PATH), exist_ok=True)
    with _lock, closing(_conn()) as conn, conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS custom_emojis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL CHECK(owner_id > 0),
                document_id INTEGER NOT NULL CHECK(document_id > 0),
                emoji TEXT,
                emoji_text TEXT,
                source_message_id INTEGER,
                created_at TEXT NOT NULL,
                UNIQUE(owner_id, document_id)
            );
            CREATE INDEX IF NOT EXISTS idx_custom_emojis_owner ON custom_emojis(owner_id, id);

            CREATE TABLE IF NOT EXISTS custom_emoji_flow (
                owner_id INTEGER NOT NULL,
                surface TEXT NOT NULL CHECK(surface IN ('self','bot')),
                step TEXT NOT NULL CHECK(step IN ('extract','test')),
                chat_id INTEGER,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(owner_id, surface)
            );
        ''')
        # Non-destructive migration from the pre-manager schema.
        columns = _column_names(conn, 'custom_emojis')
        if 'emoji' not in columns:
            conn.execute('ALTER TABLE custom_emojis ADD COLUMN emoji TEXT')
            columns.add('emoji')
        if 'emoji_text' not in columns:
            conn.execute('ALTER TABLE custom_emojis ADD COLUMN emoji_text TEXT')
            columns.add('emoji_text')
        if 'source_message_id' not in columns:
            conn.execute('ALTER TABLE custom_emojis ADD COLUMN source_message_id INTEGER')
        conn.execute(
            "UPDATE custom_emojis SET emoji=COALESCE(emoji, emoji_text, '') "
            "WHERE emoji IS NULL OR emoji=''"
        )
        conn.execute(
            "UPDATE custom_emojis SET emoji_text=COALESCE(emoji_text, emoji, '') "
            "WHERE emoji_text IS NULL OR emoji_text=''"
        )


def save_custom_emojis(owner_id: int, items: list[dict], source_message_id: int) -> int:
    from contextlib import closing
    from services.custom_emoji_service import parse_document_id
    if type(owner_id) is not int or not 0 < owner_id < (1 << 52):
        raise ValueError('invalid owner')
    if type(source_message_id) is not int or not 0 < source_message_id < (1 << 63):
        raise ValueError('invalid source message')
    if not items or len(items) > 4096:
        raise ValueError('invalid emoji collection')
    values = []
    for item in items:
        doc_id = parse_document_id(item['document_id'])
        emoji = item.get('emoji') or item.get('emoji_text')
        if not isinstance(emoji, str) or not emoji or len(emoji) > 4096:
            raise ValueError('invalid emoji text')
        values.append((owner_id, doc_id, emoji, emoji, source_message_id, _now_iso()))
    init_custom_emojis_db()
    with _lock, closing(_conn()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        before = conn.total_changes
        conn.executemany('''INSERT INTO custom_emojis
            (owner_id,document_id,emoji,emoji_text,source_message_id,created_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(owner_id,document_id) DO NOTHING''', values)
        return conn.total_changes - before


def custom_emoji_page(owner_id: int, page: int = 0) -> tuple[list[dict], int, int]:
    from contextlib import closing
    init_custom_emojis_db()
    with closing(_conn()) as conn, conn:
        conn.execute('BEGIN')
        total = conn.execute('SELECT COUNT(*) FROM custom_emojis WHERE owner_id=?', (int(owner_id),)).fetchone()[0]
        page = max(0, min(int(page), max(0, (total - 1) // 20)))
        rows = conn.execute('SELECT * FROM custom_emojis WHERE owner_id=? ORDER BY id DESC LIMIT 20 OFFSET ?',
                            (int(owner_id), page * 20)).fetchall()
        return [dict(row) for row in rows], total, page


def delete_custom_emoji(entry_id: int, owner_id: int) -> bool:
    from contextlib import closing
    init_custom_emojis_db()
    with _lock, closing(_conn()) as conn, conn:
        return conn.execute('DELETE FROM custom_emojis WHERE id=? AND owner_id=?',
                            (int(entry_id), int(owner_id))).rowcount == 1


def set_custom_emoji_flow(owner_id: int, surface: str, step: str,
                          chat_id: int | None = None) -> None:
    """Persist Custom Emoji Manager input state across inline/self runtimes."""
    init_custom_emojis_db()
    if surface not in {'self', 'bot'} or step not in {'extract', 'test'}:
        raise ValueError('invalid custom emoji flow')
    with _lock, _conn() as conn:
        conn.execute(
            '''INSERT INTO custom_emoji_flow(owner_id,surface,step,chat_id,updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(owner_id,surface) DO UPDATE SET
                 step=excluded.step, chat_id=excluded.chat_id,
                 updated_at=excluded.updated_at''',
            (int(owner_id), surface, step, chat_id, _now_iso()),
        )
        conn.commit()


def get_custom_emoji_flow(owner_id: int, surface: str) -> dict | None:
    init_custom_emojis_db()
    with _conn() as conn:
        row = conn.execute(
            'SELECT * FROM custom_emoji_flow WHERE owner_id=? AND surface=?',
            (int(owner_id), surface),
        ).fetchone()
    return dict(row) if row else None


def clear_custom_emoji_flow(owner_id: int, surface: str) -> None:
    init_custom_emojis_db()
    with _lock, _conn() as conn:
        conn.execute(
            'DELETE FROM custom_emoji_flow WHERE owner_id=? AND surface=?',
            (int(owner_id), surface),
        )
        conn.commit()


def admin_custom_emoji_page(page: int = 0) -> tuple[list[dict], int, int]:
    """Global admin view. Access control is enforced by the caller."""
    init_custom_emojis_db()
    with _conn() as conn:
        total = conn.execute('SELECT COUNT(*) FROM custom_emojis').fetchone()[0]
        page = max(0, min(int(page), max(0, (total - 1) // 20)))
        rows = conn.execute(
            'SELECT * FROM custom_emojis ORDER BY id DESC LIMIT 20 OFFSET ?',
            (page * 20,),
        ).fetchall()
    return [dict(row) for row in rows], total, page


def admin_delete_custom_emoji(entry_id: int) -> bool:
    init_custom_emojis_db()
    with _lock, _conn() as conn:
        cur = conn.execute('DELETE FROM custom_emojis WHERE id=?', (int(entry_id),))
        conn.commit()
        return cur.rowcount == 1

# ---------------------------------------------------------------------------
# Cryptocurrency live-price cache + persistent per-user favorites
# ---------------------------------------------------------------------------
_DEFAULT_CRYPTO_FAVORITES = ['BTC', 'ETH', 'TON', 'USDT', 'DOGS']


def init_crypto_db() -> None:
    """Create crypto tables without modifying existing application schemas."""
    os.makedirs(os.path.dirname(TABCHI_DB_PATH), exist_ok=True)
    with _lock, _conn() as conn:
        conn.executescript(
            '''
            CREATE TABLE IF NOT EXISTS crypto_cache (
                symbol TEXT PRIMARY KEY,
                price_usd REAL NOT NULL,
                price_usdt REAL NOT NULL,
                price_toman REAL NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS crypto_settings (
                user_id INTEGER PRIMARY KEY,
                favorite_coins TEXT NOT NULL DEFAULT '["BTC","ETH","TON","USDT","DOGS"]',
                created_at TEXT NOT NULL
            );
            '''
        )
        conn.commit()


def upsert_crypto_cache(*, symbol: str, price_usd: float, price_usdt: float,
                        price_toman: float, updated_at: int | None = None) -> None:
    init_crypto_db()
    ts = int(time.time() if updated_at is None else updated_at)
    with _lock, _conn() as conn:
        conn.execute(
            '''INSERT INTO crypto_cache(symbol,price_usd,price_usdt,price_toman,updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(symbol) DO UPDATE SET
                 price_usd=excluded.price_usd,
                 price_usdt=excluded.price_usdt,
                 price_toman=excluded.price_toman,
                 updated_at=excluded.updated_at''',
            (str(symbol).strip().upper(), float(price_usd), float(price_usdt),
             float(price_toman), ts),
        )
        conn.commit()


def get_crypto_cache(symbol: str, max_age: int = 30) -> dict | None:
    """Return cache only when fresh; stale market prices are never surfaced."""
    init_crypto_db()
    with _conn() as conn:
        row = conn.execute(
            'SELECT * FROM crypto_cache WHERE symbol=?',
            (str(symbol).strip().upper(),),
        ).fetchone()
    if not row:
        return None
    data = dict(row)
    if int(time.time()) - int(data['updated_at']) > max(0, int(max_age)):
        return None
    return data


def get_crypto_settings(user_id: int) -> dict:
    init_crypto_db()
    uid = int(user_id)
    with _lock, _conn() as conn:
        row = conn.execute(
            'SELECT * FROM crypto_settings WHERE user_id=?', (uid,)
        ).fetchone()
        if row is None:
            conn.execute(
                'INSERT INTO crypto_settings(user_id,favorite_coins,created_at) VALUES (?,?,?)',
                (uid, json.dumps(_DEFAULT_CRYPTO_FAVORITES), _now_iso()),
            )
            conn.commit()
            row = conn.execute(
                'SELECT * FROM crypto_settings WHERE user_id=?', (uid,)
            ).fetchone()
    data = dict(row)
    try:
        favorites = json.loads(data.get('favorite_coins') or '[]')
    except Exception:
        favorites = list(_DEFAULT_CRYPTO_FAVORITES)
    if not isinstance(favorites, list):
        favorites = list(_DEFAULT_CRYPTO_FAVORITES)
    cleaned = []
    for item in favorites:
        symbol = str(item).strip().upper()
        if symbol and symbol not in cleaned:
            cleaned.append(symbol)
    data['favorite_coins'] = cleaned[:10] or list(_DEFAULT_CRYPTO_FAVORITES)
    return data


def set_crypto_favorites(user_id: int, favorites: list[str]) -> list[str]:
    init_crypto_db()
    cleaned = []
    for item in favorites:
        symbol = str(item).strip().upper()
        if symbol and symbol not in cleaned:
            cleaned.append(symbol)
    if not 1 <= len(cleaned) <= 10:
        raise ValueError('favorites must contain 1..10 symbols')
    uid = int(user_id)
    payload = json.dumps(cleaned, ensure_ascii=False, separators=(',', ':'))
    with _lock, _conn() as conn:
        conn.execute(
            '''INSERT INTO crypto_settings(user_id,favorite_coins,created_at)
               VALUES (?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET favorite_coins=excluded.favorite_coins''',
            (uid, payload, _now_iso()),
        )
        conn.commit()
    return cleaned


# ---------------------------------------------------------------------------
# AI assistant / translation persistent storage
# ---------------------------------------------------------------------------
_DEFAULT_AI_LANGUAGE = 'Persian'


def init_ai_db() -> None:
    """Create AI tables without modifying legacy schemas."""
    os.makedirs(os.path.dirname(TABCHI_DB_PATH), exist_ok=True)
    with _lock, _conn() as conn:
        conn.executescript(
            '''
            CREATE TABLE IF NOT EXISTS ai_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user','assistant')),
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ai_history_user
            ON ai_history(user_id, id DESC);

            CREATE TABLE IF NOT EXISTS ai_user_settings (
                user_id INTEGER PRIMARY KEY,
                default_language TEXT NOT NULL DEFAULT 'Persian',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ai_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ai_cache (
                cache_key TEXT PRIMARY KEY,
                response TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ai_cache_expiry
            ON ai_cache(expires_at);
            '''
        )
        conn.commit()


def add_ai_history(user_id: int, role: str, message: str) -> int:
    init_ai_db()
    role = str(role).strip().lower()
    if role not in {'user', 'assistant'}:
        raise ValueError('invalid AI history role')
    text = str(message or '').strip()
    if not text:
        raise ValueError('empty AI history message')
    with _lock, _conn() as conn:
        cur = conn.execute(
            'INSERT INTO ai_history(user_id,role,message,created_at) VALUES (?,?,?,?)',
            (int(user_id), role, text, _now_iso()),
        )
        conn.commit()
        return int(cur.lastrowid)


def add_ai_exchange(user_id: int, user_message: str, assistant_message: str, keep: int = 20) -> None:
    """Persist one complete user/assistant exchange atomically and prune history."""
    init_ai_db()
    uid = int(user_id)
    user_text = str(user_message or '').strip()
    assistant_text = str(assistant_message or '').strip()
    if not user_text or not assistant_text:
        raise ValueError('empty AI exchange message')
    keep = max(2, min(20, int(keep)))
    now = _now_iso()
    with _lock, _conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute(
            'INSERT INTO ai_history(user_id,role,message,created_at) VALUES (?,?,?,?)',
            (uid, 'user', user_text, now),
        )
        conn.execute(
            'INSERT INTO ai_history(user_id,role,message,created_at) VALUES (?,?,?,?)',
            (uid, 'assistant', assistant_text, now),
        )
        conn.execute(
            '''DELETE FROM ai_history
               WHERE user_id=? AND id NOT IN (
                 SELECT id FROM ai_history
                 WHERE user_id=?
                 ORDER BY id DESC
                 LIMIT ?
               )''',
            (uid, uid, keep),
        )
        conn.commit()


def get_ai_history(user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    init_ai_db()
    limit = max(1, min(20, int(limit)))
    with _conn() as conn:
        rows = conn.execute(
            '''SELECT id,user_id,role,message,created_at
               FROM ai_history
               WHERE user_id=?
               ORDER BY id DESC
               LIMIT ?''',
            (int(user_id), limit),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def prune_ai_history(user_id: int, keep: int = 20) -> int:
    init_ai_db()
    keep = max(0, min(20, int(keep)))
    uid = int(user_id)
    with _lock, _conn() as conn:
        if keep == 0:
            cur = conn.execute('DELETE FROM ai_history WHERE user_id=?', (uid,))
        else:
            cur = conn.execute(
                '''DELETE FROM ai_history
                   WHERE user_id=? AND id NOT IN (
                     SELECT id FROM ai_history
                     WHERE user_id=?
                     ORDER BY id DESC
                     LIMIT ?
                   )''',
                (uid, uid, keep),
            )
        conn.commit()
        return int(cur.rowcount)


def clear_ai_history(user_id: int) -> int:
    init_ai_db()
    with _lock, _conn() as conn:
        cur = conn.execute('DELETE FROM ai_history WHERE user_id=?', (int(user_id),))
        conn.commit()
        return int(cur.rowcount)


def count_ai_history(user_id: int) -> int:
    init_ai_db()
    with _conn() as conn:
        row = conn.execute(
            'SELECT COUNT(*) AS c FROM ai_history WHERE user_id=?',
            (int(user_id),),
        ).fetchone()
    return int(row['c']) if row else 0


def get_ai_user_settings(user_id: int) -> dict[str, Any]:
    init_ai_db()
    uid = int(user_id)
    now = _now_iso()
    with _lock, _conn() as conn:
        row = conn.execute(
            'SELECT * FROM ai_user_settings WHERE user_id=?', (uid,)
        ).fetchone()
        if row is None:
            conn.execute(
                '''INSERT INTO ai_user_settings(user_id,default_language,created_at,updated_at)
                   VALUES (?,?,?,?)''',
                (uid, _DEFAULT_AI_LANGUAGE, now, now),
            )
            conn.commit()
            row = conn.execute(
                'SELECT * FROM ai_user_settings WHERE user_id=?', (uid,)
            ).fetchone()
    return dict(row)


def set_ai_default_language(user_id: int, language: str) -> str:
    init_ai_db()
    value = ' '.join(str(language or '').strip().split())
    if not value or len(value) > 80:
        raise ValueError('invalid language')
    uid = int(user_id)
    now = _now_iso()
    with _lock, _conn() as conn:
        conn.execute(
            '''INSERT INTO ai_user_settings(user_id,default_language,created_at,updated_at)
               VALUES (?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 default_language=excluded.default_language,
                 updated_at=excluded.updated_at''',
            (uid, value, now, now),
        )
        conn.commit()
    return value


def get_ai_config(key: str, default: str = '') -> str:
    init_ai_db()
    with _conn() as conn:
        row = conn.execute(
            'SELECT value FROM ai_config WHERE key=?', (str(key),)
        ).fetchone()
    return str(row['value']) if row else str(default)


def set_ai_config(key: str, value: str) -> None:
    init_ai_db()
    k = str(key or '').strip()
    if not k:
        raise ValueError('empty config key')
    v = str(value or '').strip()
    with _lock, _conn() as conn:
        conn.execute(
            '''INSERT INTO ai_config(key,value,updated_at)
               VALUES (?,?,?)
               ON CONFLICT(key) DO UPDATE SET
                 value=excluded.value,
                 updated_at=excluded.updated_at''',
            (k, v, _now_iso()),
        )
        conn.commit()


def get_ai_system_prompt(default_prompt: str) -> str:
    prompt = get_ai_config('system_prompt', '').strip()
    return prompt or str(default_prompt).strip()


def set_ai_system_prompt(prompt: str) -> str:
    value = str(prompt or '').strip()
    if len(value) < 10 or len(value) > 8000:
        raise ValueError('system prompt length must be 10..8000')
    set_ai_config('system_prompt', value)
    return value


def get_ai_cache(cache_key: str) -> str | None:
    init_ai_db()
    key = str(cache_key)
    now = int(time.time())
    with _lock, _conn() as conn:
        conn.execute('DELETE FROM ai_cache WHERE expires_at<=?', (now,))
        row = conn.execute(
            'SELECT response FROM ai_cache WHERE cache_key=? AND expires_at>?',
            (key, now),
        ).fetchone()
        conn.commit()
    return str(row['response']) if row else None


def set_ai_cache(cache_key: str, response: str, ttl_seconds: int) -> None:
    init_ai_db()
    now = int(time.time())
    ttl = max(1, int(ttl_seconds))
    with _lock, _conn() as conn:
        conn.execute(
            '''INSERT INTO ai_cache(cache_key,response,created_at,expires_at)
               VALUES (?,?,?,?)
               ON CONFLICT(cache_key) DO UPDATE SET
                 response=excluded.response,
                 created_at=excluded.created_at,
                 expires_at=excluded.expires_at''',
            (str(cache_key), str(response), now, now + ttl),
        )
        conn.commit()
