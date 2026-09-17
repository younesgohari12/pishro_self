"""Validated, atomic and replay-safe transfers of virtual diamonds."""
from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_EVEN

import db
from config import MIN_TRANSFER, TRANSFER_FEE_PERCENT
from database import models
from services.transaction_service import connect, begin_immediate, ledger_entry
from services import identity_service

MAX_USER_ID = (1 << 52) - 1
MAX_BALANCE = (1 << 63) - 1


class TransferError(ValueError):
    pass


def parse_integer(value, *, maximum=MAX_BALANCE):
    if type(value) is int:
        number = value
    elif isinstance(value, str) and re.fullmatch(r'[0-9]{1,19}', value.strip()):
        number = int(value.strip())
    else:
        raise TransferError('عدد صحیح مثبت و معتبر وارد کنید.')
    if not 0 < number <= maximum:
        raise TransferError('عدد خارج از محدوده مجاز است.')
    return number


def registered_user(user_id):
    uid = parse_integer(user_id, maximum=MAX_USER_ID)
    # Only an actual incoming interaction sets registered_at. Merely having a
    # wallet (including phantom wallets from the old bug) is not registration.
    with db._lock:
        return bool(db._get_internal(uid).get('registered_at'))


async def validate_recipient(bot, sender_id, target):
    try:
        parsed = identity_service.parse_target(target)
        if type(parsed) is int:
            if parsed == sender_id:
                raise TransferError('نمی‌توانی به خودت انتقال بدهی.')
            if not registered_user(parsed):
                raise TransferError('مقصد در ربات ثبت‌نام نکرده است؛ ابتدا باید ربات را استارت کند.')
        entity = await identity_service.resolve_user(bot, parsed)
    except identity_service.IdentityError as exc:
        raise TransferError(str(exc)) from None
    uid = entity.id
    if uid == sender_id:
        raise TransferError('نمی‌توانی به خودت انتقال بدهی.')
    if not registered_user(uid):
        raise TransferError('مقصد در ربات ثبت‌نام نکرده است؛ ابتدا باید ربات را استارت کند.')
    return uid


def quote(amount):
    amount = parse_integer(amount)
    if amount < MIN_TRANSFER:
        raise TransferError(f'حداقل انتقال {MIN_TRANSFER} الماس است.')
    rate = Decimal(str(TRANSFER_FEE_PERCENT))
    if not rate.is_finite() or not 0 <= rate < 100:
        raise TransferError('تنظیم کارمزد معتبر نیست.')
    fee = max(1, int((Decimal(amount) * rate / 100).to_integral_value(rounding=ROUND_HALF_EVEN)))
    if fee >= amount:
        raise TransferError('مقدار پس از کسر کارمزد باید مثبت باشد.')
    return amount, fee, amount - fee


def transfer(sender_id, recipient_id, amount, request_id):
    sender = parse_integer(sender_id, maximum=MAX_USER_ID)
    recipient = parse_integer(recipient_id, maximum=MAX_USER_ID)
    amount, fee, received = quote(amount)
    if sender == recipient:
        raise TransferError('نمی‌توانی به خودت انتقال بدهی.')
    if not isinstance(request_id, str) or not re.fullmatch(r'[a-f0-9]{32}', request_id):
        raise TransferError('شناسه انتقال معتبر نیست.')
    with db._lock, models._lock, connect() as conn:
        begin_immediate(conn)
        previous = conn.execute('SELECT * FROM wallet_transfers WHERE request_id=?', (request_id,)).fetchone()
        if previous:
            if (previous['sender_id'], previous['recipient_id'], previous['amount']) != (sender, recipient, amount):
                raise TransferError('شناسه انتقال قبلاً استفاده شده است.')
            return dict(previous, duplicate=True)
        if not registered_user(sender) or not registered_user(recipient):
            raise TransferError('فرستنده و مقصد باید در ربات ثبت‌نام کرده باشند.')
        rows = {r['telegram_id']: r['diamonds'] for r in conn.execute(
            'SELECT telegram_id,diamonds FROM users WHERE telegram_id IN (?,?)', (sender, recipient))}
        if sender not in rows or recipient not in rows:
            raise TransferError('کیف پول مقصد یا فرستنده موجود نیست؛ دوباره ربات را استارت کنید.')
        if rows[sender] < amount:
            raise TransferError('موجودی کافی نیست.')
        if rows[recipient] > MAX_BALANCE - received:
            raise TransferError('موجودی مقصد از سقف مجاز عبور می‌کند.')
        now = models._now_iso()
        for uid, delta in ((sender, -amount), (recipient, received)):
            after = rows[uid] + delta
            conn.execute('UPDATE users SET diamonds=?,updated_at=? WHERE telegram_id=?', (after, now, uid))
            # Legacy ledger CHECK constraints accept only these type names.
            # The dedicated wallet_transfers row records the actual operation.
            ledger_entry(conn, user_id=uid, tx_type='ADMIN_REMOVE' if delta < 0 else 'ADMIN_ADD',
                         amount=delta, before=rows[uid], after=after, game_id=None,
                         description=f'WALLET_TRANSFER:{request_id}', created_at=now)
        conn.execute('''INSERT INTO wallet_transfers
            (request_id,sender_id,recipient_id,amount,fee,received,sender_after,recipient_after,created_at)
            VALUES (?,?,?,?,?,?,?,?,?)''',
            (request_id, sender, recipient, amount, fee, received, rows[sender]-amount, rows[recipient]+received, now))
        conn.commit()
        return dict(conn.execute('SELECT * FROM wallet_transfers WHERE request_id=?', (request_id,)).fetchone(), duplicate=False)
