from __future__ import annotations

import time
from typing import Any

from telethon.tl.types import Channel, Chat, User
from telethon.utils import get_peer_id

from services.whitelist_service import WhitelistError, resolve_entries

import self_manager
import ui
from database import models
from handlers.panel import (
    preview_summary,
    render_banner_list,
    render_banner_manage,
    render_blacklist,
    render_whitelist,
    render_tabchi_home,
    render_tabchi_settings,
    render_target_selector,
)


class TabchiController:
    """Conversation/controller layer for Tabchi inside the main Telegram bot."""

    def __init__(self, bot, bot_username: str, has_self_session):
        self.bot = bot
        self.bot_username = (bot_username or '').lstrip('@')
        self.has_self_session = has_self_session
        self.states: dict[int, dict[str, Any]] = {}

    def has_state(self, user_id: int) -> bool:
        return int(user_id) in self.states

    async def show_menu(self, event, *, edit: bool = True):
        self.states.pop(int(event.sender_id), None)
        text, buttons = render_tabchi_home(int(event.sender_id))
        if edit:
            try:
                await event.edit(text, buttons=buttons, parse_mode='md')
                return
            except Exception:
                pass
        await self.bot.send_message(event.chat_id, text, buttons=buttons, parse_mode='md')

    async def send_menu(self, chat_id: int, user_id: int):
        self.states.pop(int(user_id), None)
        text, buttons = render_tabchi_home(int(user_id))
        await self.bot.send_message(chat_id, text, buttons=buttons, parse_mode='md')

    async def start_create(self, user_id: int, chat_id: int):
        uid = int(user_id)
        if not self.has_self_session(uid):
            await self.bot.send_message(
                chat_id,
                '⛔️ **برای استفاده از تبچی ابتدا سلف‌بات را به همین حساب متصل کن.**',
                buttons=[[ui.inline_button('🚀 اتصال سلف', b'install_self', 'primary')]],
                parse_mode='md',
            )
            return
        self.states[uid] = {'step': 'create_content', 'draft': {}}
        await self.bot.send_message(
            chat_id,
            '📢 **محتوای بنر را ارسال کنید.**\n\n'
            'پشتیبانی: متن، عکس + کپشن، ویدیو + کپشن، ویس، فایل و گیف.',
            buttons=[[ui.inline_button('❌ لغو', b'tb_cancel', 'danger')]],
            parse_mode='md',
        )

    def _content_from_event(self, event):
        message = event.message
        text = (event.raw_text or event.text or '').strip()
        if getattr(message, 'voice', None):
            typ = 'voice'
        elif getattr(message, 'gif', None):
            typ = 'gif'
        elif getattr(message, 'video', None):
            typ = 'video'
        elif getattr(message, 'photo', None):
            typ = 'photo'
        elif getattr(message, 'document', None):
            typ = 'file'
        elif text:
            typ = 'text'
        else:
            return None

        file_id = None
        if typ != 'text':
            obj = getattr(message, 'document', None) or getattr(message, 'photo', None)
            raw = getattr(obj, 'id', None) if obj is not None else None
            file_id = str(raw) if raw is not None else None

        return {
            'type': typ,
            'file_id': file_id,
            'text': text if typ == 'text' else None,
            'caption': text if typ != 'text' else None,
            'source_peer': self.bot_username,
            'source_message_id': int(event.id),
        }

    async def handle_message(self, event) -> bool:
        uid = int(event.sender_id)
        state = self.states.get(uid)
        if not state:
            return False

        step = state.get('step')
        text = (event.raw_text or event.text or '').strip()

        if step == 'create_content':
            content = self._content_from_event(event)
            if not content:
                await event.reply('❌ نوع محتوا پشتیبانی نمی‌شود. متن/عکس/ویدیو/ویس/فایل/گیف بفرست.')
                return True
            state['draft'] = content
            state['step'] = 'create_name'
            await event.reply(
                '📝 **نام بنر:**\n\nمثال: `تبلیغ صبح`',
                buttons=[[ui.inline_button('❌ لغو', b'tb_cancel', 'danger')]],
                parse_mode='md',
            )
            return True

        if step == 'create_name':
            if not text:
                await event.reply('❌ نام بنر نمی‌تواند خالی باشد.')
                return True
            state['draft']['name'] = text[:100]
            state['draft']['send_target'] = []
            state['step'] = 'create_targets'
            await self._send_draft_targets(event.chat_id, state['draft'])
            return True

        if step == 'create_interval':
            minutes = self._parse_interval(text)
            if minutes is None:
                await event.reply('❌ فاصله باید عددی بین 1 تا 10080 دقیقه باشد. مثال: `10`', parse_mode='md')
                return True
            state['draft']['interval'] = minutes
            state['step'] = 'create_confirm'
            await self._show_draft_confirmation(event.chat_id, state['draft'])
            return True

        if step == 'draft_replace_content':
            content = self._content_from_event(event)
            if not content:
                await event.reply('❌ نوع محتوا پشتیبانی نمی‌شود.')
                return True
            old = state.get('draft', {})
            for key in ('name', 'send_target', 'send_mode', 'interval'):
                if key in old:
                    content[key] = old[key]
            state['draft'] = content
            state['step'] = 'create_confirm'
            await self._show_draft_confirmation(event.chat_id, content)
            return True

        if step == 'draft_edit_name':
            if not text:
                await event.reply('❌ نام بنر نمی‌تواند خالی باشد.')
                return True
            state['draft']['name'] = text[:100]
            state['step'] = 'create_confirm'
            await self._show_draft_confirmation(event.chat_id, state['draft'])
            return True

        if step == 'draft_edit_interval':
            minutes = self._parse_interval(text)
            if minutes is None:
                await event.reply('❌ عددی بین 1 تا 10080 دقیقه وارد کن.')
                return True
            state['draft']['interval'] = minutes
            state['step'] = 'create_confirm'
            await self._show_draft_confirmation(event.chat_id, state['draft'])
            return True

        if step == 'edit_name':
            bid = int(state['banner_id'])
            if not text:
                await event.reply('❌ نام بنر نمی‌تواند خالی باشد.')
                return True
            if not models.update_banner(bid, uid, name=text[:100]):
                return await self._message_denied(event)
            self.states.pop(uid, None)
            await event.reply('✅ نام بنر تغییر کرد.')
            await self._send_manage(event.chat_id, uid, bid)
            return True

        if step == 'edit_text':
            bid = int(state['banner_id'])
            banner = models.get_banner(bid, uid)
            if not banner:
                return await self._message_denied(event)
            if not text:
                await event.reply('❌ متن/کپشن نمی‌تواند خالی باشد.')
                return True
            if banner['send_mode'] == 'forward':
                ok, error = await self._edit_source_message(uid, banner, text)
                if not ok:
                    await event.reply(f'❌ ویرایش پیام اصلی ممکن نشد: `{error}`', parse_mode='md')
                    return True
            fields = {'text': text, 'caption': None} if banner['type'] == 'text' else {'caption': text}
            models.update_banner(bid, uid, **fields)
            self.states.pop(uid, None)
            await event.reply('✅ متن/کپشن بنر تغییر کرد.')
            await self._send_manage(event.chat_id, uid, bid)
            return True

        if step == 'edit_file':
            bid = int(state['banner_id'])
            if not models.get_banner(bid, uid):
                return await self._message_denied(event)
            content = self._content_from_event(event)
            if not content or content['type'] == 'text':
                await event.reply('❌ یک عکس، ویدیو، ویس، فایل یا گیف جدید بفرست.')
                return True
            models.update_banner(bid, uid, **content)
            self.states.pop(uid, None)
            await event.reply('✅ فایل بنر تغییر کرد.')
            await self._send_manage(event.chat_id, uid, bid)
            return True

        if step == 'edit_interval':
            bid = int(state['banner_id'])
            minutes = self._parse_interval(text)
            if minutes is None:
                await event.reply('❌ عددی بین 1 تا 10080 دقیقه وارد کن.')
                return True
            banner = models.get_banner(bid, uid)
            if not banner:
                return await self._message_denied(event)
            patch: dict[str, Any] = {'interval': minutes}
            if banner['status'] == 'active':
                patch['next_send_at'] = time.time() + minutes * 60
            models.update_banner(bid, uid, **patch)
            self.states.pop(uid, None)
            await event.reply(f'✅ فاصله ارسال روی هر {minutes} دقیقه تنظیم شد.')
            await self._send_manage(event.chat_id, uid, bid)
            return True

        if step == 'whitelist_add':
            if event.chat_id != uid:
                return False
            if text == 'لغو':
                self.states.pop(uid, None)
                result, buttons = render_whitelist(uid)
                await event.reply(result, buttons=buttons, parse_mode='md')
                return True
            try:
                entries = await resolve_entries(
                    self_manager.get_client_for_user(uid), text,
                    message=getattr(event, 'message', None),
                    contact=getattr(event, 'contact', None),
                )
                # Cancellation or another menu action invalidates this request.
                if self.states.get(uid) is not state:
                    return True
                models.add_whitelist_entries(uid, entries)
            except WhitelistError as exc:
                if self.states.get(uid) is state:
                    await event.reply(f'❌ {exc}', parse_mode=None)
                return True
            except Exception:
                if self.states.get(uid) is state:
                    await event.reply('❌ ذخیرهٔ مقصدها ممکن نشد؛ دوباره تلاش کنید.')
                return True
            self.states.pop(uid, None)
            await event.reply(f'✅ {len(entries)} مقصد ثبت شد؛ ارسال فقط به لیست سفید فعال است.')
            result, buttons = render_whitelist(uid)
            await event.reply(result, buttons=buttons, parse_mode='md')
            return True

        if step == 'blacklist_add':
            if not text:
                await event.reply('❌ آیدی یا یوزرنیم را ارسال کن.')
                return True
            try:
                target, target_type = await self._resolve_blacklist_target(uid, text)
                models.add_blacklist(user_id=uid, target=target, target_type=target_type)
            except Exception as exc:
                await event.reply(
                    '❌ مقصد پیدا نشد یا قابل تشخیص نیست.\n'
                    'آیدی/یوزرنیم باید متعلق به یکی از چت‌های قابل دسترسی همین حساب باشد.\n\n'
                    f'خطا: `{type(exc).__name__}: {exc}`',
                    parse_mode='md',
                )
                return True
            self.states.pop(uid, None)
            await event.reply(f'✅ `{target}` به بلک لیست اضافه شد.', parse_mode='md')
            await self._send_blacklist(event.chat_id, uid)
            return True

        return False

    async def handle_callback(self, event, data: str) -> bool:
        uid = int(event.sender_id)
        if not data.startswith('tb_'):
            return False

        if data == 'tb_menu':
            await self.show_menu(event)
            return True
        if data == 'tb_list':
            self.states.pop(uid, None)
            text, buttons = render_banner_list(uid)
            await event.edit(text, buttons=buttons, parse_mode='md')
            return True
        if data == 'tb_settings':
            self.states.pop(uid, None)
            text, buttons = render_tabchi_settings(uid)
            await event.edit(text, buttons=buttons, parse_mode='md')
            return True
        if data == 'tb_blacklist':
            self.states.pop(uid, None)
            text, buttons = render_blacklist(uid)
            await event.edit(text, buttons=buttons, parse_mode='md')
            return True
        if data == 'tb_whitelist' or data.startswith('tb_wl_'):
            if event.chat_id != uid:
                await event.answer('لیست سفید را در گفتگوی خصوصی ربات مدیریت کنید.', alert=True)
                return True
            if data == 'tb_wl_add':
                if not self.has_self_session(uid):
                    await event.answer('ابتدا سلف‌بات را متصل کن.', alert=True)
                    return True
                self.states[uid] = {'step': 'whitelist_add'}
                await event.edit(
                    '➕ **افزودن به لیست سفید**\n\n'
                    'آیدی عددی، @یوزرنیم، لینک تلگرام یا نام دقیق گروه/مخاطب را بفرستید. '
                    'پیام فورواردشده با مبدأ قابل‌شناسایی یا کارت مخاطب هم پذیرفته می‌شود.\n\n'
                    'فقط گروه‌ها و پیوی‌های موجود در گفتگوهای حساب متصل قابل انتخاب‌اند. '
                    'برای گروه خصوصی، آیدی کامل یا پیام قابل‌شناسایی آن را بفرستید.\n'
                    'می‌توانید تا ۲۰ مقصد را در یک پیام، هرکدام در یک خط وارد کنید.\n\n'
                    'با افزودن مقصد، حالت «فقط لیست سفید» فعال می‌شود.\n'
                    'لغو: دکمهٔ زیر یا ارسال «لغو».',
                    buttons=[[ui.inline_button('❌ لغو', b'tb_whitelist', 'danger')]],
                    parse_mode='md',
                )
                return True
            page = 0
            if data in {'tb_wl_enable', 'tb_wl_disable'}:
                models.set_whitelist_enabled(uid, data == 'tb_wl_enable')
            elif data.startswith('tb_wl_del_'):
                suffix = data.removeprefix('tb_wl_del_')
                if not suffix.isascii() or not suffix.isdigit() or len(suffix) > 18:
                    return await self._bad_button(event)
                models.delete_whitelist(int(suffix), uid)
            elif data.startswith('tb_wl_page_'):
                suffix = data.removeprefix('tb_wl_page_')
                if not suffix.isascii() or not suffix.isdigit() or len(suffix) > 8:
                    return await self._bad_button(event)
                page = int(suffix)
            elif data != 'tb_whitelist':
                return await self._bad_button(event)
            self.states.pop(uid, None)
            result, buttons = render_whitelist(uid, page)
            await event.edit(result, buttons=buttons, parse_mode='md')
            return True
        if data == 'tb_bl_add':
            if not self.has_self_session(uid):
                await event.answer('ابتدا سلف‌بات را متصل کن.', alert=True)
                return True
            self.states[uid] = {'step': 'blacklist_add'}
            await event.edit(
                '➕ **افزودن به بلک لیست**\n\n'
                'آیدی عددی یا یوزرنیم را ارسال کنید:\n\n'
                '`123456789`\nیا\n`@username`\nیا\n`@groupname`',
                buttons=[[ui.inline_button('❌ لغو', b'tb_blacklist', 'danger')]],
                parse_mode='md',
            )
            return True
        if data.startswith('tb_bl_del_'):
            try:
                entry_id = int(data.rsplit('_', 1)[1])
            except Exception:
                return await self._bad_button(event)
            models.delete_blacklist(entry_id, uid)
            text, buttons = render_blacklist(uid)
            await event.edit(text, buttons=buttons, parse_mode='md')
            return True
        if data == 'tb_new':
            try:
                await event.delete()
            except Exception:
                pass
            await self.start_create(uid, event.chat_id)
            return True
        if data == 'tb_cancel':
            self.states.pop(uid, None)
            text, buttons = render_tabchi_home(uid)
            try:
                await event.edit('❌ عملیات لغو شد.\n\n' + text, buttons=buttons, parse_mode='md')
            except Exception:
                await self.bot.send_message(event.chat_id, text, buttons=buttons, parse_mode='md')
            return True

        # Draft: multi-select destination.
        if data in {'tb_dtarget_group', 'tb_dtarget_private', 'tb_dtarget_ok'}:
            state = self.states.get(uid)
            draft = state.get('draft') if state else None
            if not draft:
                await event.answer('نشست ساخت بنر منقضی شده است.', alert=True)
                return True
            selected = models.normalize_send_targets(draft.get('send_target'))
            if data.endswith('_group'):
                self._toggle(selected, 'group')
            elif data.endswith('_private'):
                self._toggle(selected, 'private')
            else:
                if not selected:
                    await event.answer('حداقل یک مقصد را انتخاب کن.', alert=True)
                    return True
                draft['send_target'] = selected
                state['step'] = 'create_mode'
                await event.edit(
                    '🔁 **نوع ارسال را انتخاب کنید:**',
                    buttons=[
                        [
                            ui.inline_button('🔁 فوروارد', b'tb_draft_forward', 'primary'),
                            ui.inline_button('📤 ارسال عادی', b'tb_draft_normal', 'success'),
                        ],
                        [ui.inline_button('❌ لغو', b'tb_cancel', 'danger')],
                    ],
                    parse_mode='md',
                )
                return True
            draft['send_target'] = selected
            text, buttons = render_target_selector(selected, prefix='tb_dtarget', cancel_data='tb_cancel')
            await event.edit(text, buttons=buttons, parse_mode='md')
            return True

        if data in {'tb_draft_forward', 'tb_draft_normal'}:
            state = self.states.get(uid)
            if not state or not state.get('draft'):
                await event.answer('نشست ساخت بنر منقضی شده است.', alert=True)
                return True
            state['draft']['send_mode'] = 'forward' if data.endswith('forward') else 'normal'
            state['step'] = 'create_interval'
            await event.edit(
                '⏱ **هر چند دقیقه ارسال شود؟**\n\nمثال: `10` یعنی هر 10 دقیقه.',
                buttons=[[ui.inline_button('❌ لغو', b'tb_cancel', 'danger')]],
                parse_mode='md',
            )
            return True

        if data == 'tb_draft_confirm':
            state = self.states.get(uid)
            draft = state.get('draft') if state else None
            required = {'name', 'type', 'send_target', 'send_mode', 'interval', 'source_message_id'}
            if not draft or not required.issubset(draft) or not models.normalize_send_targets(draft.get('send_target')):
                await event.answer('اطلاعات بنر ناقص یا منقضی شده است.', alert=True)
                return True
            bid = models.create_banner(
                user_id=uid,
                name=draft['name'], type=draft['type'], file_id=draft.get('file_id'),
                text=draft.get('text'), caption=draft.get('caption'),
                send_mode=draft['send_mode'], send_target=draft['send_target'],
                interval=int(draft['interval']), status='active',
                source_peer=draft.get('source_peer') or self.bot_username,
                source_message_id=int(draft['source_message_id']),
            )
            self.states.pop(uid, None)
            await event.edit(
                '✅ **بنر ساخته و فعال شد.**',
                buttons=[
                    [ui.inline_button('⚙️ مدیریت بنر', f'tb_b_{bid}', 'primary')],
                    [ui.inline_button('📢 لیست بنرها', b'tb_list', 'secondary')],
                ],
                parse_mode='md',
            )
            return True

        if data == 'tb_draft_edit':
            state = self.states.get(uid)
            if not state or not state.get('draft'):
                await event.answer('نشست منقضی شده است.', alert=True)
                return True
            await event.edit(
                '✏️ **ویرایش پیش‌نویس**\n\nبخش موردنظر را انتخاب کن:',
                buttons=[
                    [ui.inline_button('🖼 تغییر محتوا', b'tb_draft_content', 'primary')],
                    [ui.inline_button('📝 تغییر نام', b'tb_draft_name', 'primary')],
                    [ui.inline_button('👥 تغییر مقصد', b'tb_draft_targets', 'success')],
                    [ui.inline_button('🔁 تغییر حالت ارسال', b'tb_draft_mode', 'primary')],
                    [ui.inline_button('⏱ تغییر زمان', b'tb_draft_interval', 'primary')],
                    [ui.inline_button('↩️ پیش نمایش', b'tb_draft_back', 'secondary')],
                    [ui.inline_button('❌ لغو', b'tb_cancel', 'danger')],
                ],
                parse_mode='md',
            )
            return True
        if data == 'tb_draft_content':
            state = self.states.get(uid)
            if not state:
                return True
            state['step'] = 'draft_replace_content'
            await event.edit('📢 محتوای جدید را ارسال کن:', buttons=[[ui.inline_button('❌ لغو', b'tb_cancel', 'danger')]])
            return True
        if data == 'tb_draft_name':
            state = self.states.get(uid)
            if not state:
                return True
            state['step'] = 'draft_edit_name'
            await event.edit('📝 نام جدید را ارسال کن:', buttons=[[ui.inline_button('❌ لغو', b'tb_cancel', 'danger')]])
            return True
        if data == 'tb_draft_targets':
            state = self.states.get(uid)
            if not state or not state.get('draft'):
                return True
            state['step'] = 'create_targets'
            text, buttons = render_target_selector(state['draft'].get('send_target'), prefix='tb_dtarget', cancel_data='tb_cancel')
            await event.edit(text, buttons=buttons, parse_mode='md')
            return True
        if data == 'tb_draft_mode':
            await event.edit(
                '🔁 **نوع ارسال را انتخاب کنید:**',
                buttons=[
                    [
                        ui.inline_button('🔁 فوروارد', b'tb_draft_forward', 'primary'),
                        ui.inline_button('📤 ارسال عادی', b'tb_draft_normal', 'success'),
                    ],
                    [ui.inline_button('❌ لغو', b'tb_cancel', 'danger')],
                ],
                parse_mode='md',
            )
            return True
        if data == 'tb_draft_interval':
            state = self.states.get(uid)
            if not state:
                return True
            state['step'] = 'draft_edit_interval'
            await event.edit('⏱ فاصله جدید را به دقیقه وارد کن:', buttons=[[ui.inline_button('❌ لغو', b'tb_cancel', 'danger')]])
            return True
        if data == 'tb_draft_back':
            state = self.states.get(uid)
            if state and state.get('draft'):
                state['step'] = 'create_confirm'
                try:
                    await event.delete()
                except Exception:
                    pass
                await self._show_draft_confirmation(event.chat_id, state['draft'])
            return True

        # Existing banner destination selector: tb_et_<banner>_<action>
        if data.startswith('tb_et_'):
            parts = data.split('_')
            if len(parts) != 4:
                return await self._bad_button(event)
            try:
                bid = int(parts[2])
            except Exception:
                return await self._bad_button(event)
            action = parts[3]
            banner = models.get_banner(bid, uid)
            if not banner:
                return await self._denied(event)
            state = self.states.get(uid)
            if not state or state.get('step') != 'edit_targets' or int(state.get('banner_id', 0)) != bid:
                state = {
                    'step': 'edit_targets',
                    'banner_id': bid,
                    'selected_targets': models.get_banner_send_targets(banner),
                }
                self.states[uid] = state
            selected = models.normalize_send_targets(state.get('selected_targets'))
            if action in {'group', 'private'}:
                self._toggle(selected, action)
                state['selected_targets'] = selected
                text, buttons = render_target_selector(selected, prefix=f'tb_et_{bid}', cancel_data=f'tb_b_{bid}')
                await event.edit(text, buttons=buttons, parse_mode='md')
                return True
            if action == 'ok':
                if not selected:
                    await event.answer('حداقل یک مقصد را انتخاب کن.', alert=True)
                    return True
                models.update_banner(bid, uid, send_target=selected)
                self.states.pop(uid, None)
                await event.answer('✅ مقصد ارسال تغییر کرد.', alert=False)
                await self._edit_manage(event, uid, bid)
                return True
            return await self._bad_button(event)

        parts = data.split('_')
        try:
            if data.startswith('tb_b_'):
                await self._edit_manage(event, uid, int(parts[2]))
                return True
            if data.startswith('tb_on_'):
                bid = int(parts[2])
                if not models.get_banner(bid, uid):
                    return await self._denied(event)
                models.set_banner_status(bid, uid, 'active')
                await self._edit_manage(event, uid, bid)
                return True
            if data.startswith('tb_off_'):
                bid = int(parts[2])
                if not models.get_banner(bid, uid):
                    return await self._denied(event)
                models.set_banner_status(bid, uid, 'paused')
                await self._edit_manage(event, uid, bid)
                return True
            if data.startswith('tb_name_'):
                bid = int(parts[2])
                if not models.get_banner(bid, uid):
                    return await self._denied(event)
                self.states[uid] = {'step': 'edit_name', 'banner_id': bid}
                await event.edit('✏️ نام جدید بنر را ارسال کن:', buttons=[[ui.inline_button('❌ لغو', f'tb_b_{bid}', 'danger')]])
                return True
            if data.startswith('tb_text_'):
                bid = int(parts[2])
                if not models.get_banner(bid, uid):
                    return await self._denied(event)
                self.states[uid] = {'step': 'edit_text', 'banner_id': bid}
                await event.edit('📝 متن/کپشن جدید را ارسال کن:', buttons=[[ui.inline_button('❌ لغو', f'tb_b_{bid}', 'danger')]])
                return True
            if data.startswith('tb_file_'):
                bid = int(parts[2])
                if not models.get_banner(bid, uid):
                    return await self._denied(event)
                self.states[uid] = {'step': 'edit_file', 'banner_id': bid}
                await event.edit('🖼 فایل جدید را ارسال کن (عکس/ویدیو/ویس/فایل/گیف):', buttons=[[ui.inline_button('❌ لغو', f'tb_b_{bid}', 'danger')]])
                return True
            if data.startswith('tb_targets_'):
                bid = int(parts[2])
                banner = models.get_banner(bid, uid)
                if not banner:
                    return await self._denied(event)
                selected = models.get_banner_send_targets(banner)
                self.states[uid] = {'step': 'edit_targets', 'banner_id': bid, 'selected_targets': selected}
                text, buttons = render_target_selector(selected, prefix=f'tb_et_{bid}', cancel_data=f'tb_b_{bid}')
                await event.edit(text, buttons=buttons, parse_mode='md')
                return True
            if data.startswith('tb_mode_'):
                bid = int(parts[2])
                banner = models.get_banner(bid, uid)
                if not banner:
                    return await self._denied(event)
                await event.edit(
                    '🔁 **حالت ارسال را انتخاب کنید:**',
                    buttons=[
                        [
                            ui.inline_button('🔁 فوروارد', f'tb_sm_{bid}_forward', 'primary'),
                            ui.inline_button('📤 ارسال عادی', f'tb_sm_{bid}_normal', 'success'),
                        ],
                        [ui.inline_button('↩️ بازگشت', f'tb_b_{bid}', 'secondary')],
                    ],
                    parse_mode='md',
                )
                return True
            if data.startswith('tb_sm_'):
                # tb_sm_<banner>_<forward|normal>
                if len(parts) != 4:
                    return await self._bad_button(event)
                bid = int(parts[2])
                mode = parts[3]
                if mode not in {'forward', 'normal'}:
                    return await self._bad_button(event)
                banner = models.get_banner(bid, uid)
                if not banner:
                    return await self._denied(event)
                if mode == 'forward':
                    ok, error = await self._prepare_forward_source(uid, banner)
                    if not ok:
                        await event.answer(f'پیام اصلی برای Forward آماده نشد: {error}', alert=True)
                        return True
                models.update_banner(bid, uid, send_mode=mode)
                await event.answer('✅ حالت ارسال تغییر کرد.', alert=False)
                await self._edit_manage(event, uid, bid)
                return True
            if data.startswith('tb_time_'):
                bid = int(parts[2])
                if not models.get_banner(bid, uid):
                    return await self._denied(event)
                self.states[uid] = {'step': 'edit_interval', 'banner_id': bid}
                await event.edit('⏱ فاصله جدید را به دقیقه وارد کن:', buttons=[[ui.inline_button('❌ لغو', f'tb_b_{bid}', 'danger')]])
                return True
            if data.startswith('tb_delok_'):
                bid = int(parts[2])
                if not models.delete_banner(bid, uid):
                    return await self._denied(event)
                self.states.pop(uid, None)
                text, buttons = render_banner_list(uid)
                await event.edit('🗑 بنر حذف شد.\n\n' + text, buttons=buttons, parse_mode='md')
                return True
            if data.startswith('tb_del_'):
                bid = int(parts[2])
                banner = models.get_banner(bid, uid)
                if not banner:
                    return await self._denied(event)
                await event.edit(
                    f"⚠️ بنر **{banner['name']}** حذف شود؟",
                    buttons=[
                        [ui.inline_button('🗑 بله، حذف', f'tb_delok_{bid}', 'danger')],
                        [ui.inline_button('↩️ خیر', f'tb_b_{bid}', 'secondary')],
                    ],
                    parse_mode='md',
                )
                return True
        except (ValueError, IndexError):
            return await self._bad_button(event)

        return True

    @staticmethod
    def _parse_interval(text: str) -> int | None:
        try:
            value = int((text or '').strip())
        except Exception:
            return None
        return value if 1 <= value <= 10080 else None

    @staticmethod
    def _toggle(selected: list[str], key: str) -> None:
        if key in selected:
            selected.remove(key)
        else:
            selected.append(key)

    async def _send_draft_targets(self, chat_id: int, draft: dict):
        text, buttons = render_target_selector(draft.get('send_target'), prefix='tb_dtarget', cancel_data='tb_cancel')
        await self.bot.send_message(chat_id, text, buttons=buttons, parse_mode='md')

    async def _show_draft_confirmation(self, chat_id: int, draft: dict):
        await self._send_preview(chat_id, draft)
        await self.bot.send_message(
            chat_id,
            preview_summary(draft),
            buttons=[
                [ui.inline_button('✅ فعال سازی', b'tb_draft_confirm', 'success')],
                [ui.inline_button('✏️ ویرایش', b'tb_draft_edit', 'primary')],
                [ui.inline_button('❌ لغو', b'tb_cancel', 'danger')],
            ],
            parse_mode='md',
        )

    async def _send_preview(self, chat_id: int, banner: dict):
        msg_id = banner.get('source_message_id')
        if not msg_id:
            return
        try:
            source = await self.bot.get_messages(chat_id, ids=int(msg_id))
        except Exception:
            source = None
        if not source:
            await self.bot.send_message(chat_id, '⚠️ محتوای اصلی برای پیش‌نمایش پیدا نشد.')
            return
        try:
            if banner.get('send_mode') == 'forward':
                await self.bot.forward_messages(chat_id, source)
            elif banner.get('type') == 'text':
                await self.bot.send_message(chat_id, banner.get('text') or source.raw_text or '')
            else:
                await self.bot.send_message(chat_id, banner.get('caption') or '', file=source.media)
        except Exception as exc:
            await self.bot.send_message(chat_id, f'⚠️ خطای پیش‌نمایش: `{type(exc).__name__}: {exc}`', parse_mode='md')

    async def _resolve_blacklist_target(self, uid: int, token: str) -> tuple[str, str]:
        client = self_manager.get_client_for_user(uid)
        if client is None or not client.is_connected():
            raise RuntimeError('سلف‌بات متصل نیست')

        raw = token.strip()
        entity = None
        lookup = raw[1:] if raw.startswith('@') else raw
        try:
            if raw.startswith('@'):
                entity = await client.get_entity(raw)
            elif lookup.lstrip('-').isdigit():
                numeric = int(lookup)
                try:
                    entity = await client.get_entity(numeric)
                except Exception:
                    async for dialog in client.iter_dialogs():
                        ent = dialog.entity
                        raw_id = getattr(ent, 'id', None)
                        try:
                            peer_id = int(get_peer_id(ent))
                        except Exception:
                            peer_id = None
                        if raw_id == abs(numeric) or raw_id == numeric or peer_id == numeric:
                            entity = ent
                            break
            else:
                entity = await client.get_entity(raw)
        except Exception:
            entity = None

        if entity is None:
            raise LookupError('مقصد در چت‌های این حساب پیدا نشد')

        if isinstance(entity, User):
            target_type = 'user'
        elif isinstance(entity, (Chat, Channel)):
            target_type = 'group'
        else:
            raise ValueError('نوع مقصد پشتیبانی نمی‌شود')

        # Preserve username when supplied; numeric IDs are canonicalized to the
        # value the user entered so both raw and peer ids can be matched later.
        if raw.startswith('@'):
            target = '@' + raw[1:].lower()
        elif raw.lstrip('-').isdigit():
            target = str(int(raw))
        else:
            username = getattr(entity, 'username', None)
            target = ('@' + username.lower()) if username else str(int(getattr(entity, 'id')))
        return target, target_type

    async def _edit_source_message(self, uid: int, banner: dict, text: str) -> tuple[bool, str | None]:
        client = self_manager.get_client_for_user(uid)
        if client is None or not client.is_connected():
            return False, 'سلف‌بات متصل نیست'
        try:
            await client.edit_message(banner['source_peer'], int(banner['source_message_id']), text)
            return True, None
        except Exception as exc:
            return False, f'{type(exc).__name__}: {exc}'

    async def _prepare_forward_source(self, uid: int, banner: dict) -> tuple[bool, str | None]:
        client = self_manager.get_client_for_user(uid)
        if client is None or not client.is_connected():
            return False, 'سلف‌بات متصل نیست'
        try:
            source = await client.get_messages(banner['source_peer'], ids=int(banner['source_message_id']))
            if not source:
                return False, 'پیام اصلی پیدا نشد'
            desired = (banner.get('text') if banner['type'] == 'text' else banner.get('caption')) or ''
            current = (source.raw_text or '').strip()
            if desired.strip() != current:
                await client.edit_message(banner['source_peer'], int(banner['source_message_id']), desired)
            return True, None
        except Exception as exc:
            return False, f'{type(exc).__name__}: {exc}'

    async def _edit_manage(self, event, uid: int, bid: int):
        self.states.pop(uid, None)
        banner = models.get_banner(bid, uid)
        if not banner:
            return await self._denied(event)
        text, buttons = render_banner_manage(banner)
        await event.edit(text, buttons=buttons, parse_mode='md')

    async def _send_manage(self, chat_id: int, uid: int, bid: int):
        banner = models.get_banner(bid, uid)
        if not banner:
            return
        text, buttons = render_banner_manage(banner)
        await self.bot.send_message(chat_id, text, buttons=buttons, parse_mode='md')

    async def _send_blacklist(self, chat_id: int, uid: int):
        text, buttons = render_blacklist(uid)
        await self.bot.send_message(chat_id, text, buttons=buttons, parse_mode='md')

    async def _denied(self, event):
        await event.answer('⛔️ بنر پیدا نشد یا متعلق به شما نیست.', alert=True)
        return True

    async def _message_denied(self, event):
        self.states.pop(int(event.sender_id), None)
        await event.reply('⛔️ بنر پیدا نشد یا متعلق به شما نیست.')
        return True

    async def _bad_button(self, event):
        await event.answer('❌ داده دکمه نامعتبر است.', alert=True)
        return True
