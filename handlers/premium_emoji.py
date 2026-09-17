"""Private, owner-scoped premium emoji workflows in the main bot."""
from __future__ import annotations

import asyncio
import re
import secrets
import time
from telethon import errors
from database import models
from services.logging_service import log_api_error, log_db_error, log_user_action
from services.custom_emoji_service import (
    EmojiError, extract_custom_emojis, result_pages, parse_document_id,
    resolve_custom_emoji, send_custom_emoji,
)
from ui.keyboards import (
    premium_emoji_menu, premium_emoji_back, premium_emoji_save, premium_emoji_list,
)

PROMPT = '✨ پیام دارای ایموجی پرمیوم را ارسال کنید.'
MENU_TEXT = (
    '✨ **Custom Emoji Manager**\n\n'
    'استخراج Entity واقعی، تست Document ID و مدیریت ایموجی‌های ذخیره‌شده.'
)


class PremiumEmojiController:
    def __init__(self, bot):
        self.bot = bot
        self.states = {}

    def has_state(self, uid):
        return int(uid) in self.states

    def cancel(self, uid):
        self.states.pop(int(uid), None)

    async def _list(self, event, page=0, *, deleting=False):
        rows, total, page = models.custom_emoji_page(int(event.sender_id), page)
        text = f'📦 **ایموجی‌های من**\n\nتعداد ذخیره‌شده: **{total}**\n\n'
        if rows:
            text += '\n'.join(f"▫️ `{row['document_id']}`" for row in rows)
            text += f'\n\nصفحهٔ {page + 1} از {max(1, (total + 19) // 20)}'
            if deleting:
                text += '\nبرای حذف، دکمهٔ شناسهٔ موردنظر را بزنید.'
        else:
            text += 'هنوز ایموجی ذخیره نکرده‌اید.'
        await event.edit(text, buttons=premium_emoji_list(rows, page, total, deleting=deleting), parse_mode='md')

    async def handle_callback(self, event, data):
        if not data.startswith('em_'):
            return False
        uid = int(event.sender_id)
        if event.chat_id != uid:
            await event.answer('این بخش فقط در گفتگوی خصوصی ربات در دسترس است.', alert=True)
            return True
        if data == 'em_menu':
            self.cancel(uid)
            await event.edit(MENU_TEXT, buttons=premium_emoji_menu(), parse_mode='md')
        elif data == 'em_extract':
            self.states[uid] = {'step': 'extract'}
            await event.edit(PROMPT, buttons=premium_emoji_back(), parse_mode=None)
        elif data == 'em_test':
            self.states[uid] = {'step': 'test'}
            await event.edit('🧪 document_id ایموجی را وارد کنید.\nمثال: 5354913105224675749',
                             buttons=premium_emoji_back(), parse_mode=None)
        elif data.startswith('em_save_'):
            state = self.states.get(uid)
            pending = state.get('pending') if state else None
            token = data.removeprefix('em_save_')
            if (not pending or token != pending['token']
                    or time.monotonic() > pending['expires']):
                await event.answer('این نتیجه منقضی شده؛ پیام را دوباره برای استخراج بفرستید.', alert=True)
                return True
            try:
                count = models.save_custom_emojis(uid, pending['items'], pending['source_message_id'])
            except Exception as exc:
                log_db_error('custom_emoji_save', exc, user_id=uid)
                await event.answer('ذخیره انجام نشد؛ دوباره تلاش کنید.', alert=True)
                return True
            self.cancel(uid)
            log_user_action(uid, 'custom_emoji_save', count=count)
            await event.edit(f'✅ {count} ایموجی جدید ذخیره شد.\nشناسه‌های تکراری دوباره ذخیره نمی‌شوند.',
                             buttons=premium_emoji_menu(), parse_mode=None)
        else:
            listing = re.fullmatch(r'em_(list|delete)_([0-9]{1,8})', data)
            deletion = re.fullmatch(r'em_remove_([0-9]{1,18})_([0-9]{1,8})', data)
            if listing:
                self.cancel(uid)
                await self._list(event, int(listing[2]), deleting=listing[1] == 'delete')
            elif deletion:
                self.cancel(uid)
                deleted = models.delete_custom_emoji(int(deletion[1]), uid)
                await event.answer('حذف شد.' if deleted else 'این ایموجی در لیست شما نیست.')
                await self._list(event, int(deletion[2]), deleting=True)
            else:
                await event.answer('دکمه نامعتبر است.', alert=True)
        return True

    async def handle_message(self, event):
        uid = int(event.sender_id)
        state = self.states.get(uid)
        if state is None or event.chat_id != uid:
            return False
        text = (getattr(event, 'raw_text', '') or '').strip()
        if text == 'لغو':
            self.cancel(uid)
            await event.reply('عملیات لغو شد.', buttons=premium_emoji_menu(), parse_mode=None)
            return True
        if state['step'] == 'extract':
            message = getattr(event, 'message', None)
            items = extract_custom_emojis(message)
            state.pop('pending', None)
            if not items:
                await event.reply('❌ در این پیام Custom Emoji پیدا نشد.', buttons=premium_emoji_menu(), parse_mode=None)
                return True
            source_id = getattr(message, 'id', None)
            if type(source_id) is not int or source_id <= 0:
                await event.reply('شناسهٔ پیام قابل‌شناسایی نیست؛ دوباره ارسال کنید.')
                return True
            pending = {'items': items, 'source_message_id': source_id,
                       'token': secrets.token_hex(8), 'expires': time.monotonic() + 1800}
            state['pending'] = pending
            pages = result_pages(items)
            for index, page in enumerate(pages):
                if self.states.get(uid) is not state or state.get('pending') is not pending:
                    break
                buttons = premium_emoji_save(pending['token']) if index == len(pages) - 1 else None
                await event.reply(page, buttons=buttons, parse_mode='md')
            return True
        if state['step'] == 'test':
            if state.get('busy'):
                await event.reply('تست قبلی هنوز در حال بررسی است.')
                return True
            state['busy'] = True
            try:
                doc_id = parse_document_id(text)
                payload = await resolve_custom_emoji(self.bot, doc_id)
                if self.states.get(uid) is not state:
                    return True
                await send_custom_emoji(payload, client=self.bot, peer=uid, timeout=20)
            except EmojiError as exc:
                if self.states.get(uid) is state:
                    await event.reply(str(exc), buttons=premium_emoji_back(), parse_mode=None)
            except errors.FloodWaitError as exc:
                log_api_error('custom_emoji_test_flood_wait', exc, user_id=uid)
                if self.states.get(uid) is state:
                    await event.reply(f'⏳ تلگرام تست را موقتاً محدود کرده است؛ {exc.seconds} ثانیه بعد دوباره امتحان کنید.',
                                      buttons=premium_emoji_back(), parse_mode=None)
            except asyncio.TimeoutError as exc:
                log_api_error('custom_emoji_test_timeout', exc, user_id=uid)
                if self.states.get(uid) is state:
                    await event.reply('پاسخ تلگرام به‌موقع دریافت نشد؛ ابتدا گفتگو را برای رسیدن پیام تست بررسی کنید.',
                                      buttons=premium_emoji_back(), parse_mode=None)
            except Exception as exc:
                log_api_error('custom_emoji_test', exc, user_id=uid)
                if self.states.get(uid) is state:
                    await event.reply('❌ تست انجام نشد؛ شناسه یا دسترسی ربات به ارسال این ایموجی را بررسی کنید.',
                                      buttons=premium_emoji_back(), parse_mode=None)
            finally:
                state.pop('busy', None)
            return True
        return False
