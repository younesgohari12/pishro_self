"""مدیریت ورود کاربران - نسخه حرفه‌ای با دکمه‌های رنگی"""
import os
import asyncio
import re
import time
import tempfile

from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from telethon.tl.types import ReplyKeyboardHide, auth

from config import API_ID, API_HASH, SESSIONS_DIR, TRIAL_DURATION_HOURS

import ui

login_states = {}
_request_after = {}
LOGIN_TTL = 600


async def _has_existing_self(uid):
    """جلوگیری از لاگین مجدد وقتی سلف فعلی هنوز فعال است."""
    try:
        import self_manager

        client = self_manager.active_clients.get(int(uid))
        if client is not None:
            try:
                if await client.is_connected():
                    return True
            except Exception:
                return True

        for session_name in (f"user_{int(uid)}", str(uid)):
            task = self_manager.running_sessions.get(session_name)
            if task is not None and not task.done():
                return True
    except Exception:
        pass

    try:
        session_file = os.path.join(SESSIONS_DIR, f"user_{int(uid)}.txt")
        if os.path.isfile(session_file):
            with open(session_file, "r", encoding="utf-8-sig") as f:
                if f.read().strip():
                    return True
    except Exception:
        pass

    return False


def active_state(uid, state, chat_id):
    return (login_states.get(uid) is state and chat_id == uid == state.get('chat_id')
            and time.monotonic() < state.get('expires_at', 0))


def own_phone(uid, contact):
    if contact is None or getattr(contact, 'user_id', None) != uid:
        raise ValueError('برای اتصال فقط دکمه «ارسال شماره من» را بزنید؛ شماره دستی یا مخاطب دیگر پذیرفته نیست.')
    phone = str(getattr(contact, 'phone_number', '') or '').strip()
    if not phone.startswith('+'):
        phone = '+' + phone
    if not re.fullmatch(r'\+[1-9][0-9]{7,14}', phone):
        raise ValueError('شماره معتبر با کد کشور لازم است.')
    return phone



def number_keyboard(uid):
    """کیبورد عددی برای کد ورود"""
    return [
        [
            ui.inline_button("1", f"code_{uid}_1", "primary", icon=False),
            ui.inline_button("2", f"code_{uid}_2", "primary", icon=False),
            ui.inline_button("3", f"code_{uid}_3", "primary", icon=False),
        ],
        [
            ui.inline_button("4", f"code_{uid}_4", "primary", icon=False),
            ui.inline_button("5", f"code_{uid}_5", "primary", icon=False),
            ui.inline_button("6", f"code_{uid}_6", "primary", icon=False),
        ],
        [
            ui.inline_button("7", f"code_{uid}_7", "primary", icon=False),
            ui.inline_button("8", f"code_{uid}_8", "primary", icon=False),
            ui.inline_button("9", f"code_{uid}_9", "primary", icon=False),
        ],
        [
            ui.inline_button("⌫", f"code_{uid}_del", "danger", icon=False),
            ui.inline_button("0", f"code_{uid}_0", "primary", icon=False),
            ui.inline_button("✅", f"code_{uid}_ok", "success", icon=False),
        ],
        [
            ui.inline_button("✖️ لغو ورود", f"code_{uid}_cancel", "danger")
        ],
    ]


async def _disconnect(client):
    try:
        await client.disconnect()
    except Exception:
        pass


def cleanup_state(uid, disconnect=True, *, expected=None):
    """Discard only the intended attempt; a stale task cannot cancel a new one."""
    state = login_states.get(uid)
    if not state or (expected is not None and state is not expected):
        return
    login_states.pop(uid, None)
    handle = state.pop('_expiry_handle', None)
    if handle:
        handle.cancel()
    if disconnect and state.get('client'):
        asyncio.get_running_loop().create_task(_disconnect(state['client']))


def save_session_atomic(session_file, session_string):
    """Create private bytes first; failed writes never truncate the old session."""
    os.makedirs(os.path.dirname(session_file), exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                         dir=os.path.dirname(session_file), delete=False) as handle:
            temporary = handle.name
            os.chmod(temporary, 0o600)
            handle.write(session_string)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, session_file)
        temporary = None
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


