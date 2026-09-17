"""AI assistant handler/controller."""
from __future__ import annotations

import re

import ui
from config import AI_MODEL, AI_SYSTEM_PROMPT
from database import models
from services import memory
from services.ai_assistant import (
    RateLimitExceeded,
    ask_ai,
    split_telegram_text,
)
from services.avalai_ai import AvalAIChatError
from services.logging_service import log_api_error


_AI_RE = re.compile(
    r'^\s*\.(?:ai|هوش(?:\s+مصنوعی)?)(?:\s+(.*))?$',
    re.IGNORECASE | re.DOTALL,
)


def extract_ai_prompt(text: str) -> str | None:
    match = _AI_RE.match(text or '')
    if not match:
        return None
    return (match.group(1) or '').strip()


def is_ai_command(text: str) -> bool:
    return bool(_AI_RE.match(text or ''))


def ai_help_text() -> str:
    return (
        '🤖 هوش مصنوعی\n\n'
        'نمونه‌ها:\n'
        '.ai سلام خوبی؟\n'
        '.هوش یک ایده برای پروژه بده\n'
        '.هوش مصنوعی درباره هوش مصنوعی توضیح بده\n\n'
        'برای سؤال‌های زمان‌حساس، در صورت تنظیم SEARCH_API_KEY سرچ اینترنت خودکار انجام می‌شود.'
    )


async def execute_ai_text(user_id: int, raw_text: str) -> str:
    prompt = extract_ai_prompt(raw_text)
    if prompt is None:
        return ai_help_text()
    if not prompt:
        return ai_help_text()
    try:
        result = await ask_ai(int(user_id), prompt)
        return result.text
    except RateLimitExceeded as exc:
        return f'⏳ تعداد درخواست‌ها زیاد است. حدود {exc.retry_after} ثانیه دیگر دوباره تلاش کن.'
    except AvalAIChatError as exc:
        log_api_error('ai_request', exc, user_id=int(user_id))
        return f'⚠️ پاسخ AvalAI دریافت نشد: {exc}'
    except Exception as exc:
        log_api_error('ai_request_unexpected', exc, user_id=int(user_id))
        return '⚠️ هوش مصنوعی موقتاً در دسترس نیست.'


def ai_menu(user_id: int, *, is_admin: bool = False):
    count = memory.count_messages(int(user_id))
    text = (
        '🤖 **هوش مصنوعی AvalAI**\n\n'
        f'🧩 مدل: `{AI_MODEL}`\n'
        f'🧠 پیام‌های حافظه: **{count}/20**\n\n'
        'چت، تحلیل، کد، ترجمه و ایده‌پردازی پشتیبانی می‌شود. '
        'سؤال‌های نیازمند اطلاعات جدید به‌صورت خودکار برای سرچ اینترنت بررسی می‌شوند.'
    )
    buttons = [
        [ui.inline_button('💬 شروع گفتگو', b'ai_chat', 'primary')],
        [
            ui.inline_button('🧠 حافظه من', b'ai_memory', 'primary'),
            ui.inline_button('📊 تعداد پیام‌ها', b'ai_count', 'secondary'),
        ],
        [ui.inline_button('🗑 پاک کردن حافظه', b'ai_clear', 'danger')],
    ]
    if is_admin:
        buttons.append([ui.inline_button('⚙️ System Prompt', b'ai_system_prompt', 'secondary')])
    buttons.append([ui.inline_button('↩️ بازگشت', b'main_menu', 'secondary')])
    return text, buttons


def _memory_view(user_id: int) -> str:
    history = models.get_ai_history(int(user_id), limit=20)
    if not history:
        return '🧠 حافظه من\n\nهنوز پیامی در حافظه ذخیره نشده است.'
    lines = ['🧠 حافظه من', '', f'آخرین {len(history)} پیام ذخیره‌شده:', '']
    for item in history:
        icon = '👤' if item['role'] == 'user' else '🤖'
        text = ' '.join(str(item['message']).split())
        if len(text) > 180:
            text = text[:177] + '...'
        lines.append(f'{icon} {text}')
    return '\n'.join(lines)


