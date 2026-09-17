"""Dependency-free parsing for Persian game commands."""
from __future__ import annotations

import re


_GAME_RE = re.compile(r'^\s*(?:بازی|شرط\s*بندی|شرطبندی)\s+([0-9۰-۹٠-٩,٬]+)\s*$')
_DIGIT_MAP = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')


def parse_bet(text: str) -> int | None:
    match = _GAME_RE.fullmatch(text or '')
    if not match:
        return None
    value = match.group(1).translate(_DIGIT_MAP).replace(',', '').replace('٬', '')
    try:
        result = int(value)
    except ValueError:
        return None
    return result if result > 0 else None
