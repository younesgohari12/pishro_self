"""Reply-based group transfers, backed by the same atomic wallet service."""
from __future__ import annotations
import hashlib
import re

from telethon.tl.types import User
import db
from config import MIN_TRANSFER, TRANSFER_FEE_PERCENT
from services import admin_manager, transfer_service
from services.identity_service import DIGITS
from services.membership_service import check_required_memberships


class TransferController:
    def __init__(self, bot):
        self.bot = bot

    async def handle_message(self, event):
        if not getattr(event, 'is_group', False) or getattr(event, 'is_private', False):
            return False
        text = (event.raw_text or '').strip()
        if not re.match(r'^انتقال(?:\s|$)', text):
            return False
        match = re.fullmatch(r'انتقال\s+(\S+)', text)
        if not match:
            await event.reply(
                f'روی پیام مقصد ریپلای کنید و بنویسید: انتقال 100\n'
                f'حداقل: {MIN_TRANSFER} الماس؛ کارمزد: {TRANSFER_FEE_PERCENT}٪')
            return True
        try:
            if getattr(event, 'fwd_from', None) or getattr(getattr(event, 'message', None), 'fwd_from', None):
                raise transfer_service.TransferError('دستور انتقال باید توسط خودتان نوشته شود؛ پیام فورواردشده پذیرفته نیست.')
            amount, _, _ = transfer_service.quote(match.group(1).translate(DIGITS))
            sender = await event.get_sender()
            if (not isinstance(sender, User) or sender.bot or sender.deleted
                    or sender.id != event.sender_id):
                raise transfer_service.TransferError('انتقال فقط با حساب شخصی مجاز است؛ ارسال ناشناس یا از طرف کانال پذیرفته نیست.')
            uid = sender.id
            if not transfer_service.registered_user(uid):
                raise transfer_service.TransferError('ابتدا ربات را در گفتگوی خصوصی استارت کنید.')
            if not getattr(event, 'is_reply', False):
                raise transfer_service.TransferError('برای انتقال، روی پیام کاربر مقصد ریپلای کنید.')
            reply = await event.get_reply_message()
            if reply is None or getattr(reply, 'chat_id', None) != event.chat_id:
                raise transfer_service.TransferError('پیام مقصد در همین گروه قابل دسترسی نیست.')
            # Deliberately use the author of the replied-to message, never its
            # forwarded origin, text mention, username or signature.
            recipient = await reply.get_sender()
            if (not isinstance(recipient, User) or recipient.bot or recipient.deleted
                    or recipient.id != getattr(reply, 'sender_id', None)):
                raise transfer_service.TransferError('نویسنده پیام مقصد باید یک کاربر معتبر باشد؛ ربات و کانال پذیرفته نیستند.')
            target = await transfer_service.validate_recipient(self.bot, uid, recipient.id)
            if not admin_manager.is_owner(uid):
                joined, _ = await check_required_memberships(self.bot, uid, db.get_global().get('force_channels') or [])
                if not joined:
                    raise transfer_service.TransferError('ابتدا عضویت کانال‌های اجباری را در ربات تکمیل و تأیید کنید.')
            message_id = getattr(event, 'id', None)
            if type(message_id) is not int or message_id <= 0 or type(event.chat_id) is not int:
                raise transfer_service.TransferError('شناسه پیام انتقال معتبر نیست.')
            # Amount/target are NOT part of the key: editing or redelivering the
            # same command must never create a second debit.
            request_id = hashlib.sha256(f'group-transfer:v1:{event.chat_id}:{message_id}:{uid}'.encode()).hexdigest()[:32]
            receipt = transfer_service.transfer(uid, target, amount, request_id)
        except transfer_service.TransferError as exc:
            await event.reply(f'❌ {exc}')
            return True
        except Exception:
            await event.reply('❌ بررسی یا ثبت انتقال ناموفق بود؛ همین دستور را پس از بررسی وضعیت دوباره امتحان کنید.')
            return True
        if not receipt['duplicate']:
            try:
                db.add_stats({'fees_collected': receipt['fee']})
                db.update_user_settings(target, {'last_alert_hours': 9999})
            except Exception:
                pass
        heading = 'ℹ️ این پیام قبلاً پردازش شده؛ برداشت دوباره انجام نشد.' if receipt['duplicate'] else '✅ انتقال الماس انجام شد.'
        # Public receipt contains the transfer only, not either wallet's balance.
        try:
            await event.reply(
                f'{heading}\n👤 فرستنده: {uid}\n👤 مقصد: {target}\n'
                f"💎 کسرشده: {receipt['amount']:,} الماس\n"
                f"✅ رسیده به مقصد: {receipt['received']:,} الماس\n"
                f"🧾 کارمزد: {receipt['fee']:,} الماس", parse_mode=None)
        except Exception:
            # A delivery failure cannot reverse or replay a committed transfer.
            pass
        return True
