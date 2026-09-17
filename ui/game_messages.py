"""Compact Persian game cards and Jalali date formatting."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from config import PRICE_PER_DIAMOND


def fmt(value: int) -> str:
    return f'{int(value):,}'


def display_name(username: str | None, first_name: str | None = None, user_id: int | None = None) -> str:
    if username:
        return '@' + str(username).lstrip('@')
    if first_name:
        return str(first_name).replace('*', '').replace('`', '')
    return f'کاربر {user_id}' if user_id is not None else 'بدون نام'


def jalali_now() -> tuple[str, str]:
    local = datetime.now(timezone.utc).astimezone(ZoneInfo('Asia/Tehran'))
    try:
        import jdatetime
        value = jdatetime.datetime.fromgregorian(datetime=local.replace(tzinfo=None))
        return value.strftime('%Y/%m/%d'), local.strftime('%H:%M:%S')
    except ImportError:
        # Runtime installation includes jdatetime; this keeps diagnostics import-safe.
        return local.strftime('%Y/%m/%d'), local.strftime('%H:%M:%S')


def balance_card(*, username: str | None, first_name: str | None, user_id: int, diamonds: int) -> str:
    date, clock = jalali_now()
    return (
        '💎 **موجودی حساب**\n\n'
        f'👤 کاربر: **{display_name(username, first_name, user_id)}**\n'
        f'💎 الماس: **{fmt(diamonds)}**\n'
        f'💵 ارزش نمایشی: **{fmt(int(diamonds) * int(PRICE_PER_DIAMOND))} تومان**\n'
        f'📅 تاریخ: **{date}**\n'
        f'🕒 ساعت: **{clock}**\n\n'
        '_الماس اعتبار مجازی داخل ربات است و قابلیت برداشت نقدی ندارد._'
    )


def open_game_card(game: dict) -> str:
    bet = int(game['bet_amount'])
    gross = bet * 2
    tax = gross * int(game['tax_percent']) // 100
    reward = gross - tax
    return (
        '🎮 **نبرد الماس**\n\n'
        f'💎 ورودی هر بازیکن: **{fmt(bet)} الماس**\n'
        f'🏆 جایزه خام: **{fmt(gross)} الماس**\n'
        f"💸 کارمزد: **{int(game['tax_percent'])}%**\n"
        f'🏅 جایزه نهایی: **{fmt(reward)} الماس**\n\n'
        f"👤 سازنده: **{display_name(game.get('creator_username'), user_id=game.get('creator_id'))}**\n\n"
        '⏳ **منتظر حریف...**'
    )


def result_card(game: dict) -> str:
    creator = display_name(game.get('creator_username'), user_id=game.get('creator_id'))
    opponent = display_name(game.get('opponent_username'), user_id=game.get('opponent_id'))
    winner_username = game.get('creator_username') if int(game['winner_id']) == int(game['creator_id']) else game.get('opponent_username')
    winner = display_name(winner_username, user_id=game.get('winner_id'))
    return (
        '🎉 **نتیجه نبرد مشخص شد**\n\n'
        f'👤 بازیکن اول: **{creator}**\n'
        '⚔️ **VS**\n'
        f'👤 بازیکن دوم: **{opponent}**\n\n'
        f'🏆 برنده: **{winner}**\n\n'
        f"💎 ورودی هر نفر: **{fmt(game['bet_amount'])}**\n"
        f"💰 مجموع: **{fmt(int(game['bet_amount']) * 2)}**\n"
        f"💸 کارمزد: **{fmt(game['tax_amount'])}**\n"
        f"🏆 جایزه: **{fmt(game['reward_amount'])} الماس**"
    )


def cancelled_card(game: dict) -> str:
    return (
        '❌ **بازی لغو شد**\n\n'
        f"💎 **{fmt(game['bet_amount'])} الماس** به سازنده بازگردانده شد."
    )


def expired_card(game: dict) -> str:
    return (
        '⌛ **بازی منقضی شد**\n\n'
        f"💎 **{fmt(game['bet_amount'])} الماس** به سازنده بازگردانده شد."
    )