async def start_login(bot, chat_id, uid, purpose="install"):
    """شروع فرآیند اتصال. purpose may be install or free_trial."""
    if chat_id != uid:
        await bot.send_message(chat_id, 'اتصال حساب فقط در گفتگوی خصوصی ربات امکان دارد.')
        return

    if await _has_existing_self(uid):
        await bot.send_message(chat_id, 'شما قبلاً وارد شده‌اید. ابتدا سلف فعلی را حذف کنید یا از حساب خارج شوید.')
        return
    existing = login_states.get(uid)
    if existing and existing.get('step') == 'FINALIZING':
        return await bot.send_message(chat_id, 'اتصال در حال نهایی‌شدن است؛ کمی صبر کنید.')
    cleanup_state(uid, disconnect=True)

    login_states[uid] = {
        'expires_at': time.monotonic() + LOGIN_TTL,
        'step': 'WAIT_PHONE',
        'phone': None,
        'client': None,
        'hash': None,
        'code': '',
        'chat_id': chat_id,
        'purpose': purpose
    }

    state = login_states[uid]
    state['_expiry_handle'] = asyncio.get_running_loop().call_later(
        LOGIN_TTL, lambda: cleanup_state(uid, expected=state))

    # Important: pass Telethon Button wrappers directly to send_message.
    # Manually putting custom.Button objects inside KeyboardButtonRow causes
    # serialization errors and made the install menu disappear with no prompt.
    phone_keyboard = [
        [ui.request_phone_button(
            "📲 ارسال شماره من",
            "success",
            resize=True,
            single_use=True
        )],
        [ui.text_button("✖️ لغو", "danger")],
    ]

    try:
        return await bot.send_message(
            chat_id,
            ui.INSTALL_PHONE_PROMPT,
            buttons=phone_keyboard,
            parse_mode='md'
        )
    except Exception:
        # Do not leave a stale WAIT_PHONE state when the prompt could not be sent.
        cleanup_state(uid, disconnect=True, expected=state)
        raise


async def send_code_request(bot, chat_id, uid, phone=None, *, contact=None):
    """Only the sender's own Telegram contact may initiate an auth request."""
    state = login_states.get(uid)
    if not state or not active_state(uid, state, chat_id) or state.get('step') != 'WAIT_PHONE':
        return
    try:
        phone = own_phone(uid, contact)
    except ValueError as exc:
        await bot.send_message(chat_id, str(exc))
        return
    now = time.monotonic()
    if _request_after.get(uid, 0) > now:
        await bot.send_message(chat_id, 'لطفاً کمی صبر کنید و دوباره تلاش کنید.')
        return
    # Claim the state before the first await; duplicate contacts cannot send twice.
    state['step'] = 'SENDING_CODE'
    _request_after[uid] = now + 60
    client = None
    try:
        await bot.send_message(chat_id, '⏳ در حال برقراری ارتباط امن با تلگرام...',
                               buttons=ReplyKeyboardHide())
        if not active_state(uid, state, chat_id):
            return
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        state['client'] = client
        state['phone'] = phone
        await asyncio.wait_for(client.connect(), timeout=40)
        if not active_state(uid, state, chat_id):
            return
        result = await asyncio.wait_for(client.send_code_request(phone), timeout=40)
        if not active_state(uid, state, chat_id):
            return
        # A nonempty hash alone is insufficient (e.g. payment-required response).
        if not isinstance(result, auth.SentCode) or not result.phone_code_hash:
            raise ValueError('تلگرام ارسال کد ورود قابل استفاده را تأیید نکرد. دوباره تلاش کنید.')
        length = getattr(result.type, 'length', None)
        if type(length) is not int or not 4 <= length <= 8:
            raise ValueError('روش تأیید درخواست‌شده توسط تلگرام در این نسخه پشتیبانی نمی‌شود.')
        state.update(step='WAIT_CODE', hash=result.phone_code_hash, code='', code_length=length)
        message = await bot.send_message(
            chat_id, f"📩 تلگرام درخواست کد را پذیرفت.\nکد {length} رقمی دریافتی را وارد کنید.\n\n📱 شماره: `{phone}`",
            buttons=number_keyboard(uid), parse_mode='md')
        state['code_message_id'] = message.id
    except Exception as exc:
        if isinstance(exc, errors.FloodWaitError):
            _request_after[uid] = time.monotonic() + max(60, exc.seconds)
            message = f'تلگرام درخواست جدید را محدود کرده؛ {exc.seconds} ثانیه صبر کنید.'
        elif isinstance(exc, errors.PhoneNumberInvalidError):
            message = 'شماره ارسال‌شده معتبر نیست.'
        elif isinstance(exc, errors.PhoneNumberUnoccupiedError):
            message = 'این شماره حساب تلگرام قابل ورود ندارد.'
        elif isinstance(exc, errors.PhoneNumberBannedError):
            message = 'این شماره توسط تلگرام محدود شده است.'
        elif isinstance(exc, ValueError):
            message = str(exc)
        else:
            message = 'ارتباط یا درخواست کد ناموفق بود؛ دوباره از منوی اتصال شروع کنید.'
        cleanup_state(uid, expected=state)
        await bot.send_message(chat_id, f'❌ {message}')
    finally:
        if client and not active_state(uid, state, chat_id):
            cleanup_state(uid, expected=state)
            await _disconnect(client)


