from __future__ import annotations

import asyncio
import heapq
from typing import Awaitable, Callable

import db
import ui
from config import ADMIN_ID, TRIAL_DURATION_HOURS
from database import models
from services import admin_manager, ticket_manager, trial_manager
from services.markdown import escape_md
from services.broadcast_queue import BroadcastJob, broadcast_queue


def _admin_user_ids_page(page: int, page_size: int):
    """Get sorted admin user page without loading every user id into RAM."""
    import itertools

    profiles = [int(p['user_id']) for p in models.list_profiles(limit=500)]
    extra_profiles = [uid for uid in profiles if not db.user_exists(uid)]

    def stream():
        yield from db.iter_user_ids()
        yield from extra_profiles

    total = db.count_users() + len(extra_profiles)
    top = heapq.nlargest(page * page_size, stream())
    return top[(page - 1) * page_size: page * page_size], total


# کنترلرهای فعال پنل ادمین (برای بستن state ها با دستور .بستن از سمت سلف)
_active_controllers: list['AdminController'] = []


def clear_pending_state(uid) -> bool:
    """پاک‌سازی کامل state پنل ادمین یک کاربر از حافظه و دیتابیس.

    True یعنی state فعالی پیدا و پاک شد؛ همیشه دیتابیس هم پاک می‌شود.
    """
    removed = False
    try:
        key = int(uid)
    except (TypeError, ValueError):
        return False
    for controller in list(_active_controllers):
        states = getattr(controller, 'states', None)
        if isinstance(states, dict) and key in states:
            states.pop(key, None)
            removed = True
    try:
        db.delete_admin_state(key)
    except Exception:  # noqa: BLE001 - دیتابیس نباید بستن state را بشکند
        pass
    return removed


