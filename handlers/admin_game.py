"""Admin panel for persistent game settings, statistics and lookups."""
from __future__ import annotations

from telethon.utils import get_peer_id

import ui
from database import models
from services import balance_service
from ui.game_messages import display_name, fmt


class AdminGameController:
    def __init__(self, bot, allowed):
        self.bot = bot
        self.allowed = allowed
        self.states: dict[int, dict] = {}

    def render_menu(self):
        s = models.get_game_settings()
        status = '🟢 فعال' if int(s.get('game_enabled') or 0) else '🔴 غیرفعال'
        group = s.get('main_game_group_title') or s.get('main_game_group') or 'تنظیم نشده'
        text = (
            '🎮 **مدیریت بازی**\n\n'
            f'وضعیت: **{status}**\n'
            f'📍 گروه اصلی: `{group}`\n'
            f"💸 کارمزد: **{s['game_tax']}%**\n"
            f"💎 ورودی: **{fmt(s['min_bet'])} تا {fmt(s['max_bet'])}**\n"
            f"⏱ انقضا: **{int(s['expiration_seconds']) // 60} دقیقه**\n"
            f"🔐 محدودیت بازی باز: **{'فعال' if int(s['prevent_multiple_open']) else 'غیرفعال'}**\n"
            '🎲 انتخاب برنده: **secrets (رمزنگاری‌شده)**'
        )
        buttons = [
            [ui.inline_button('📍 گروه اصلی', b'ag_group'), ui.inline_button('💸 مالیات', b'ag_tax')],
            [ui.inline_button('💎 حداقل ورودی', b'ag_min'), ui.inline_button('💎 حداکثر ورودی', b'ag_max')],
            [ui.inline_button('⏱ زمان انقضا', b'ag_exp'), ui.inline_button('🎲 روش برنده', b'ag_method')],
            [ui.inline_button('⏯ فعال/غیرفعال', b'ag_toggle', 'success'), ui.inline_button('🔐 محدودیت بازی باز', b'ag_multi')],
            [ui.inline_button('📊 آمار بازی‌ها', b'ag_stats'), ui.inline_button('📜 تاریخچه', b'ag_history')],
            [ui.inline_button('🔎 جستجوی بازی', b'ag_find_game'), ui.inline_button('🔎 جستجوی کاربر', b'ag_find_user')],
            [ui.inline_button('↩️ مدیریت', b'adm2_menu', 'secondary')],
        ]
        return text, buttons

    def render_stats(self):
        s = models.game_statistics()
        text = (
            '📊 **آمار بازی‌ها**\n\n'
            f"🎮 کل بازی‌ها: **{fmt(s['TOTAL'])}**\n"
            f"✅ تمام‌شده: **{fmt(s['FINISHED'])}**\n"
            f"⏳ باز: **{fmt(s['OPEN'])}**\n"
            f"🔒 قفل‌شده: **{fmt(s['LOCKED'])}**\n"
            f"❌ لغوشده: **{fmt(s['CANCELLED'])}**\n"
            f"⌛ منقضی‌شده: **{fmt(s['EXPIRED'])}**\n\n"
            f"💎 مجموع گردش: **{fmt(s['VOLUME'])} الماس**\n"
            f"💸 مجموع کارمزد: **{fmt(s['TAX'])} الماس**"
        )
        return text, [[ui.inline_button('🔄 بروزرسانی', b'ag_stats')], [ui.inline_button('↩️ بازی', b'ag_menu', 'secondary')]]

    def render_history(self):
        rows = models.list_games(15)
        text = '📜 **تاریخچه بازی‌ها**\n\n'
        if not rows:
            text += 'هنوز بازی‌ای ثبت نشده است.'
        else:
            text += '\n'.join(
                f"`#{r['id']}` • **{r['status']}** • {fmt(r['bet_amount'])} 💎 • `{r['creator_id']}`"
                for r in rows
            )
        return text, [[ui.inline_button('↩️ بازی', b'ag_menu', 'secondary')]]

    def render_game(self, game_id: int):
        g = models.get_game(game_id)
        if not g:
            return '❌ بازی پیدا نشد.', [[ui.inline_button('↩️ بازی', b'ag_menu')]]
        text = (
            f"🔎 **بازی #{g['id']}**\n\n"
            f"وضعیت: **{g['status']}**\n"
            f"👤 سازنده: `{g['creator_id']}`\n"
            f"👤 حریف: `{g.get('opponent_id') or '-'}`\n"
            f"🏆 برنده: `{g.get('winner_id') or '-'}`\n"
            f"💎 ورودی: **{fmt(g['bet_amount'])}**\n"
            f"💸 مالیات ثابت بازی: **{g['tax_percent']}% / {fmt(g['tax_amount'])}**\n"
            f"📍 پیام: `{g['chat_id']} / {g['message_id']}`\n"
            f"🕒 ایجاد UTC: `{g['created_at']}`"
        )
        return text, [[ui.inline_button('↩️ بازی', b'ag_menu', 'secondary')]]

    def render_user(self, token: str):
        user = models.find_game_user(token)
        if not user:
            return '❌ کاربر در کیف پول بازی پیدا نشد.', [[ui.inline_button('↩️ بازی', b'ag_menu')]]
        txs = models.list_diamond_transactions(int(user['telegram_id']), 8)
        lines = [
            '🔎 **کاربر بازی**\n',
            f"👤 {display_name(user.get('username'), user.get('first_name'), user['telegram_id'])}",
            f"🆔 `{user['telegram_id']}`",
            f"💎 موجودی: **{fmt(user['diamonds'])}**\n",
            '📜 آخرین تراکنش‌ها:',
        ]
        lines.extend(
            f"`#{t['id']}` • {t['type']} • {t['amount']:+,} • مانده {fmt(t['balance_after'])}"
            for t in txs
        )
        if not txs:
            lines.append('بدون تراکنش')
        return '\n'.join(lines), [[ui.inline_button('↩️ بازی', b'ag_menu', 'secondary')]]

    async def handle_callback(self, event, data: str) -> bool:
        if not data.startswith('ag_'):
            return False
        uid = int(event.sender_id)
        sender = await event.get_sender()
        username = getattr(sender, 'username', '') or ''
        if not self.allowed(uid, 'manage_games', username):
            await event.answer('⛔️ دسترسی مدیریت بازی ندارید.', alert=True)
            return True
        if data == 'ag_menu':
            self.states.pop(uid, None); text, buttons = self.render_menu()
        elif data == 'ag_method':
            await event.answer('🎲 روش امن فعال: secrets.choice\nنتیجه فقط یک‌بار در دیتابیس ثبت می‌شود.', alert=True)
            return True
        elif data == 'ag_toggle':
            s = models.get_game_settings(); models.update_game_settings(game_enabled=not bool(s['game_enabled']))
            text, buttons = self.render_menu()
        elif data == 'ag_multi':
            s = models.get_game_settings(); models.update_game_settings(prevent_multiple_open=not bool(s['prevent_multiple_open']))
            text, buttons = self.render_menu()
        elif data == 'ag_stats':
            text, buttons = self.render_stats()
        elif data == 'ag_history':
            text, buttons = self.render_history()
        elif data in {'ag_group','ag_tax','ag_min','ag_max','ag_exp','ag_find_game','ag_find_user'}:
            step = {
                'ag_group':'GROUP','ag_tax':'TAX','ag_min':'MIN','ag_max':'MAX','ag_exp':'EXP',
                'ag_find_game':'FIND_GAME','ag_find_user':'FIND_USER',
            }[data]
            self.states[uid] = {'step': step}
            prompt = {
                'GROUP':'ID عددی گروه یا @username را ارسال کنید:',
                'TAX':'درصد کارمزد را بین 0 تا 30 ارسال کنید:',
                'MIN':'حداقل ورودی الماس را ارسال کنید:',
                'MAX':'حداکثر ورودی الماس را ارسال کنید:',
                'EXP':'زمان انقضا را به دقیقه (1 تا 1440) ارسال کنید:',
                'FIND_GAME':'شماره بازی را ارسال کنید:',
                'FIND_USER':'ID عددی یا @username کاربر را ارسال کنید:',
            }[step]
            await event.edit(f'🎮 **تنظیمات بازی**\n\n{prompt}', buttons=[[ui.inline_button('❌ لغو', b'ag_menu', 'danger')]], parse_mode='md')
            return True
        else:
            await event.answer('درخواست نامعتبر است.', alert=True); return True
        await event.edit(text, buttons=buttons, parse_mode='md')
        return True

    async def handle_message(self, event) -> bool:
        uid = int(event.sender_id)
        state = self.states.get(uid)
        if not state:
            return False
        sender = await event.get_sender()
        username = getattr(sender, 'username', '') or ''
        if not self.allowed(uid, 'manage_games', username):
            self.states.pop(uid, None); return False
        text = (event.raw_text or '').strip()
        step = state['step']
        try:
            if step == 'GROUP':
                if not (text.startswith('@') or text.lstrip('-').isdigit()):
                    raise ValueError('ID عددی یا @username معتبر ارسال کنید.')
                title = text
                canonical = text
                try:
                    entity = await self.bot.get_entity(text if text.startswith('@') else int(text))
                    canonical = str(get_peer_id(entity))
                    title = getattr(entity, 'title', None) or getattr(entity, 'username', None) or canonical
                except Exception:
                    if text.startswith('@'):
                        raise ValueError('گروه با این username برای ربات قابل شناسایی نیست.')
                models.update_game_settings(main_game_group=canonical, main_game_group_title=title)
                result = f'📍 گروه اصلی روی `{title}` تنظیم شد.'
            elif step in {'TAX','MIN','MAX','EXP','FIND_GAME'}:
                value = int(text.replace(',', '').replace('٬', ''))
                if step == 'TAX':
                    models.update_game_settings(game_tax=value); result = f'💸 کارمزد بازی روی **{value}%** تنظیم شد.'
                elif step == 'MIN':
                    models.update_game_settings(min_bet=value); result = f'💎 حداقل ورودی روی **{fmt(value)}** تنظیم شد.'
                elif step == 'MAX':
                    models.update_game_settings(max_bet=value); result = f'💎 حداکثر ورودی روی **{fmt(value)}** تنظیم شد.'
                elif step == 'EXP':
                    models.update_game_settings(expiration_seconds=value * 60); result = f'⏱ انقضا روی **{value} دقیقه** تنظیم شد.'
                else:
                    self.states.pop(uid, None); out, buttons = self.render_game(value)
                    await self.bot.send_message(event.chat_id, out, buttons=buttons, parse_mode='md'); return True
            elif step == 'FIND_USER':
                self.states.pop(uid, None); out, buttons = self.render_user(text)
                await self.bot.send_message(event.chat_id, out, buttons=buttons, parse_mode='md'); return True
            else:
                return False
        except (ValueError, TypeError) as exc:
            message = str(exc) or 'مقدار نامعتبر است.'
            await event.reply(f'❌ {message}')
            return True
        self.states.pop(uid, None)
        await event.reply(result, parse_mode='md')
        out, buttons = self.render_menu()
        await self.bot.send_message(event.chat_id, out, buttons=buttons, parse_mode='md')
        return True