async def handle_code_button(event, uid, action):
    """مدیریت دکمه‌های کد ورود"""
    if uid not in login_states:
        await event.answer("⚠️ نشست شما منقضی شده است.", alert=True)
        return

    state = login_states[uid]
    if event.sender_id != uid or not active_state(uid, state, event.chat_id):
        await event.answer('نشست معتبر نیست.', alert=True)
        return
    if state.get('code_message_id') != event.message_id:
        await event.answer('این صفحه‌کلید منقضی شده است.', alert=True)
        return
    if action != 'cancel' and state.get('step') != 'WAIT_CODE':
        await event.answer('لطفاً منتظر بمانید.', alert=True)
        return

    # --------------------------------
    # لغو
    # --------------------------------
    if action == 'cancel':
        if state.get('step') == 'FINALIZING':
            await event.answer('اتصال در حال نهایی‌شدن است.', alert=True)
            return
        cleanup_state(uid, disconnect=True, expected=state)
        await event.answer("در حال لغو...", alert=False)

        try:
            await event.client.send_message(
                event.chat_id,
                ui.LOGIN_CANCELLED,
                buttons=ReplyKeyboardHide(),
                parse_mode='md'
            )
        except Exception:
            pass
        return

    # --------------------------------
    # حذف کاراکتر
    # --------------------------------
    if action == 'del':
        state['code'] = state['code'][:-1]
        display = state['code'] or '—'

        await event.answer(f"🔢 {display}", alert=False)

        try:
            await event.edit(
                f"🔢 **کد تأیید تلگرام**\n\n"
                f"📱 شماره: `{state['phone']}`\n"
                f"🔢 مقدار فعلی: `{display}`",
                buttons=number_keyboard(uid),
                parse_mode='md'
            )
        except Exception:
            pass
        return

    # --------------------------------
    # تأیید کد
    # --------------------------------
    if action == 'ok':
        if len(state['code']) != state.get('code_length', 5):
            await event.answer(f"❌ کد باید {state.get('code_length', 5)} رقم باشد.", alert=True)
            return

        await event.answer("⏳ در حال بررسی کد...", alert=False)
        await verify_code(event, uid, state)
        return

    # --------------------------------
    # اعداد
    # --------------------------------
    if len(action) == 1 and action in '0123456789':
        if len(state['code']) < state.get('code_length', 5):
            state['code'] += action
            display = state['code']

            await event.answer(f"🔢 {display}", alert=False)

            try:
                await event.edit(
                    f"🔢 **کد تأیید تلگرام**\n\n"
                    f"📱 شماره: `{state['phone']}`\n"
                    f"🔢 مقدار فعلی: `{display}`",
                    buttons=number_keyboard(uid),
                    parse_mode='md'
                )
            except Exception:
                pass
        else:
            await event.answer("⚠️ کد کامل شده است. برای ادامه ✅ را بزنید.", alert=True)


