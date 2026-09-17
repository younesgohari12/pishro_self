from __future__ import annotations

import asyncio

import ui
from database import models
from services import admin_manager, ticket_manager


class SupportController:
    def __init__(self, bot):
        self.bot = bot
        self.states: dict[int, dict] = {}

    def has_state(self, user_id: int) -> bool:
        return int(user_id) in self.states

    def render_home(self):
        return (
            '🎧 **پشتیبانی**\n\nروش ارتباط را انتخاب کنید:',
            [
                [ui.inline_button('🎫 ارسال تیکت', b'support_ticket', 'primary')],
                [ui.inline_button('📞 پشتیبانی مستقیم', b'support_direct', 'success')],
                [ui.inline_button('↩️ بازگشت', b'main_menu', 'secondary')],
            ],
        )

    def render_direct(self):
        contacts = models.list_support_contacts()
        text = '📞 **پشتیبانی مستقیم**\n\n'
        buttons = []
        if not contacts:
            text += 'در حال حاضر پشتیبان مستقیمی ثبت نشده است.'
        else:
            text += 'یکی از پشتیبان‌ها را انتخاب کنید:'
            for i, row in enumerate(contacts, 1):
                uname = (row.get('username') or '').lstrip('@')
                target_id = row.get('target_id')
                if uname:
                    url = f'https://t.me/{uname}'
                elif target_id:
                    url = f'tg://user?id={int(target_id)}'
                else:
                    continue
                buttons.append([ui.url_button(f'💬 پشتیبانی {i}', url, 'primary')])
        buttons.append([ui.inline_button('↩️ پشتیبانی', b'support_menu', 'secondary')])
        return text, buttons

    async def handle_callback(self, event, data: str) -> bool:
        uid = int(event.sender_id)
        if data == 'support_menu':
            self.states.pop(uid, None)
            text, buttons = self.render_home()
            await event.edit(text, buttons=buttons, parse_mode='md')
            return True
        if data == 'support_ticket':
            self.states[uid] = {'step': 'WAIT_TICKET'}
            await event.edit(
                '🎫 **ارسال تیکت**\n\nپیام خود را ارسال کنید.\n\n'
                'متن، عکس، فیلم، ویس، فایل و سایر پیام‌های تلگرام پشتیبانی می‌شوند.',
                buttons=[[ui.inline_button('❌ لغو', b'support_menu', 'danger')]],
                parse_mode='md',
            )
            return True
        if data == 'support_direct':
            text, buttons = self.render_direct()
            await event.edit(text, buttons=buttons, parse_mode='md')
            return True
        return False

    async def handle_message(self, event) -> bool:
        uid = int(event.sender_id)
        state = self.states.get(uid)
        if not state or state.get('step') != 'WAIT_TICKET':
            return False

        msg = event.message
        raw_text = (getattr(msg, 'raw_text', '') or '').strip()
        if not raw_text and not getattr(msg, 'media', None):
            await event.reply('❌ لطفاً یک پیام یا فایل معتبر ارسال کنید.')
            return True

        sender = await event.get_sender()
        models.touch_profile(
            uid,
            username=getattr(sender, 'username', '') or '',
            first_name=getattr(sender, 'first_name', '') or '',
            last_name=getattr(sender, 'last_name', '') or '',
        )
        ticket_id = models.create_ticket(uid)
        message_type = ticket_manager.detect_message_type(msg)
        models.add_ticket_message(
            ticket_id,
            sender_role='user',
            sender_id=uid,
            message_type=message_type,
            text=raw_text or None,
            telegram_message_id=getattr(msg, 'id', None),
        )

        header = ticket_manager.ticket_header(ticket_id, uid)
        delivered = 0
        for admin_id in admin_manager.ticket_recipient_ids():
            perms = admin_manager.permissions_for(admin_id)
            action_row = []
            if 'answer_tickets' in perms:
                action_row.append(ui.inline_button('✅ پاسخ', f'adm2_treply_{ticket_id}', 'success'))
            if 'manage_tickets' in perms:
                action_row.append(ui.inline_button('❌ بستن تیکت', f'adm2_tclose_{ticket_id}', 'danger'))
            buttons = [action_row] if action_row else None
            try:
                await self.bot.send_message(admin_id, header, buttons=buttons, parse_mode='md')
                try:
                    await self.bot.forward_messages(admin_id, msg)
                except Exception:
                    if raw_text:
                        await self.bot.send_message(admin_id, raw_text)
                delivered += 1
            except Exception:
                pass
            await asyncio.sleep(0.03)

        self.states.pop(uid, None)
        await event.reply(
            f'✅ **تیکت شما ثبت شد.**\n\n🎫 شماره تیکت: `#{ticket_id}`\n'
            'پس از پاسخ مدیر، پیام در همین ربات برای شما ارسال می‌شود.',
            buttons=[[ui.inline_button('↩️ پشتیبانی', b'support_menu', 'secondary')]],
            parse_mode='md',
        )
        return True
