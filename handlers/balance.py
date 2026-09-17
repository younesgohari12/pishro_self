"""The Persian `موجودی` command, scoped to the configured game group."""
from __future__ import annotations

import asyncio

import db
from services import balance_service, game_service
from ui.game_messages import balance_card


class BalanceController:
    def __init__(self, bot):
        self.bot = bot

    async def handle_message(self, event) -> bool:
        if (event.raw_text or '').strip() != 'موجودی':
            return False
        chat = await event.get_chat()
        if not game_service.chat_matches_setting(event.chat_id, getattr(chat, 'username', None)):
            return False
        sender = await event.get_sender()
        uid = int(event.sender_id)
        legacy = db.get_user_settings(uid)
        user = balance_service.ensure_user(
            uid,
            username=getattr(sender, 'username', '') or '',
            first_name=getattr(sender, 'first_name', '') or '',
            initial_balance=int(legacy.get('diamonds') or 0),
        )
        reply = await event.reply(
            balance_card(
                username=user.get('username'), first_name=user.get('first_name'),
                user_id=uid, diamonds=int(user['diamonds']),
            ),
            parse_mode='md',
        )
        asyncio.create_task(self._delete_later(reply))
        return True

    @staticmethod
    async def _delete_later(message) -> None:
        await asyncio.sleep(10)
        try:
            await message.delete()
        except Exception:
            pass