async def verify_code(event, uid, state):
    """تأیید کد ورود"""
    if event.sender_id != uid or not active_state(uid, state, event.chat_id) or state.get('step') != 'WAIT_CODE':
        return
    state['step'] = 'VERIFYING_CODE'
    client = state.get('client')

    if not client:
        cleanup_state(uid)
        await event.answer("⚠️ نشست منقضی شده است.", alert=True)
        return

    try:
        await asyncio.wait_for(client.sign_in(
            phone=state['phone'],
            code=state['code'],
            phone_code_hash=state['hash']
        ), timeout=40)

        await complete_login(
            event.client,
            state['chat_id'],
            uid,
            state,
            edit_event=event
        )

    except errors.SessionPasswordNeededError:
        if not active_state(uid, state, event.chat_id):
            return
        state['step'] = 'WAIT_2FA'

        try:
            await event.edit(
                ui.WAIT_2FA_TEXT,
                parse_mode='md'
            )
        except Exception:
            pass

    except errors.PhoneCodeInvalidError:
        if not active_state(uid, state, event.chat_id):
            return
        state['step'] = 'WAIT_CODE'
        state['code'] = ''
        await event.answer("❌ کد وارد شده اشتباه است.", alert=True)

        try:
            await event.edit(
                f"❌ **کد وارد شده اشتباه است**\n"
                f"📱 شماره: `{state['phone']}`\n\n"
                f"لطفاً دوباره کد را وارد کنید:",
                buttons=number_keyboard(uid),
                parse_mode='md'
            )
        except Exception:
            pass

    except errors.PhoneNumberUnoccupiedError:
        cleanup_state(uid, expected=state)
        await event.answer('این شماره حساب قابل ورود ندارد؛ ثبت‌نام جدید انجام نمی‌شود.', alert=True)
        if login_states.get(uid) is state:
            cleanup_state(uid)

    except errors.PhoneCodeExpiredError:
        cleanup_state(uid, expected=state)
        await event.answer("⏰ کد منقضی شده است.", alert=True)

        try:
            await event.edit(
                "⏰ **کد منقضی شده است**\n"
                "لطفاً دوباره `/start` بزنید.",
                parse_mode='md'
            )
        except Exception:
            pass

        if login_states.get(uid) is state:
            cleanup_state(uid, disconnect=True)

    except Exception:
        cleanup_state(uid, expected=state)
        await event.answer('تأیید ورود ناموفق بود؛ دوباره شروع کنید.', alert=True)
        if login_states.get(uid) is state:
            cleanup_state(uid, disconnect=True)


