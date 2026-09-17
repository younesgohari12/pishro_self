"""Parser for Persian/English `.ارز` cryptocurrency commands."""
from __future__ import annotations

from dataclasses import dataclass
import re

_DIGIT_TRANS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
_SEPARATORS = {'٬': ',', '،': ',', '٫': '.'}


@dataclass(frozen=True)
class CryptoCommand:
    amount: float | None
    source_query: str
    target_query: str | None = None
    raw: str = ''

    @property
    def mode(self) -> str:
        if self.amount is None:
            return 'price'
        return 'convert' if self.target_query else 'amount'


def normalize_digits(text: str) -> str:
    value = (text or '').translate(_DIGIT_TRANS)
    for old, new in _SEPARATORS.items():
        value = value.replace(old, new)
    return value


def normalize_asset_text(text: str) -> str:
    value = normalize_digits(text).strip().lower()
    value = value.replace('_', ' ').replace('-', ' ').replace('\u200c', ' ')
    value = value.lstrip('$#')
    value = value.replace('ي', 'ی').replace('ك', 'ک')
    value = re.sub(r'\s+', ' ', value)
    return value.strip()


def parse_number(token: str) -> float | None:
    token = normalize_digits(token).strip().replace(',', '')
    if not token:
        return None
    if not re.fullmatch(r'(?:\d+(?:\.\d*)?|\.\d+)', token):
        return None
    try:
        value = float(token)
    except (TypeError, ValueError):
        return None
    if value <= 0 or value == float('inf'):
        return None
    return value


def parse_crypto_command(text: str) -> CryptoCommand:
    """Parse `.ارز btc`, `.ارز 100 btc usdt`, `.ارز 100 تون به تتر`.

    Without an explicit ``به`` separator the source/target split is intentionally
    left unresolved. The resolver can try longest valid coin-name splits using
    CoinGecko's complete coin list, which is more reliable for multi-word names.
    """
    raw = (text or '').strip()
    body = re.sub(r'^\s*\.\s*ارز(?:\s+|$)', '', raw, count=1, flags=re.IGNORECASE).strip()
    if not body:
        raise ValueError('empty_crypto_command')

    normalized = normalize_digits(body)
    first, *rest = normalized.split(maxsplit=1)
    amount = parse_number(first)
    if amount is None:
        return CryptoCommand(None, normalize_asset_text(normalized), None, raw)

    if not rest or not rest[0].strip():
        raise ValueError('missing_crypto_asset')
    remainder = rest[0].strip()

    # Persian explicit separator. Surrounding whitespace keeps words such as
    # names/IDs from being accidentally split.
    explicit = re.split(r'\s+به\s+', remainder, maxsplit=1, flags=re.IGNORECASE)
    if len(explicit) == 2:
        source = normalize_asset_text(explicit[0])
        target = normalize_asset_text(explicit[1])
        if not source or not target:
            raise ValueError('missing_crypto_asset')
        return CryptoCommand(amount, source, target, raw)

    return CryptoCommand(amount, normalize_asset_text(remainder), None, raw)
