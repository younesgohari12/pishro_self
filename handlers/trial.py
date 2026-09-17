from __future__ import annotations

from login_manager import start_login
from services import trial_manager
import ui
from config import TRIAL_DURATION_HOURS


class TrialController:
    def __init__(self, bot):
        self.bot = bot

    def render_status(self, user_id: int):
        row = trial_manager.refresh(user_id)
        if row and int(row.get('used') or 0):
            if int(row.get('active') or 0):
                remain = trial_manager.remaining_seconds(user_id)
                h, rem = divmod(remain, 3600)
                m = rem // 60
                text = (
                    '🎁 **تست رایگان**\n\n'
                    '🟢 وضعیت: **فعال**\n'
                    f'⏳ زمان باقی‌مانده: **{h} ساعت و {m} دقیقه**\n'
                    f"🕐 انقضا: `{row.get('expire_time') or '-'}`"
                )
            else:
                text = '❌ **شما قبلاً از تست رایگان استفاده کرده‌اید.**'
            return text, [[ui.inline_button('↩️ بازگشت', b'main_menu', 'secondary')]]

        text = (
            f'🎁 **{TRIAL_DURATION_HOURS} تست رایگان**\n\n'
            'برای فعال‌سازی تست، ورود امن تلگرام را کامل کنید.\n'
            f'پس از ورود موفق، دسترسی آزمایشی دقیقاً **{TRIAL_DURATION_HOURS} ساعت** فعال می‌شود.\n\n'
            'هر حساب فقط یک بار می‌تواند از تست رایگان استفاده کند.'
        )
        buttons = [
            [ui.inline_button('🚀 شروع تست رایگان', b'trial_begin', 'success')],
            [ui.inline_button('↩️ بازگشت', b'main_menu', 'secondary')],
        ]
        return text, buttons

    async def handle_callback(self, event, data: str) -> bool:
        if data not in {'trial_menu', 'trial_begin'}:
            return False
        uid = int(event.sender_id)
        if data == 'trial_menu':
            text, buttons = self.render_status(uid)
            await event.edit(text, buttons=buttons, parse_mode='md')
            return True

        if trial_manager.has_used(uid):
            await event.answer('❌ شما قبلاً از تست رایگان استفاده کرده‌اید.', alert=True)
            text, buttons = self.render_status(uid)
            await event.edit(text, buttons=buttons, parse_mode='md')
            return True

        try:
            await start_login(self.bot, event.chat_id, uid, purpose='free_trial')
            try:
                await event.delete()
            except Exception:
                pass
        except Exception as exc:
            await event.answer(f'❌ شروع ورود ناموفق بود: {exc}', alert=True)
        return True