async def complete_login(bot, chat_id, uid, state, edit_event=None):
    """اتمام موفق فرآیند اتصال"""
    if not active_state(uid, state, chat_id):
        return
    client = state.get('client')

    if not client:
        cleanup_state(uid, disconnect=True)
        return

    try:
        me = await asyncio.wait_for(client.get_me(), timeout=40)
        if not active_state(uid, state, chat_id):
            return
        if not me or getattr(me, 'bot', False) or getattr(me, 'deleted', False):
            raise RuntimeError('حساب تلگرام قابل ورود نیست؛ ثبت‌نام جدید انجام نمی‌شود.')
        purpose = state.get('purpose', 'install')

        # Trial activation is bound to the same Telegram identity that pressed
        # the bot button. This prevents reusing one bot account to activate a
        # different Telegram account's trial.
        if purpose == 'free_trial' and int(me.id) != int(uid):
            raise RuntimeError('برای تست رایگان باید با همان اکانتی وارد شوید که در ربات هستید.')
        if int(me.id) != int(uid):
            raise RuntimeError('فقط اتصال همان حسابی مجاز است که در ربات هستید.')

        if purpose == 'free_trial':
            from services import trial_manager
            if trial_manager.has_used(uid):
                raise RuntimeError('شما قبلاً از تست رایگان استفاده کرده‌اید.')

        import db
        from services.membership_service import check_required_memberships
        joined, _ = await check_required_memberships(bot, uid, db.get_global().get('force_channels') or [])
        if not active_state(uid, state, chat_id):
            return
        if not joined:
            raise RuntimeError('قبل از تکمیل اتصال باید عضو کانال‌های اجباری باشید.')
        state['step'] = 'FINALIZING'

        # ذخیره سشن
        session_string = client.session.save()
        session_file = os.path.join(SESSIONS_DIR, f"user_{me.id}.txt")

        save_session_atomic(session_file, session_string)

        # مقداردهی اولیه دیتابیس
        try:
            import db
            db.update_user_settings(me.id, {
                'base_first_name': me.first_name or '',
                'base_last_name': me.last_name or '',
            })
        except Exception:
            pass

        trial_row = None
        if purpose == 'free_trial':
            from services import trial_manager
            ok, trial_row = trial_manager.activate_after_login(
                uid,
                username=getattr(me, 'username', '') or '',
                first_name=getattr(me, 'first_name', '') or '',
                last_name=getattr(me, 'last_name', '') or '',
                phone=state.get('phone') or '',
            )
            if not ok:
                raise RuntimeError('شما قبلاً از تست رایگان استفاده کرده‌اید.')
            try:
                import db
                db.update_user_settings(uid, {'self_enabled': True, 'bill_seconds': 0.0})
            except Exception:
                pass

        # Persisted connection and active paid/trial runtime are distinct.
        service_started = False
        try:
            from self_manager import start_session, running_sessions
            started = await start_session(f"user_{me.id}")
            task = running_sessions.get(f"user_{me.id}")
            service_started = bool(started or (task is not None and not task.done()))
        except Exception as exc:
            print(f'⚠️ session startup failed: {type(exc).__name__}')
        if service_started:
            try:
                await client.send_message('me', ui.SELF_STARTED_TEXT, parse_mode='md')
            except Exception:
                pass

        # حذف وضعیت لاگین
        cleanup_state(uid, disconnect=False, expected=state)

        # اطلاع به کاربر
        if purpose == 'free_trial':
            final_text = (
                "🎉 **تست رایگان فعال شد!**\n\n"
                f"⏳ اعتبار: **{TRIAL_DURATION_HOURS} ساعت**\n"
                f"🕐 زمان انقضا: `{(trial_row or {}).get('expire_time') or '-'}`\n\n"
                f"👤 حساب: `{me.first_name or ''} {me.last_name or ''}`\n"
                f"🆔 شناسه: `{me.id}`\n"
                f"🏷 یوزرنیم: `@{me.username or 'ندارد'}`"
            )
        else:
            final_text = (
                f"{ui.LOGIN_SUCCESS_TEXT}\n\n"
                f"👤 حساب: `{me.first_name or ''} {me.last_name or ''}`\n"
                f"🆔 شناسه: `{me.id}`\n"
                f"🏷 یوزرنیم: `@{me.username or 'ندارد'}`"
            )

        if not service_started:
            final_text = ('✅ اتصال حساب ذخیره شد؛ سرویس سلف هنوز اجرا نشده است.\n'
                          'اعتبار، وضعیت روشن/خاموش و اجرای سرویس را از پنل بررسی کنید.')

        notified = False

        if edit_event is not None:
            try:
                await edit_event.edit(
                    final_text,
                    buttons=[[ui.inline_button("↩️ بازگشت به پنل", b"main_menu", "primary")]],
                    parse_mode='md'
                )
                notified = True
            except Exception:
                notified = False

        if not notified:
            try:
                await bot.send_message(
                    chat_id,
                    final_text,
                    buttons=[[ui.inline_button("↩️ بازگشت به پنل", b"main_menu", "primary")]],
                    parse_mode='md'
                )
            except Exception:
                pass


    except Exception as e:
        try:
            if edit_event is not None:
                await edit_event.edit(f"❌ خطا: `{e}`", parse_mode='md')
            else:
                await bot.send_message(chat_id, f"❌ خطا: `{e}`", parse_mode='md')
        except Exception:
            pass

        if login_states.get(uid) is state:
            cleanup_state(uid, disconnect=True)

    finally:
        try:
            await client.disconnect()
        except Exception:
            pass