class AdminController:
    def __init__(self, bot, resolve_user: Callable[[str], Awaitable[int | None]]):
        self.bot = bot
        self.resolve_user = resolve_user
        self.states: dict[int, dict] = db.load_admin_states()
        _active_controllers.append(self)

    def _set_state(self, uid: int, state: dict):
        self.states[int(uid)] = state
        db.save_admin_state(int(uid), state)

    def _get_state(self, uid: int):
        state = self.states.get(int(uid))
        if state is None:
            return None
        if not isinstance(state, dict):
            self._clear_state(uid)
            return None

        expires_at = state.get('_expires_at')
        if expires_at is None or float(expires_at) <= __import__('time').time():
            self._clear_state(uid)
            return None

        return state

    def _clear_state(self, uid: int):
        self.states.pop(int(uid), None)
        db.delete_admin_state(int(uid))

    async def _identity(self, event):
        sender = await event.get_sender()
        username = getattr(sender, 'username', '') or ''
        uid = int(event.sender_id)
        admin_manager.note_identity(uid, username)
        return uid, username

    def _allowed(self, uid: int, perm: str, username: str = '') -> bool:
        return admin_manager.has_permission(uid, perm, username)

    def render_main(self, uid: int, username: str = ''):
        perms = admin_manager.permissions_for(uid, username)
        role = admin_manager.get_admin(uid, username) or {}
        role_text = {'owner': 'مالک اصلی', 'full': 'ادمین فول', 'limited': 'ادمین محدود'}.get(role.get('role'), '-')
        text = f'👑 **مدیریت**\n\nسطح دسترسی: **{role_text}**\n\nبخش موردنظر را انتخاب کنید:'
        if uid == ADMIN_ID:
            text += ('\n\nبررسی زندهٔ ایموجی (فقط مالک):\n'
                     '`/emoji_curation USER_ID` — فقط metadata\n'
                     '`/emoji_curation_samples USER_ID` — نمونه در Saved Messages')
        buttons = []
        row = []
        if 'view_users' in perms or 'manage_users' in perms:
            row.append(ui.inline_button('👤 مدیریت کاربران', b'adm2_users', 'primary'))
        if 'manage_tickets' in perms or 'answer_tickets' in perms:
            row.append(ui.inline_button('🎫 تیکت‌ها', b'adm2_tickets', 'primary'))
        if row:
            buttons.append(row)
        row = []
        if 'manage_support' in perms:
            row.append(ui.inline_button('🎧 پشتیبانی', b'adm2_support', 'success'))
        if 'manage_admins' in perms:
            row.append(ui.inline_button('🔐 ادمین‌ها', b'adm2_admins', 'danger'))
        if row:
            buttons.append(row)
        row = []
        if 'manage_trial' in perms:
            row.append(ui.inline_button('🎁 تست رایگان', b'adm2_trials', 'success'))
        if 'view_stats' in perms:
            row.append(ui.inline_button('📊 آمار', b'adm2_stats', 'primary'))
        if row:
            buttons.append(row)
        if 'manage_games' in perms:
            buttons.append([
                ui.inline_button('🎮 مدیریت بازی', b'ag_menu', 'success'),
                ui.inline_button('📍 گروه شرط‌بندی', b'ag_group', 'primary'),
            ])
        row = []
        if 'broadcast' in perms:
            row.append(ui.inline_button('📣 ارسال همگانی', b'adm2_broadcast', 'success'))
        if 'system_settings' in perms:
            row.append(ui.inline_button('⚙️ تنظیمات سیستم', b'adm2_system', 'secondary'))
        if row:
            buttons.append(row)
        return text, buttons

    def _profile(self, user_id: int) -> dict:
        p = models.get_profile(user_id) or {}
        s = db.get_user_settings(user_id)
        return {
            'user_id': int(user_id),
            'username': p.get('username') or s.get('username') or '',
            'first_name': p.get('first_name') or s.get('first_name') or '',
            'last_name': p.get('last_name') or s.get('base_last_name') or '',
            'registered_at': p.get('registered_at') or s.get('registered_at') or '-',
            'last_activity': p.get('last_activity') or '-',
        }

    def _user_label(self, user_id: int) -> str:
        """برچسب امن نمایشی کاربر برای متن پیام‌ها (همیشه بدون رکورد هم کار می‌کند)."""
        p = self._profile(user_id)
        if p.get('username'):
            return f"@{escape_md(p['username'])} (`{int(user_id)}`)"
        if p.get('first_name'):
            return f"**{escape_md(p['first_name'])}** (`{int(user_id)}`)"
        return f"`{int(user_id)}`"

    def render_user_detail(self, user_id: int):
        """نمای واحد و کامل کاربر در بخش کاربران.

        ریشه‌ای: این نما تنها مسیر مدیریت کاربر از پنل است و باید همیشه رندر شود —
        حتی اگر کاربر هنوز رکورد کیف پول SQLite نداشته باشد (موجودی از لایه db
        خوانده می‌شود که خودش SQLite را روی JSON پوشش می‌دهد).
        دکمه‌های افزایش/کاهش موجودی، بن/آن‌بن و مدیریت تست همه از همین‌جا در دسترس‌اند.
        """
        uid = int(user_id)
        p = self._profile(uid)
        wallet = models.get_user_details(uid) or {}
        tr = trial_manager.refresh(uid)
        if tr and int(tr.get('active') or 0):
            trial_status = '🟢 فعال'
            expiry = tr.get('expire_time') or '-'
        elif tr and int(tr.get('used') or 0):
            trial_status = '⚪️ استفاده شده'
            expiry = tr.get('expire_time') or '-'
        else:
            trial_status = '🔴 استفاده نشده'
            expiry = '-'
        uname = f"@{escape_md(p['username'])}" if p['username'] else 'ندارد'
        name = ((p.get('first_name') or '-') + ' ' + (p.get('last_name') or '')).strip()
        diamonds = int(db.get_user_settings(uid).get('diamonds', 0) or 0)
        referrals = int(wallet.get('referral_count') or 0)
        banned = bool(int(wallet.get('banned') or 0))
        status = '🚫 بن شده' if banned else '🟢 فعال'
        text = (
            '👤 **اطلاعات کاربر**\n\n'
            f"ID: `{uid}`\n"
            f"Username: `{uname}`\n"
            f"نام: **{escape_md(name)}**\n"
            f"تاریخ عضویت: `{escape_md(p.get('registered_at') or '-')}`\n"
            f"آخرین فعالیت: `{escape_md(p.get('last_activity') or '-')}`\n"
            f"وضعیت Trial: **{trial_status}**\n"
            f"زمان انقضا: `{escape_md(expiry)}`\n"
            f"تعداد تیکت‌ها: **{models.ticket_count_for_user(uid)}**\n"
            f"👥 رفرال: **{referrals}**\n"
            f"💎 موجودی: **{diamonds} الماس**\n"
            f"وضعیت حساب: **{status}**"
        )
        if banned:
            ban_row = [ui.inline_button('✅ آن‌بن کاربر', f'adm2_user_unban_{uid}', 'success')]
        else:
            ban_row = [ui.inline_button('🚫 بن کردن کاربر', f'adm2_user_ban_{uid}', 'danger')]
        buttons = [
            [
                ui.inline_button('➕ افزایش موجودی', f'adm2_uplus_{uid}', 'success'),
                ui.inline_button('➖ کاهش موجودی', f'adm2_uminus_{uid}', 'danger'),
            ],
            ban_row,
            [ui.inline_button('🎁 مدیریت تست این کاربر', f'adm2_trialuser_{uid}', 'success')],
            [ui.inline_button('↩️ کاربران', b'adm2_users', 'secondary')],
        ]
        return text, buttons

    def render_users(self, page: int = 1):
        page_size = 40
        page = max(1, int(page))
        rows, total_users = _admin_user_ids_page(page, page_size)
        total_pages = max(1, (total_users + page_size - 1) // page_size)
        page = max(1, min(page, total_pages))
        if not rows and page > 1:
            rows, total_users = _admin_user_ids_page(page, page_size)

        text = (
            f'👥 **کاربران**\n\n'
            f'تعداد ثبت‌شده: **{total_users}**\n'
            f'صفحه: **{page}/{total_pages}**\n\n'
            'برای مشاهده جزئیات یک کاربر را انتخاب کنید:'
        )
        buttons = []
        for uid in rows:
            p = self._profile(uid)
            label = p['username'] and f"@{escape_md(p['username'])}" or escape_md(p['first_name']) or str(uid)
            buttons.append([ui.inline_button(f'👤 {label[:35]} • {uid}', f'adm2_user_{uid}', 'primary', icon=False)])
        if total_pages > 1:
            nav = []
            if page > 1:
                nav.append(ui.inline_button('⬅️ صفحه قبل', f'adm2_users_page_{page - 1}', 'secondary'))
            if page < total_pages:
                nav.append(ui.inline_button('صفحه بعد ➡️', f'adm2_users_page_{page + 1}', 'secondary'))
            if nav:
                buttons.append(nav)
        buttons.insert(0, [ui.inline_button('🔎 جستجوی کاربر', b'adm2_user_search', 'primary')])
        buttons.append([ui.inline_button('↩️ مدیریت', b'adm2_menu', 'secondary')])
        return text, buttons

    def render_user_management_detail(self, user_id: int):
        """سازگاری عقب: نمای مدیریت کاربر اکنون همان نمای واحد کامل است.

        قبلاً این نما به رکورد کیف پول SQLite وابسته بود و برای کاربر بدون رکورد
        «کاربر پیدا نشد» برمی‌گرداند؛ اکنون نمای واحد همیشه رندر می‌شود.
        """
        return self.render_user_detail(user_id)

    def render_tickets(self):
        rows = models.list_tickets(limit=40)
        counts = models.ticket_counts()
        text = (
            '🎫 **تیکت‌ها**\n\n'
            f"🟢 باز: **{counts['open']}**\n"
            f"⚪️ بسته: **{counts['closed']}**\n"
            f"📊 کل: **{counts['total']}**"
        )
        buttons = []
        for row in rows:
            mark = '🟢' if row['status'] == 'open' else '⚪️'
            buttons.append([ui.inline_button(
                f"{mark} تیکت #{row['id']}",
                f"adm2_ticket_{row['id']}", 'primary' if row['status'] == 'open' else 'secondary', icon=False
            )])
        buttons.append([ui.inline_button('↩️ مدیریت', b'adm2_menu', 'secondary')])
        return text, buttons

    def render_ticket(self, ticket_id: int):
        row = models.get_ticket(ticket_id)
        if not row:
            return '❌ تیکت پیدا نشد.', [[ui.inline_button('↩️ تیکت‌ها', b'adm2_tickets', 'secondary')]]
        status = '🟢 باز' if row['status'] == 'open' else '⚪️ بسته'
        text = (
            f"🎫 **تیکت #{ticket_id}**\n\n"
            f"وضعیت: **{status}**\n"
            f"کاربر: `{row['user_id']}`\n"
            f"ساخته‌شده: `{row['created_at']}`\n\n"
            + escape_md(ticket_manager.user_info_text(int(row['user_id'])))
        )
        buttons = []
        if row['status'] == 'open':
            buttons.append([
                ui.inline_button('✅ پاسخ', f'adm2_treply_{ticket_id}', 'success'),
                ui.inline_button('❌ بستن', f'adm2_tclose_{ticket_id}', 'danger'),
            ])
        buttons.append([ui.inline_button('↩️ تیکت‌ها', b'adm2_tickets', 'secondary')])
        return text, buttons

    def render_support_admin(self):
        rows = models.list_support_contacts()
        text = '🎧 **مدیریت پشتیبانی مستقیم**\n\n'
        if rows:
            text += '\n'.join(
                f"{i}- @{r['username']}" if r.get('username') else f"{i}- {r.get('target_id') or r['target']}"
                for i, r in enumerate(rows, 1)
            )
        else:
            text += 'هیچ پشتیبانی ثبت نشده است.'
        buttons = [[ui.inline_button('➕ افزودن پشتیبان', b'adm2_support_add', 'success')]]
        for row in rows[:30]:
            label = '@' + row['username'] if row.get('username') else str(row.get('target_id') or row['target'])
            buttons.append([ui.inline_button(f'🗑 {label}', f"adm2_support_del_{row['id']}", 'danger', icon=False)])
        buttons.append([ui.inline_button('↩️ مدیریت', b'adm2_menu', 'secondary')])
        return text, buttons

    def render_admins(self):
        rows = models.list_admins()
        text = f'🔐 **مدیریت ادمین‌ها**\n\n👑 مالک اصلی: `{ADMIN_ID}`\n'
        if rows:
            for i, row in enumerate(rows, 1):
                ident = f"@{row['username']}" if row.get('username') else str(row.get('user_id') or '-')
                role = 'Full' if row['role'] == 'full' else 'Limited'
                text += f"\n{i}- {ident} — **{role}**"
        else:
            text += '\nادمین اضافه‌ای ثبت نشده است.'
        buttons = [[ui.inline_button('➕ افزودن ادمین', b'adm2_admin_add', 'success')]]
        for row in rows[:30]:
            ident = f"@{row['username']}" if row.get('username') else str(row.get('user_id') or row['id'])
            buttons.append([ui.inline_button(f'🗑 حذف {ident}', f"adm2_admin_del_{row['id']}", 'danger', icon=False)])
        buttons.append([ui.inline_button('↩️ مدیریت', b'adm2_menu', 'secondary')])
        return text, buttons

    def render_permission_selector(self, uid: int):
        st = self._get_state(uid) or {}
        selected = set(st.get('permissions') or [])
        text = '🔐 **دسترسی‌های ادمین محدود**\n\nهر دسترسی را مستقل انتخاب کنید:'
        buttons = []
        for key in admin_manager.ALL_PERMISSIONS:
            mark = '☑️' if key in selected else '⬜️'
            buttons.append([ui.inline_button(f'{mark} {admin_manager.PERMISSION_LABELS[key]}', f'adm2_perm_{key}', 'primary', icon=False)])
        buttons += [
            [ui.inline_button('✅ ذخیره ادمین', b'adm2_perm_ok', 'success')],
            [ui.inline_button('❌ لغو', b'adm2_admins', 'danger')],
        ]
        return text, buttons

    def render_trials(self):
        c = models.trial_counts()
        rows = models.list_active_trials(limit=30)
        text = (
            '🎁 **مدیریت تست رایگان**\n\n'
            f"🟢 فعال: **{c['active']}**\n"
            f"✅ استفاده‌شده: **{c['used']}**\n"
            f"📊 کل رکوردها: **{c['total']}**"
        )
        buttons = [[ui.inline_button('🔎 مدیریت کاربر', b'adm2_trial_lookup', 'primary')]]
        if rows:
            text += f"\n\n⏳ نزدیک‌ترین انقضا: `{rows[0].get('expire_time') or '-'}`"
        buttons.append([ui.inline_button('↩️ مدیریت', b'adm2_menu', 'secondary')])
        return text, buttons

    def render_trial_user(self, user_id: int):
        row = trial_manager.refresh(user_id)
        if not row:
            status = '🔴 استفاده نشده'
            expiry = '-'
        elif int(row.get('active') or 0):
            status = '🟢 فعال'
            expiry = row.get('expire_time') or '-'
        else:
            status = '⚪️ استفاده شده / غیرفعال' if int(row.get('used') or 0) else '🔴 استفاده نشده'
            expiry = row.get('expire_time') or '-'
        text = (
            '🎁 **مدیریت تست کاربر**\n\n'
            f'کاربر: `{user_id}`\n'
            f'وضعیت: **{status}**\n'
            f'انقضا: `{expiry}`\n\n'
            'لغو تست، سابقه یک‌بارمصرف را پاک نمی‌کند.'
        )
        buttons = [
            [ui.inline_button(f'🎁 فعال/تمدید {TRIAL_DURATION_HOURS} ساعت', f'adm2_trial_grant_{user_id}', 'success')],
            [ui.inline_button('⛔ لغو دسترسی تست', f'adm2_trial_revoke_{user_id}', 'danger')],
            [ui.inline_button('↩️ تست رایگان', b'adm2_trials', 'secondary')],
        ]
        return text, buttons

    def render_stats(self):
        tc = models.ticket_counts(); tr = models.trial_counts()
        profile_ids = [int(p['user_id']) for p in models.list_profiles(limit=500)]
        users = db.count_users() + sum(1 for uid in profile_ids if not db.user_exists(uid))
        text = (
            '📊 **آمار مدیریتی**\n\n'
            f'👥 کاربران: **{users}**\n'
            f"🎫 تیکت باز: **{tc['open']}** / کل **{tc['total']}**\n"
            f"🎁 Trial فعال: **{tr['active']}** / استفاده‌شده **{tr['used']}**\n"
            f'🎧 پشتیبان مستقیم: **{len(models.list_support_contacts())}**\n'
            f'🔐 ادمین اضافه: **{len(models.list_admins())}**'
        )
        return text, [[ui.inline_button('🔄 بروزرسانی', b'adm2_stats', 'primary')], [ui.inline_button('↩️ مدیریت', b'adm2_menu', 'secondary')]]

    def render_system(self):
        g = db.get_global()
        text = (
            '⚙️ **تنظیمات سیستم**\n\n'
            f"🤖 ربات: **{'🟢 روشن' if g.get('bot_enabled') else '🔴 خاموش'}**\n"
            f"💳 کارت: `{g.get('card_number') or 'تنظیم نشده'}`\n"
            f"👤 صاحب حساب: **{g.get('card_holder') or 'تنظیم نشده'}**\n"
            f"🔒 جوین اجباری: `{', '.join(g.get('force_channels') or []) or 'غیرفعال'}`"
        )
        buttons = [
            [ui.inline_button('⏯ تغییر وضعیت ربات', b'adm2_system_toggle', 'primary')],
            [ui.inline_button('💳 تغییر شماره کارت', b'adm2_system_card', 'primary')],
            [ui.inline_button('👤 تغییر نام دریافت‌کننده', b'adm2_system_holder', 'primary')],
            [ui.inline_button('🔒 تغییر جوین اجباری', b'adm2_system_force', 'primary')],
            [ui.inline_button('↩️ مدیریت', b'adm2_menu', 'secondary')],
        ]
        return text, buttons

    async def handle_callback(self, event, data: str) -> bool:
        if not data.startswith('adm2_'):
            return False
        uid, username = await self._identity(event)
        if not admin_manager.is_admin(uid, username):
            await event.answer('⛔️ دسترسی ندارید.', alert=True)
            return True

        if data == 'adm2_menu':
            self._clear_state(uid)
            text, buttons = self.render_main(uid, username)
            await event.edit(text, buttons=buttons, parse_mode='md'); return True

        if data == 'adm2_users':
            if not admin_manager.can_any(uid, ('view_users','manage_users'), username): return await self._deny(event)
            text, buttons = self.render_users(); await event.edit(text, buttons=buttons, parse_mode='md'); return True
        if data.startswith('adm2_users_page_'):
            if not admin_manager.can_any(uid, ('view_users','manage_users'), username): return await self._deny(event)
            page = int(data.rsplit('_', 1)[1])
            text, buttons = self.render_users(page)
            await event.edit(text, buttons=buttons, parse_mode='md')
            return True
        if data == 'adm2_user_search':
            if not admin_manager.can_any(uid, ('view_users','manage_users'), username): return await self._deny(event)
            self._set_state(uid, {'step':'USER_SEARCH'})
            await event.edit('🔎 **جستجوی کاربر**\n\nID، Username یا نام کاربر را ارسال کنید:', buttons=[[ui.inline_button('❌ لغو', b'adm2_users', 'danger')]], parse_mode='md'); return True
        if data.startswith('adm2_user_ban_') or data.startswith('adm2_user_unban_'):
            if not self._allowed(uid, 'manage_users', username): return await self._deny(event)
            action = 'ban' if 'adm2_user_ban_' in data else 'unban'
            target = int(data.rsplit('_',1)[1])
            self._set_state(uid, {'step':'USER_CONFIRM','action':action,'target':target})
            msg = '🚫 تایید بن کاربر؟' if action == 'ban' else '✅ تایید آن بن کاربر؟'
            await event.edit(msg, buttons=[[ui.inline_button('تایید', f'adm2_user_confirm_{target}', 'success'), ui.inline_button('لغو', f'adm2_user_{target}', 'danger')]], parse_mode='md'); return True
        if data.startswith('adm2_user_confirm_'):
            if not self._allowed(uid, 'manage_users', username): return await self._deny(event)
            st=self._get_state(uid) or {}
            target=int(data.rsplit('_',1)[1])
            if st.get('target') != target: return True
            if st.get('action') == 'ban':
                models.ban_user(target)
                try: await self.bot.send_message(target, '🚫 حساب شما توسط مدیریت مسدود شده است.')
                except Exception: pass
            else:
                models.unban_user(target)
                try: await self.bot.send_message(target, '✅ حساب شما دوباره فعال شد.')
                except Exception: pass
            self._clear_state(uid)
            text, buttons = self.render_user_management_detail(target); await event.edit(text, buttons=buttons, parse_mode='md'); return True
        if data.startswith('adm2_user_'):
            if not admin_manager.can_any(uid, ('view_users','manage_users'), username): return await self._deny(event)
            target = int(data.rsplit('_', 1)[1]); text, buttons = self.render_user_management_detail(target)
            await event.edit(text, buttons=buttons, parse_mode='md'); return True
        if data.startswith('adm2_uplus_') or data.startswith('adm2_uminus_'):
            if not self._allowed(uid, 'manage_users', username): return await self._deny(event)
            target = int(data.rsplit('_', 1)[1])
            delta = 1 if data.startswith('adm2_uplus_') else -1
            self._set_state(uid, {'step':'USER_BALANCE_AMOUNT','target':target,'delta':delta})
            action = 'افزایش' if delta > 0 else 'کاهش'
            sign = '+' if delta > 0 else '-'
            current = int(db.get_user_settings(target).get('diamonds', 0) or 0)
            preset_rows = [
                [ui.inline_button(f'{sign}{amt}', f'adm2_uquick_{delta}_{amt}_{target}', 'primary', icon=False)
                 for amt in (10, 50, 100)],
                [ui.inline_button(f'{sign}{amt}', f'adm2_uquick_{delta}_{amt}_{target}', 'primary', icon=False)
                 for amt in (500, 1000)],
            ]
            await event.edit(
                f'💎 **{action} موجودی کاربر**\n\n'
                f'کاربر: {self._user_label(target)}\n'
                f'موجودی فعلی: **{current} الماس**\n\n'
                'مقدار الماس را ارسال کنید یا یکی از مقادیر پیشنهادی را انتخاب کنید:',
                buttons=preset_rows + [[ui.inline_button('❌ لغو', f'adm2_user_{target}', 'danger')]],
                parse_mode='md'
            ); return True

        if data.startswith('adm2_uquick_'):
            if not self._allowed(uid, 'manage_users', username): return await self._deny(event)
            try:
                _, _, delta_s, amount_s, target_s = data.split('_', 4)
                delta, amount, target = int(delta_s), int(amount_s), int(target_s)
            except Exception:
                await event.answer('دادهٔ دکمه نامعتبر است.', alert=True); return True
            if delta not in (1, -1) or amount <= 0:
                await event.answer('مقدار نامعتبر است.', alert=True); return True
            try:
                await event.answer()
            except Exception:
                pass
            await self._adjust_user_balance(event, uid, target, delta, amount)
            return True

        if data == 'adm2_tickets':
            if not admin_manager.can_any(uid, ('answer_tickets','manage_tickets'), username): return await self._deny(event)
            text, buttons = self.render_tickets(); await event.edit(text, buttons=buttons, parse_mode='md'); return True
        if data.startswith('adm2_ticket_'):
            if not admin_manager.can_any(uid, ('answer_tickets','manage_tickets'), username): return await self._deny(event)
            tid = int(data.rsplit('_',1)[1]); text, buttons = self.render_ticket(tid)
            await event.edit(text, buttons=buttons, parse_mode='md'); return True
        if data.startswith('adm2_treply_'):
            if not self._allowed(uid, 'answer_tickets', username): return await self._deny(event)
            tid = int(data.rsplit('_',1)[1]); ticket = models.get_ticket(tid)
            if not ticket or ticket['status'] != 'open':
                await event.answer('❌ تیکت باز نیست.', alert=True); return True
            models.assign_ticket(tid, uid)
            self._set_state(uid, {'step':'TICKET_REPLY','ticket_id':tid})
            await event.edit(f'✅ **پاسخ به تیکت #{tid}**\n\nپیام پاسخ را ارسال کنید. متن یا هر نوع فایل قابل ارسال است.', buttons=[[ui.inline_button('❌ لغو', f'adm2_ticket_{tid}', 'danger')]], parse_mode='md'); return True
        if data.startswith('adm2_tclose_'):
            if not self._allowed(uid, 'manage_tickets', username): return await self._deny(event)
            tid = int(data.rsplit('_',1)[1]); ticket = models.get_ticket(tid)
            if ticket and models.close_ticket(tid, uid):
                try: await self.bot.send_message(int(ticket['user_id']), f'❌ **تیکت #{tid} بسته شد.**', parse_mode='md')
                except Exception: pass
            text, buttons = self.render_ticket(tid); await event.edit(text, buttons=buttons, parse_mode='md'); return True

        if data == 'adm2_support':
            if not self._allowed(uid, 'manage_support', username): return await self._deny(event)
            self._clear_state(uid); text, buttons = self.render_support_admin(); await event.edit(text, buttons=buttons, parse_mode='md'); return True
        if data == 'adm2_support_add':
            if not self._allowed(uid, 'manage_support', username): return await self._deny(event)
            self._set_state(uid, {'step':'SUPPORT_ADD'})
            await event.edit('🎧 **افزودن پشتیبان**\n\nآیدی عددی یا username را ارسال کنید:\n`123456789` یا `@support`', buttons=[[ui.inline_button('❌ لغو', b'adm2_support', 'danger')]], parse_mode='md'); return True
        if data.startswith('adm2_support_del_'):
            if not self._allowed(uid, 'manage_support', username): return await self._deny(event)
            models.delete_support_contact(int(data.rsplit('_',1)[1])); text, buttons = self.render_support_admin(); await event.edit(text, buttons=buttons, parse_mode='md'); return True

        if data == 'adm2_admins':
            if not self._allowed(uid, 'manage_admins', username): return await self._deny(event)
            self._clear_state(uid); text, buttons = self.render_admins(); await event.edit(text, buttons=buttons, parse_mode='md'); return True
        if data == 'adm2_admin_add':
            if not self._allowed(uid, 'manage_admins', username): return await self._deny(event)
            self._set_state(uid, {'step':'ADMIN_TARGET'})
            await event.edit('🔐 **افزودن ادمین**\n\nID عددی یا Username را ارسال کنید:', buttons=[[ui.inline_button('❌ لغو', b'adm2_admins', 'danger')]], parse_mode='md'); return True
        if data.startswith('adm2_admin_del_'):
            if not self._allowed(uid, 'manage_admins', username): return await self._deny(event)
            models.delete_admin(int(data.rsplit('_',1)[1])); text, buttons = self.render_admins(); await event.edit(text, buttons=buttons, parse_mode='md'); return True
        if data in {'adm2_admin_role_full','adm2_admin_role_limited'}:
            if not self._allowed(uid, 'manage_admins', username): return await self._deny(event)
            st = self._get_state(uid)
            if not st or st.get('step') != 'ADMIN_ROLE':
                await event.answer('نشست منقضی شده است.', alert=True); return True
            if data.endswith('_full'):
                models.upsert_admin(user_id=st.get('target_id'), username=st.get('target_username'), role='full', permissions='*')
                self._clear_state(uid); text, buttons = self.render_admins(); await event.edit(text, buttons=buttons, parse_mode='md'); return True
            st['step']='ADMIN_PERMS'; st['permissions']=[]
            text, buttons = self.render_permission_selector(uid); await event.edit(text, buttons=buttons, parse_mode='md'); return True
        if data.startswith('adm2_perm_') and data != 'adm2_perm_ok':
            if not self._allowed(uid, 'manage_admins', username): return await self._deny(event)
            key = data[len('adm2_perm_'):]
            if key not in admin_manager.ALL_PERMISSIONS: return True
            st = self._get_state(uid)
            if not st or st.get('step') != 'ADMIN_PERMS': return True
            selected = set(st.get('permissions') or [])
            selected.symmetric_difference_update({key}); st['permissions'] = sorted(selected)
            text, buttons = self.render_permission_selector(uid); await event.edit(text, buttons=buttons, parse_mode='md'); return True
        if data == 'adm2_perm_ok':
            if not self._allowed(uid, 'manage_admins', username): return await self._deny(event)
            st = self._get_state(uid)
            if not st or st.get('step') != 'ADMIN_PERMS': return True
            if not st.get('permissions'):
                await event.answer('حداقل یک دسترسی انتخاب کنید.', alert=True); return True
            models.upsert_admin(user_id=st.get('target_id'), username=st.get('target_username'), role='limited', permissions=st['permissions'])
            self._clear_state(uid); text, buttons = self.render_admins(); await event.edit(text, buttons=buttons, parse_mode='md'); return True

        if data == 'adm2_trials':
            if not self._allowed(uid, 'manage_trial', username): return await self._deny(event)
            self._clear_state(uid); text, buttons = self.render_trials(); await event.edit(text, buttons=buttons, parse_mode='md'); return True
        if data == 'adm2_trial_lookup':
            if not self._allowed(uid, 'manage_trial', username): return await self._deny(event)
            self._set_state(uid, {'step':'TRIAL_LOOKUP'})
            await event.edit('🔎 **مدیریت Trial کاربر**\n\nID عددی یا Username کاربر را ارسال کنید:', buttons=[[ui.inline_button('❌ لغو', b'adm2_trials', 'danger')]], parse_mode='md'); return True
        if data.startswith('adm2_trialuser_'):
            if not self._allowed(uid, 'manage_trial', username): return await self._deny(event)
            target = int(data.rsplit('_',1)[1]); text, buttons = self.render_trial_user(target); await event.edit(text, buttons=buttons, parse_mode='md'); return True
        if data.startswith('adm2_trial_grant_'):
            if not self._allowed(uid, 'manage_trial', username): return await self._deny(event)
            target = int(data.rsplit('_',1)[1]); trial_manager.grant_admin_trial(target, TRIAL_DURATION_HOURS)
            text, buttons = self.render_trial_user(target); await event.edit(text, buttons=buttons, parse_mode='md'); return True
        if data.startswith('adm2_trial_revoke_'):
            if not self._allowed(uid, 'manage_trial', username): return await self._deny(event)
            target = int(data.rsplit('_',1)[1]); trial_manager.revoke(target)
            text, buttons = self.render_trial_user(target); await event.edit(text, buttons=buttons, parse_mode='md'); return True

        if data == 'adm2_stats':
            if not self._allowed(uid, 'view_stats', username): return await self._deny(event)
            text, buttons = self.render_stats(); await event.edit(text, buttons=buttons, parse_mode='md'); return True
        if data == 'adm2_broadcast':
            if not self._allowed(uid, 'broadcast', username): return await self._deny(event)
            self._set_state(uid, {'step':'BROADCAST'})
            await event.edit('📣 **ارسال همگانی**\n\nپیام را ارسال کنید. فقط برای کاربران ثبت‌شده همین ربات ارسال می‌شود.', buttons=[[ui.inline_button('❌ لغو', b'adm2_menu', 'danger')]], parse_mode='md'); return True

        if data == 'adm2_system':
            if not self._allowed(uid, 'system_settings', username): return await self._deny(event)
            self._clear_state(uid); text, buttons = self.render_system(); await event.edit(text, buttons=buttons, parse_mode='md'); return True
        if data == 'adm2_system_toggle':
            if not self._allowed(uid, 'system_settings', username): return await self._deny(event)
            g=db.get_global(); db.set_global({'bot_enabled':not bool(g.get('bot_enabled'))}); text,buttons=self.render_system(); await event.edit(text,buttons=buttons,parse_mode='md'); return True
        if data in {'adm2_system_card','adm2_system_holder','adm2_system_force'}:
            if not self._allowed(uid, 'system_settings', username): return await self._deny(event)
            step={'adm2_system_card':'SYS_CARD','adm2_system_holder':'SYS_HOLDER','adm2_system_force':'SYS_FORCE'}[data]
            self._set_state(uid, {'step':step})
            prompt={'SYS_CARD':'شماره کارت 16 رقمی را ارسال کنید:','SYS_HOLDER':'نام دریافت‌کننده را ارسال کنید:','SYS_FORCE':'کانال‌ها را با کاما ارسال کنید یا `off` برای غیرفعال:'}[step]
            await event.edit(f'⚙️ **تنظیمات سیستم**\n\n{prompt}',buttons=[[ui.inline_button('❌ لغو',b'adm2_system','danger')]],parse_mode='md'); return True

        return True

    async def _deny(self, event):
        await event.answer('⛔️ این دسترسی برای شما فعال نیست.', alert=True)
        return True

    async def _adjust_user_balance(self, event, uid: int, target: int, delta: int, amount: int):
        """مسیر واحد تغییر موجودی کاربر (ورود دستی + دکمه‌های سریع).

        تغییر از لایه رسمی balance_service.admin_adjust انجام می‌شود (دفتر کل
        append-only)؛ هشدار موجودی کاربر موقتاً خاموش و کاربر و مدیر مطلع می‌شوند
        و در پایان نمای تازهٔ کاربر بازگشت داده می‌شود.
        """
        target = int(target)
        delta = 1 if int(delta) > 0 else -1
        amount = int(amount)
        if amount <= 0:
            await event.reply('❌ مقدار باید عدد مثبت باشد.')
            return
        before = int(db.get_user_settings(target).get('diamonds', 0) or 0)
        from services.balance_service import admin_adjust
        try:
            after = admin_adjust(target, delta * amount, floor_zero=True, description='Admin user adjustment')
        except ValueError:
            await event.reply('❌ تغییر موجودی انجام نشد؛ مقدار درخواستی با موجودی فعلی سازگار نیست.')
            return
        db.update_user_settings(target, {'last_alert_hours': 9999})
        st = self._get_state(uid)
        if st and st.get('step') == 'USER_BALANCE_AMOUNT' and int(st.get('target') or 0) == target:
            self._clear_state(uid)
        await event.reply(
            f'✅ موجودی کاربر {self._user_label(target)} تغییر کرد.\n'
            f'قبل: **{before}** | بعد: **{after}** الماس',
            parse_mode='md'
        )
        try:
            if delta * amount > 0:
                await self.bot.send_message(
                    target,
                    f'💎 تعداد {amount} الماس به حساب شما اضافه شد.\n\nموجودی فعلی:\n{after} 💎'
                )
            else:
                await self.bot.send_message(
                    target,
                    f'💎 تعداد {amount} الماس از حساب شما کسر شد.\n\nموجودی فعلی:\n{after} 💎'
                )
        except Exception:
            pass
        text2, buttons = self.render_user_detail(target)
        await self.bot.send_message(event.chat_id, text2, buttons=buttons, parse_mode='md')

    async def handle_emoji_curation(self, event) -> bool:
        text = (event.raw_text or '').strip()
        parts = text.split()
        command = parts[0] if parts else ''
        if command not in ('/emoji_curation', '/emoji_curation_samples'):
            return False
        # Owner only, not delegated/full admins. Never echo user input.
        if event.sender_id != ADMIN_ID:
            await event.reply('Unauthorized: ADMIN_ID only', parse_mode=None)
            return True
        if not event.is_private:
            await event.reply('Use this command in the private admin chat', parse_mode=None)
            return True
        from services.session_restore import session_user_id
        target = session_user_id('user_' + parts[1]) if len(parts) == 2 else None
        if target is None:
            await event.reply('Usage: /emoji_curation <user_id> or /emoji_curation_samples <user_id>', parse_mode=None)
            return True
        import self_manager
        client = self_manager.get_client_for_user(target)
        if client is None or not client.is_connected():
            await event.reply('Self client is not online', parse_mode=None)
            return True
        # Reject overlapping runs; do not queue or replay sample sends.
        if getattr(self, '_emoji_curation_busy', False):
            await event.reply('Emoji curation is already running', parse_mode=None)
            return True
        self._emoji_curation_busy = True
        try:
            from tools.premium_emoji_live_smoke import runtime_curation
            directory, status = await runtime_curation(target,
                send_samples=command == '/emoji_curation_samples')
            await event.reply('Curation saved: premium_emoji_curation/' + directory.name +
                '\nStatus: ' + status['status'] +
                '\nValid: ' + str(status['valid_document_ids']) +
                ' | Rejected: ' + str(status['rejected_document_ids']) +
                ' | Unresolved: ' + str(status['unresolved_document_ids']) +
                '\nVisual review still required.', parse_mode=None)
        except Exception as exc:
            await event.reply('Emoji curation failed: ' + type(exc).__name__, parse_mode=None)
        finally:
            self._emoji_curation_busy = False
        return True

    async def handle_message(self, event) -> bool:
        if await self.handle_emoji_curation(event):
            return True
        uid, username = await self._identity(event)
        st = self._get_state(uid)
        if not st or not admin_manager.is_admin(uid, username):
            return False
        step = st.get('step')
        if step in {'SYS_CARD', 'SYS_HOLDER', 'SYS_FORCE'} and not self._allowed(uid, 'system_settings', username):
            self._clear_state(uid)
            await event.reply('دسترسی تغییر تنظیمات برای شما فعال نیست.')
            return True
        text = (event.raw_text or '').strip()

        if step == 'USER_SEARCH':
            if not self._allowed(uid, 'view_users', username) and not self._allowed(uid, 'manage_users', username):
                self._clear_state(uid); return False
            rows = models.search_users(text)
            self._clear_state(uid)
            target = None
            if rows:
                target = int(rows[0]['telegram_id'])
            else:
                # ریشه‌ای: کاربر ممکن است هنوز رکورد کیف پول نداشته باشد؛
                # از مسیر حل هویت (ID عددی / یوزرنیم / تلگرام) جستجو را کامل کن.
                try:
                    resolved = await self.resolve_user(text)
                except Exception:
                    resolved = None
                if resolved:
                    target = int(resolved)
            if not target:
                await event.reply('❌ کاربری پیدا نشد.'); return True
            text2, buttons = self.render_user_detail(target)
            await event.reply(text2, buttons=buttons, parse_mode='md'); return True

        if step == 'USER_BALANCE_AMOUNT':
            if not self._allowed(uid, 'manage_users', username): self._clear_state(uid); return False
            clean = text.replace(',', '').strip()
            if not clean.isdigit() or int(clean) <= 0:
                await event.reply('❌ مقدار باید عدد مثبت باشد.'); return True
            try:
                target = int(st.get('target') or 0)
                delta = int(st.get('delta') or 0)
            except Exception:
                target, delta = 0, 0
            if target <= 0 or delta not in (1, -1):
                self._clear_state(uid)
                await event.reply('❌ نشست منقضی شده است. از بخش کاربران دوباره شروع کنید.')
                return True
            await self._adjust_user_balance(event, uid, target, delta, int(clean))
            return True

        if step == 'TICKET_REPLY':
            if not self._allowed(uid, 'answer_tickets', username): self._clear_state(uid); return False
            tid=int(st['ticket_id']); ticket=models.get_ticket(tid)
            if not ticket or ticket['status']!='open':
                self._clear_state(uid); await event.reply('❌ تیکت دیگر باز نیست.'); return True
            msg_type=ticket_manager.detect_message_type(event.message)
            models.add_ticket_message(tid,sender_role='admin',sender_id=uid,message_type=msg_type,text=text or None,telegram_message_id=event.id)
            target=int(ticket['user_id'])
            try:
                await self.bot.send_message(target, ticket_manager.admin_reply_header(tid), parse_mode='md')
                try: await self.bot.forward_messages(target, event.message)
                except Exception:
                    if text: await self.bot.send_message(target,text)
                await event.reply(f'✅ پاسخ تیکت `#{tid}` برای کاربر ارسال شد.',parse_mode='md')
            except Exception as exc:
                await event.reply(f'❌ ارسال پاسخ ناموفق بود: `{escape_md(exc)}`',parse_mode='md')
            self._clear_state(uid); return True

        if step == 'SUPPORT_ADD':
            if not self._allowed(uid,'manage_support',username): return False
            token=text.strip(); target_id=None; uname=None; display=None
            if not token:
                await event.reply('❌ مقدار خالی است.'); return True
            try:
                ent=await self.bot.get_entity(token)
                target_id=getattr(ent,'id',None); uname=getattr(ent,'username',None); display=getattr(ent,'first_name',None) or getattr(ent,'title',None)
            except Exception:
                if token.lstrip('-').isdigit(): target_id=int(token)
                elif token.startswith('@'): uname=token[1:]
                else:
                    await event.reply('❌ ID یا Username معتبر ارسال کنید.'); return True
            canonical='@'+uname.lower() if uname else str(target_id)
            models.add_support_contact(target=canonical,target_id=target_id,username=uname,display_name=display,created_by=uid)
            self._clear_state(uid); await event.reply('✅ پشتیبان ذخیره شد.')
            text2,buttons=self.render_support_admin(); await self.bot.send_message(event.chat_id,text2,buttons=buttons,parse_mode='md'); return True

        if step == 'ADMIN_TARGET':
            if not self._allowed(uid,'manage_admins',username): return False
            token=text.strip(); target_id=None; uname=None
            if token.lstrip('-').isdigit():
                target_id=int(token)
                try:
                    ent=await self.bot.get_entity(target_id); uname=getattr(ent,'username',None)
                except Exception: pass
            elif token.startswith('@'):
                uname=token[1:].lower()
                try:
                    ent=await self.bot.get_entity(token)
                    target_id=getattr(ent,'id',None)
                    uname=getattr(ent,'username',None) or uname
                except Exception:
                    await event.reply('❌ این Username توسط تلگرام قابل شناسایی نیست. برای امنیت، ID عددی را ارسال کنید.')
                    return True
                if target_id is None:
                    await event.reply('❌ شناسه عددی این Username دریافت نشد. ID عددی را ارسال کنید.')
                    return True
            else:
                await event.reply('❌ ID عددی یا Username با @ ارسال کنید.'); return True
            if target_id == int(ADMIN_ID):
                await event.reply('👑 مالک اصلی از قبل دسترسی کامل دارد.'); return True
            st.update({'step':'ADMIN_ROLE','target_id':target_id,'target_username':uname})
            await event.reply('نوع ادمین را انتخاب کنید:',buttons=[
                [ui.inline_button('👑 ادمین فول',b'adm2_admin_role_full','success')],
                [ui.inline_button('🛡 ادمین محدود',b'adm2_admin_role_limited','primary')],
                [ui.inline_button('❌ لغو',b'adm2_admins','danger')],
            ]); return True

        if step == 'TRIAL_LOOKUP':
            if not self._allowed(uid,'manage_trial',username): return False
            target=await self.resolve_user(text)
            if not target:
                await event.reply('❌ کاربر پیدا نشد.'); return True
            self._clear_state(uid); text2,buttons=self.render_trial_user(int(target)); await self.bot.send_message(event.chat_id,text2,buttons=buttons,parse_mode='md'); return True

        if step == 'BROADCAST':
            if not self._allowed(uid,'broadcast',username): return False
            def broadcast_targets():
                import itertools
                seen = set()
                for target in itertools.chain(db.iter_user_ids(), (int(p['user_id']) for p in models.list_profiles(limit=500))):
                    if target not in seen:
                        seen.add(target)
                        yield target

            async def sender(target):
                if event.media:
                    await self.bot.forward_messages(target, event.message)
                else:
                    await self.bot.send_message(target, text)

            report_future = await broadcast_queue.add(BroadcastJob(
                targets=broadcast_targets(),
                sender=sender,
                batch_size=20,
                delay=1.0,
                retries=2,
            ))
            report = await report_future
            self._clear_state(uid)
            await event.reply(
                f'📣 ارسال همگانی پایان یافت.\n'
                f'✅ موفق: **{report.success}**\n'
                f'❌ ناموفق: **{report.failed}**\n'
                f'🚫 بلاک کرده: **{report.blocked}**',
                parse_mode='md'
            )
            return True

        if step == 'SYS_CARD':
            card=text.replace(' ','').replace('-','')
            if not card.isdigit() or len(card)!=16:
                await event.reply('❌ شماره کارت باید 16 رقم باشد.'); return True
            pretty='-'.join(card[i:i+4] for i in range(0,16,4)); db.set_global({'card_number':pretty})
        elif step == 'SYS_HOLDER':
            if not text: await event.reply('❌ نام خالی است.'); return True
            db.set_global({'card_holder':text})
        elif step == 'SYS_FORCE':
            if text.lower() in {'off','خاموش','none'}: db.set_global({'force_channels':[]})
            else:
                items=[x.strip() for x in text.replace('\n',',').split(',') if x.strip()]
                if not items: await event.reply('❌ مقدار نامعتبر است.'); return True
                db.set_global({'force_channels':items})
        else:
            return False
        self._clear_state(uid); await event.reply('✅ تنظیمات ذخیره شد.')
        text2,buttons=self.render_system(); await self.bot.send_message(event.chat_id,text2,buttons=buttons,parse_mode='md'); return True
