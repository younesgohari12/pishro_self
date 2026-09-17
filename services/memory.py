"""Persistent per-user AI conversation memory backed by SQLite."""
from __future__ import annotations

from database import models


def get_history(user_id: int, limit: int = 20) -> list[dict[str, str]]:
    rows = models.get_ai_history(int(user_id), limit=max(1, min(20, int(limit))))
    return [{'role': row['role'], 'content': row['message']} for row in rows]


def remember_exchange(user_id: int, user_message: str, assistant_message: str) -> None:
    # Keep each conversational turn contiguous even if several Telegram updates
    # arrive concurrently for the same account.
    models.add_ai_exchange(int(user_id), user_message, assistant_message, keep=20)


def clear_history(user_id: int) -> int:
    return models.clear_ai_history(int(user_id))


def count_messages(user_id: int) -> int:
    return models.count_ai_history(int(user_id))
