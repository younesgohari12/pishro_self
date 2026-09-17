"""Resolve numeric IDs or current Telegram usernames without trusting JSON aliases."""
from __future__ import annotations
import asyncio
import re
from telethon import errors
from telethon.tl.types import User

MAX_USER_ID = (1 << 52) - 1
DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')


class IdentityError(ValueError):
    pass


def parse_target(value):
    if type(value) is int:
        if 0 < value <= MAX_USER_ID:
            return value
        raise IdentityError('آیدی عددی معتبر نیست.')
    if not isinstance(value, str):
        raise IdentityError('آیدی عددی یا یوزرنیم معتبر بفرستید.')
    value = value.strip().translate(DIGITS)
    if re.fullmatch(r'[0-9]{1,16}', value):
        return parse_target(int(value))
    username = value[1:] if value.startswith('@') else value
    # Telegram is authoritative for whether the username actually exists.
    if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,31}', username):
        return '@' + username
    raise IdentityError('آیدی عددی یا یوزرنیم مانند @username بفرستید.')


async def resolve_user(bot, value, *, allow_bots=False):
    target = parse_target(value)
    try:
        # Telethon resolves username strings against Telegram; no legacy
        # username-to-ID fallback is permitted after username reassignment.
        entity = await asyncio.wait_for(bot.get_entity(target), timeout=20)
    except errors.FloodWaitError as exc:
        raise IdentityError(f'تلگرام بررسی را محدود کرده؛ {exc.seconds} ثانیه صبر کنید.') from None
    except Exception:
        raise IdentityError('حساب پیدا نشد یا بررسی تلگرام ممکن نیست؛ آیدی یا یوزرنیم را بررسی کنید.') from None
    if (not isinstance(entity, User) or entity.deleted or (entity.bot and not allow_bots)
            or type(entity.id) is not int or not 0 < entity.id <= MAX_USER_ID):
        raise IdentityError('مقصد باید یک حساب تلگرام معتبر و فعال باشد.')
    if type(target) is int and entity.id != target:
        raise IdentityError('شناسه حساب با درخواست مطابقت ندارد.')
    return entity
