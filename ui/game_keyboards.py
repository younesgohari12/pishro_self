"""Inline keyboards for game messages; callback data carries IDs only."""
from __future__ import annotations

from ui import inline_button


def open_game_keyboard(game_id: int):
    gid = int(game_id)
    return [
        [inline_button('⚔️ پیوستن به بازی', f'game_join_{gid}', 'success')],
        [inline_button('❌ لغو بازی', f'game_cancel_{gid}', 'danger')],
    ]


def result_keyboard(game_id: int):
    gid = int(game_id)
    return [[
        inline_button('💎 موجودی برنده', f'game_balance_{gid}_winner', 'success'),
        inline_button('💎 موجودی بازنده', f'game_balance_{gid}_loser', 'primary'),
    ]]


def closed_game_keyboard():
    return None
