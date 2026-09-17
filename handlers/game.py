"""Telegram event adapter for the atomic game service."""
from __future__ import annotations

import asyncio
import db
from database import models
from services import balance_service, game_service
from services.game_parser import parse_bet
from ui.game_keyboards import closed_game_keyboard, open_game_keyboard, result_keyboard
from ui.game_messages import cancelled_card, expired_card, fmt, open_game_card, result_card


def callback_message_id(event) -> int:
    value = getattr(event, 'message_id', None)
    if value is None:
        value = getattr(getattr(event, 'original_update', None), 'msg_id', 0)
    return int(value or 0)


class GameController:
    def __init__(self, bot):
        self.bot = bot

    async def handle_message(self, event) -> bool:
        bet = parse_bet(event.raw_text or '')
        if bet is None:
            return False
        chat = await event.get_chat()
        if not game_service.chat_matches_setting(event.chat_id, getattr(chat, 'username', None)):
            return False
        settings = models.get_game_settings()
        if not int(settings.get('game_enabled') or 0):
            await event.reply('⛔️ بازی در حال حاضر غیرفعال است.')
            return True
        if bet < int(settings['min_bet']) or bet > int(settings['max_bet']):
            await event.reply(
                '❌ **مقدار ورودی مجاز نیست.**\n\n'
                f"حداقل: **{fmt(settings['min_bet'])}**\n"
                f"حداکثر: **{fmt(settings['max_bet'])} الماس**",
                parse_mode='md',
            )
            return True
        sender = await event.get_sender()
        uid = int(event.sender_id)
        legacy = db.get_user_settings(uid)
        balance_service.ensure_user(
            uid, username=getattr(sender, 'username', '') or '',
            first_name=getattr(sender, 'first_name', '') or '',
            initial_balance=int(legacy.get('diamonds') or 0),
        )
        placeholder = await event.reply('🎮 در حال ساخت نبرد الماس...')
        try:
            game = game_service.create_game(
                chat_id=int(event.chat_id), message_id=int(placeholder.id), creator_id=uid,
                creator_username=getattr(sender, 'username', '') or '', bet_amount=bet,
            )
        except game_service.InsufficientBalance as exc:
            await placeholder.edit(
                '❌ **موجودی شما کافی نیست.**\n\n'
                f'💎 ورودی مورد نیاز: **{fmt(exc.required)}**\n'
                f'💎 موجودی شما: **{fmt(exc.balance)}**', parse_mode='md'
            )
            return True
        except game_service.ActiveGameExists:
            await placeholder.edit('❌ شما یک بازی فعال دارید؛ ابتدا آن را تمام یا لغو کنید.')
            return True
        except game_service.GameDisabled:
            await placeholder.edit('⛔️ بازی در حال حاضر غیرفعال است.')
            return True
        except game_service.InvalidBet:
            await placeholder.edit('❌ مقدار ورودی معتبر نیست.')
            return True
        except Exception:
            await placeholder.edit('❌ ساخت بازی ناموفق بود؛ هیچ الماسی کسر نشد.')
            return True
        try:
            await placeholder.edit(open_game_card(game), buttons=open_game_keyboard(game['id']), parse_mode='md')
        except Exception:
            try:
                game_service.cancel_game(
                    game_id=game['id'], creator_id=uid,
                    chat_id=int(event.chat_id), message_id=int(placeholder.id),
                )
            finally:
                await placeholder.edit('❌ ساخت پیام بازی ناموفق بود؛ مبلغ کامل بازگردانده شد.')
        return True

    async def handle_callback(self, event, data: str) -> bool:
        if not data.startswith('game_'):
            return False
        parts = data.split('_')
        if len(parts) < 3 or not parts[2].isdigit():
            await event.answer('❌ درخواست نامعتبر است.', alert=True)
            return True
        action, game_id = parts[1], int(parts[2])
        chat_id = int(event.chat_id)
        message_id = callback_message_id(event)
        sender = await event.get_sender()
        uid = int(event.sender_id)
        if action == 'join':
            legacy = db.get_user_settings(uid)
            balance_service.ensure_user(
                uid, username=getattr(sender, 'username', '') or '',
                first_name=getattr(sender, 'first_name', '') or '',
                initial_balance=int(legacy.get('diamonds') or 0),
            )
            try:
                game = game_service.join_game(
                    game_id=game_id, opponent_id=uid,
                    opponent_username=getattr(sender, 'username', '') or '',
                    chat_id=chat_id, message_id=message_id,
                )
            except game_service.OwnGame:
                await event.answer('❌ شما نمی‌توانید وارد بازی خودتان شوید.', alert=True)
                return True
            except game_service.InsufficientBalance as exc:
                await event.answer(
                    '❌ موجودی شما کافی نیست.\n\n'
                    f'💎 ورودی مورد نیاز: {fmt(exc.required)}\n'
                    f'💎 موجودی شما: {fmt(exc.balance)}', alert=True
                )
                return True
            except (game_service.GameUnavailable, game_service.CallbackMismatch):
                await event.answer('❌ این بازی قبلاً شروع شده یا در دسترس نیست.', alert=True)
                return True
            await event.edit(result_card(game), buttons=result_keyboard(game_id), parse_mode='md')
            await event.answer('🎉 نتیجه بازی مشخص شد.')
            return True
        if action == 'cancel':
            try:
                game = game_service.cancel_game(
                    game_id=game_id, creator_id=uid, chat_id=chat_id, message_id=message_id
                )
            except game_service.NotCreator:
                await event.answer('❌ فقط سازنده می‌تواند بازی را لغو کند.', alert=True)
                return True
            except (game_service.GameUnavailable, game_service.CallbackMismatch):
                await event.answer('❌ این بازی دیگر قابل لغو نیست.', alert=True)
                return True
            await event.edit(cancelled_card(game), buttons=closed_game_keyboard(), parse_mode='md')
            await event.answer('✅ بازی لغو و مبلغ بازگردانده شد.')
            return True
        if action == 'balance' and len(parts) == 4 and parts[3] in {'winner', 'loser'}:
            game = game_service.get_game(game_id)
            if not game or game['status'] != 'FINISHED':
                await event.answer('❌ نتیجه بازی در دسترس نیست.', alert=True)
                return True
            target = int(game['winner_id'] if parts[3] == 'winner' else game['loser_id'])
            current = balance_service.get_balance(target)
            label = 'برنده' if parts[3] == 'winner' else 'بازنده'
            await event.answer(f'💎 موجودی {label}: {fmt(current)} الماس', alert=True)
            return True
        await event.answer('❌ درخواست نامعتبر است.', alert=True)
        return True

    async def run_expiration_scheduler(self) -> None:
        print('✅ زمان‌بند انقضای بازی فعال شد')
        while True:
            try:
                for game in game_service.expire_due_games(limit=100):
                    try:
                        await self.bot.edit_message(
                            int(game['chat_id']), int(game['message_id']), expired_card(game),
                            buttons=closed_game_keyboard(), parse_mode='md',
                        )
                    except Exception:
                        pass
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f'⚠️ game scheduler: {type(exc).__name__}: {exc}')
                await asyncio.sleep(10)