class AIController:
    def __init__(self, bot, *, admin_check=None):
        self.bot = bot
        self.states: dict[int, dict] = {}
        self.admin_check = admin_check or (lambda _uid: False)

    def is_admin(self, uid: int) -> bool:
        try:
            return bool(self.admin_check(int(uid)))
        except Exception:
            return False

    def has_state(self, uid: int) -> bool:
        return int(uid) in self.states

    def cancel(self, uid: int) -> None:
        self.states.pop(int(uid), None)

    async def _send_ai_answer(self, event, prompt: str) -> None:
        raw = f'.ai {prompt}'
        answer = await execute_ai_text(int(event.sender_id), raw)
        chunks = split_telegram_text(answer)
        for idx, chunk in enumerate(chunks):
            buttons = None
            if idx == len(chunks) - 1:
                buttons = [[ui.inline_button('↩️ هوش مصنوعی', b'ai_menu', 'secondary')]]
            await event.reply(chunk, buttons=buttons, parse_mode=None)

    async def handle_message(self, event) -> bool:
        uid = int(event.sender_id)
        state = self.states.get(uid)
        if not state:
            return False
        text = (event.text or '').strip()
        if text.startswith('.'):
            return False
        if text.lower() in {'لغو', 'cancel'}:
            self.cancel(uid)
            menu_text, buttons = ai_menu(uid, is_admin=self.is_admin(uid))
            await event.reply(menu_text, buttons=buttons, parse_mode='md')
            return True

        step = state.get('step')
        if step == 'chat':
            if not text:
                await event.reply('❌ یک پیام متنی ارسال کن.')
                return True
            await self._send_ai_answer(event, text)
            # Keep chat mode active for natural multi-turn conversation.
            return True

        if step == 'system_prompt':
            if not self.is_admin(uid):
                self.cancel(uid)
                return True
            try:
                models.set_ai_system_prompt(text)
            except ValueError:
                await event.reply('❌ System Prompt باید بین 10 تا 8000 کاراکتر باشد.')
                return True
            self.cancel(uid)
            await event.reply(
                '✅ System Prompt ذخیره شد.',
                buttons=[[ui.inline_button('↩️ هوش مصنوعی', b'ai_menu', 'secondary')]],
            )
            return True
        return False

    async def handle_callback(self, event, data: str) -> bool:
        uid = int(event.sender_id)
        admin = self.is_admin(uid)
        if data == 'ai_menu':
            self.cancel(uid)
            text, buttons = ai_menu(uid, is_admin=admin)
            await event.edit(text, buttons=buttons, parse_mode='md')
            return True
        if data == 'ai_chat':
            self.states[uid] = {'step': 'chat'}
            await event.edit(
                '💬 **حالت گفتگو فعال شد.**\n\n'
                'پیام بعدی را بفرست. حافظه 20 پیام آخر برای همین کاربر استفاده می‌شود.\n'
                'برای خروج بنویس `لغو`.',
                buttons=[[ui.inline_button('✖️ خروج از گفتگو', b'ai_menu', 'danger')]],
                parse_mode='md',
            )
            return True
        if data == 'ai_memory':
            text = _memory_view(uid)
            await event.edit(
                text,
                buttons=[[ui.inline_button('↩️ هوش مصنوعی', b'ai_menu', 'secondary')]],
                parse_mode=None,
            )
            return True
        if data == 'ai_count':
            count = memory.count_messages(uid)
            await event.answer(f'تعداد پیام‌های ذخیره‌شده: {count} از 20', alert=True)
            return True
        if data == 'ai_clear':
            await event.edit(
                '🗑 **پاک کردن حافظه**\n\nتمام تاریخچه AI شما حذف شود؟',
                buttons=[
                    [ui.inline_button('✅ بله، پاک کن', b'ai_clear_yes', 'danger')],
                    [ui.inline_button('↩️ انصراف', b'ai_menu', 'secondary')],
                ],
                parse_mode='md',
            )
            return True
        if data == 'ai_clear_yes':
            deleted = memory.clear_history(uid)
            await event.answer(f'{deleted} پیام حذف شد.', alert=True)
            text, buttons = ai_menu(uid, is_admin=admin)
            await event.edit(text, buttons=buttons, parse_mode='md')
            return True
        if data == 'ai_system_prompt':
            if not admin:
                await event.answer('⛔️ فقط ادمین دسترسی دارد.', alert=True)
                return True
            self.states[uid] = {'step': 'system_prompt'}
            current = models.get_ai_system_prompt(AI_SYSTEM_PROMPT)
            preview = current if len(current) <= 1000 else current[:997] + '...'
            await event.edit(
                '⚙️ **System Prompt فعلی**\n\n'
                f'{preview}\n\n'
                'System Prompt جدید را به‌صورت پیام ارسال کن.',
                buttons=[[ui.inline_button('✖️ لغو', b'ai_menu', 'danger')]],
                parse_mode=None,
            )
            return True
        return False
