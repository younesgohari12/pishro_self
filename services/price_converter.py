"""Pure cryptocurrency conversion and display helpers."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def convert_amount(amount: float, source_price_usdt: float, target_price_usdt: float) -> float:
    if amount <= 0 or source_price_usdt <= 0 or target_price_usdt <= 0:
        raise ValueError('conversion inputs must be positive')
    return float((Decimal(str(amount)) * Decimal(str(source_price_usdt))) / Decimal(str(target_price_usdt)))


def to_toman(amount: float, price_usdt: float, usdt_toman: float) -> float:
    if amount < 0 or price_usdt < 0 or usdt_toman <= 0:
        raise ValueError('invalid price input')
    return float(Decimal(str(amount)) * Decimal(str(price_usdt)) * Decimal(str(usdt_toman)))


def format_number(value: float | int, *, max_decimals: int = 8) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return '0'
    if not number.is_finite():
        raise ValueError('nonfinite price')
    if number != 0 and abs(number) < Decimal('0.00000001'):
        return f'{number:.6g}'
    if number == 0:
        return '0'
    abs_n = abs(number)
    if abs_n >= 1:
        decimals = min(max_decimals, 2 if abs_n >= 100 else 6)
    else:
        decimals = max_decimals
    quantum = Decimal('1') if decimals == 0 else Decimal('1.' + ('0' * decimals))
    number = number.quantize(quantum, rounding=ROUND_HALF_UP)
    text = f'{number:,.{decimals}f}'
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text


def format_toman(value: float | int | None) -> str:
    if value is None:
        return 'ناموجود'
    if 0 < abs(value) < 1:
        return format_number(value)
    try:
        number = Decimal(str(value)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return '0'
    return f'{int(number):,}'
