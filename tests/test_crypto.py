import asyncio
import time
from pathlib import Path

import pytest

from database import models
from services.crypto_api import CryptoAPI, Coin
from services.crypto_parser import parse_crypto_command
from services.price_converter import convert_amount, format_number, format_toman

ROOT = Path(__file__).parents[1]


def test_parser_price_and_amount_examples():
    c = parse_crypto_command('.ارز بیت کوین')
    assert c.mode == 'price' and c.source_query == 'بیت کوین'

    c = parse_crypto_command('.ارز 10 تتر')
    assert c.mode == 'amount' and c.amount == 10 and c.source_query == 'تتر'

    c = parse_crypto_command('.ارز ۱۰۰ تون به تتر')
    assert c.mode == 'convert' and c.amount == 100
    assert c.source_query == 'تون' and c.target_query == 'تتر'

    c = parse_crypto_command('.ارز 1,000.5 DOGS BTC')
    assert c.amount == pytest.approx(1000.5)
    assert c.source_query == 'dogs btc'


def test_converter_and_number_formatting():
    assert convert_amount(100, 0.001, 50_000) == pytest.approx(0.000002)
    assert format_toman(1234567.6) == '1,234,568'
    assert format_number(1234567.125) == '1,234,567.13'


def test_crypto_database_tables_and_persistent_settings():
    models.init_crypto_db()
    settings = models.get_crypto_settings(1001)
    assert settings['favorite_coins'] == ['BTC', 'ETH', 'TON', 'USDT', 'DOGS']
    models.set_crypto_favorites(1001, ['btc', 'sol', 'BTC'])
    assert models.get_crypto_settings(1001)['favorite_coins'] == ['BTC', 'SOL']

    models.upsert_crypto_cache(symbol='BTC', price_usd=50000, price_usdt=50010, price_toman=5_000_000_000)
    row = models.get_crypto_cache('btc', max_age=30)
    assert row and row['symbol'] == 'BTC' and row['price_usd'] == 50000


def test_coin_list_resolution_supports_full_provider_names(monkeypatch):
    api = CryptoAPI()
    api._coins = [
        Coin('bitcoin', 'btc', 'Bitcoin'),
        Coin('arbitrum', 'arb', 'Arbitrum'),
        Coin('the-open-network', 'gram', 'Gram (prev. Toncoin)'),
        Coin('dogs', 'dogs', 'Dogs'),
    ]
    api._coins_at = time.monotonic()

    assert asyncio.run(api.resolve_coin('Arbitrum')).id == 'arbitrum'
    assert asyncio.run(api.resolve_coin('ARB')).id == 'arbitrum'
    assert asyncio.run(api.resolve_coin('داگز')).id == 'dogs'
    assert asyncio.run(api.resolve_coin('تون')).id == 'the-open-network'
    assert asyncio.run(api.resolve_coin('GRAM')).id == 'the-open-network'


def test_live_quote_math_nobitex_irr_to_toman_and_memory_cache(monkeypatch):
    api = CryptoAPI()
    api._coins = [Coin('bitcoin', 'btc', 'Bitcoin'), Coin('tether', 'usdt', 'Tether')]
    api._coins_at = time.monotonic()
    calls = []

    async def fake_get_json(url, *, params=None, headers=None):
        calls.append((url, dict(params or {})))
        if url.endswith('/market/stats'):
            return {'status': 'ok', 'stats': {'usdt-rls': {'latest': '1000000'}}}
        if url.endswith('/simple/price'):
            now = int(time.time())
            return {
                'bitcoin': {'usd': 50000, 'usd_24h_change': 2.5, 'last_updated_at': now},
                'tether': {'usd': 1.0, 'usd_24h_change': 0.01, 'last_updated_at': now},
            }
        raise AssertionError(url)

    monkeypatch.setattr(api, '_get_json', fake_get_json)
    q1 = asyncio.run(api.quote('btc'))
    assert q1.price_usdt == pytest.approx(50000)
    # 1 USDT = 1,000,000 IRR = 100,000 toman
    assert q1.price_toman == pytest.approx(5_000_000_000)
    first_count = len(calls)
    q2 = asyncio.run(api.quote('bitcoin'))
    assert q2.price_toman == q1.price_toman
    assert len(calls) == first_count, 'second quote inside TTL must hit memory cache'


def test_crypto_is_integrated_into_project_sources():
    self_source = (ROOT / 'self.py').read_text(encoding='utf-8')
    bot_source = (ROOT / 'bot' / 'core.py').read_text(encoding='utf-8')
    inline_source = (ROOT / 'inline.py').read_text(encoding='utf-8')
    main_source = (ROOT / 'main.py').read_text(encoding='utf-8')

    assert 'PATTERN_CRYPTO' in self_source and 'execute_crypto_text' in self_source
    assert '💰 ارز دیجیتال' in bot_source and 'CryptoController' in bot_source
    assert '📊 قیمت ارز' in inline_source and '🔥 ارزهای محبوب' in inline_source
    assert 'init_crypto_db()' in main_source
