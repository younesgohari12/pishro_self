import asyncio
import time
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
import config
from services import crypto_api as service
from services.crypto_api import CryptoAPI, CryptoAPIUnavailable, CryptoAmbiguous, CryptoNotFound, Coin
from handlers import crypto
from services.price_converter import format_number, format_toman
from test_security_runtime import fund


def api_fixture(monkeypatch, *, local_error=False, price=123.45, stamp=None):
    api = CryptoAPI()
    api._coins = [Coin('bitcoin', 'btc', 'Bitcoin'), Coin('tether', 'usdt', 'Tether'),
                  Coin('arbitrum', 'arb', 'Arbitrum'), Coin('long-tail-asset', 'xyz', 'Long Tail Asset')]
    api._coins_at = time.monotonic()
    async def fetch(url, *, params=None, headers=None):
        if url.endswith('/market/stats'):
            if local_error: raise CryptoAPIUnavailable('local_rate_down')
            return {'stats': {'usdt-rls': {'latest': '1000000'}}}
        if url.endswith('/v1/markets'):
            raise CryptoAPIUnavailable('fallback_rate_down')
        assert url.endswith('/simple/price')
        assert headers == api._cg_headers()
        ts = int(time.time()) if stamp is None else stamp
        return {'bitcoin': {'usd': price, 'last_updated_at': ts, 'usd_24h_change': 1.25},
                'tether': {'usd': 1.001, 'last_updated_at': ts}}
    monkeypatch.setattr(api, '_get_json', AsyncMock(side_effect=fetch))
    return api


@pytest.mark.parametrize('query', ['BTC', 'Bitcoin', 'بیت کوین', 'بیتکوین', 'بیت‌کوین', '$BTC', '#btc'])
def test_price_aliases(query, monkeypatch):
    api = api_fixture(monkeypatch)
    assert asyncio.run(api.resolve_coin(query)).id == 'bitcoin'


@pytest.mark.parametrize('query', ['long-tail-asset', 'Long Tail Asset', 'XYZ'])
def test_full_dynamic_catalogue_is_not_limited_to_popular_coins(query, monkeypatch):
    api = api_fixture(monkeypatch)
    assert asyncio.run(api.resolve_coin(query)).id == 'long-tail-asset'


def test_persian_unfamiliar_name_is_validated_against_actual_catalogue(monkeypatch):
    from services import avalai_ai
    api = api_fixture(monkeypatch)
    ai = AsyncMock(return_value='Long Tail Asset')
    monkeypatch.setattr(avalai_ai, 'chat_completion', ai)
    async def run():
        assert (await api.resolve_coin('اسم فارسی آزمایشی')).id == 'long-tail-asset'
        assert (await api.resolve_coin('اسم فارسی آزمایشی')).id == 'long-tail-asset'
    asyncio.run(run())
    ai.assert_awaited_once()
    ai.return_value = 'Imaginary made up asset'
    with pytest.raises(CryptoNotFound):
        asyncio.run(api.resolve_coin('نام ناشناخته دیگر'))


def test_duplicate_symbol_requires_exact_coin_id(monkeypatch):
    api = api_fixture(monkeypatch)
    api._coins += [Coin('another-xyz', 'xyz', 'Different coin')]
    with pytest.raises(CryptoAmbiguous) as exc:
        asyncio.run(api.resolve_coin('xyz'))
    assert len(exc.value.candidates) == 2
    assert asyncio.run(api.resolve_coin('another-xyz')).name == 'Different coin'


def test_toman_math_uses_tether_usd_price_and_irr_divided_by_ten(monkeypatch):
    api = api_fixture(monkeypatch)
    q = asyncio.run(api.quote('btc'))
    assert q.price_usd == 123.45
    assert q.price_toman == pytest.approx(123.45 / 1.001 * 100000)
    rendered = crypto.render_price(q)
    assert '$123.45' in rendered and 'تومان' in rendered
    assert 'CoinGecko' in rendered and 'نوبیتکس' in rendered


def test_toman_outage_keeps_real_usd_and_never_invents_toman(monkeypatch):
    api = api_fixture(monkeypatch, local_error=True)
    q = asyncio.run(api.quote('btc'))
    assert q.price_usd == 123.45 and q.price_toman is None
    rendered = crypto.render_price(q)
    assert '$123.45' in rendered and 'تومان فعلاً در دسترس نیست' in rendered
    assert '0 تومان' not in rendered


@pytest.mark.parametrize('stamp', [0, int(time.time()) - 301, int(time.time()) + 600])
def test_old_missing_or_future_prices_are_not_called_live(monkeypatch, stamp):
    api = api_fixture(monkeypatch, stamp=stamp)
    with pytest.raises(CryptoAPIUnavailable):
        asyncio.run(api.quote('btc'))


@pytest.mark.parametrize('price', [float('nan'), float('inf'), -10, 0])
def test_invalid_prices_are_rejected(monkeypatch, price):
    api = api_fixture(monkeypatch, price=price)
    with pytest.raises(CryptoAPIUnavailable):
        asyncio.run(api.quote('btc'))


def test_parallel_identical_requests_share_one_fetch(monkeypatch):
    api = api_fixture(monkeypatch)
    async def run():
        return await asyncio.gather(*[api.quote('btc') for _ in range(10)])
    results = asyncio.run(run())
    assert all(q == results[0] for q in results)
    assert api._get_json.await_count == 2


def test_fresh_cache_expiration_fetches_again(monkeypatch):
    api = api_fixture(monkeypatch)
    asyncio.run(api.quote('btc'))
    from dataclasses import replace
    api._price_cache['bitcoin'] = replace(api._price_cache['bitcoin'], fetched_at=time.monotonic()-1000)
    asyncio.run(api.quote('btc'))
    assert api._get_json.await_count == 3  # independent fresh FX cache remains valid


def test_tiny_positive_prices_never_display_zero():
    assert format_number(0.000000000123) != '0'
    assert format_toman(0.000000000123) != '0'
    assert format_toman(None) == 'ناموجود'


def test_headers_use_demo_or_pro_without_query_string_key(monkeypatch):
    monkeypatch.setattr(service, 'COINGECKO_API_KEY', 'synthetic')
    api = CryptoAPI()
    monkeypatch.setattr(service, 'COINGECKO_API_KEY_TYPE', 'demo')
    assert api._cg_headers() == {'x-cg-demo-api-key': 'synthetic'}
    monkeypatch.setattr(service, 'COINGECKO_API_KEY_TYPE', 'pro')
    assert api._cg_headers() == {'x-cg-pro-api-key': 'synthetic'}
    assert 'synthetic' not in api._cg_url('/simple/price')


def test_429_respects_provider_cooldown_without_retry_storm(monkeypatch):
    api = CryptoAPI()
    calls = []
    class Response:
        status = 429
        headers = {'Retry-After': '120'}
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
    class Session:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def get(self, url, **kwargs):
            calls.append(kwargs)
            return Response()
    monkeypatch.setattr(service.aiohttp, 'ClientSession', Session)
    async def run():
        for _ in range(2):
            with pytest.raises(CryptoAPIUnavailable, match='rate_limited'):
                await api._get_json('https://api.coingecko.com/api/v3/simple/price')
    asyncio.run(run())
    assert len(calls) == 1
    assert calls[0]['allow_redirects'] is False


def test_real_self_crypto_handler_is_enabled_and_main_bot_stays_disabled(monkeypatch):
    import self as runtime
    import inline
    from test_session_restore import Client, saved_string
    fund(101, 10)
    api = api_fixture(monkeypatch)
    monkeypatch.setattr(crypto, 'crypto_api', api)
    crypto.crypto_limiter.reset(101)
    completed = []
    async def scenario(client):
        client.send_message.reset_mock()
        event = NS(chat_id=202, raw_text='.ارز BTC', delete=AsyncMock())
        await client.handlers['crypto_cmd'](event)
        assert '$123.45' in client.send_message.call_args.args[1]
        assert 'تومان' in client.send_message.call_args.args[1]
        assert client.send_message.call_args.kwargs['parse_mode'] is None
        assert await crypto.execute_crypto_text('.ارز BTC') == crypto.CRYPTO_DISABLED
        rows = inline.build_main_menu(101, 'test_bot')[1]
        assert b'icrypto_menu' in [getattr(b, 'data', None) for row in rows for b in row]
        completed.append(True)
    client = Client(scenario=scenario)
    monkeypatch.setattr(runtime, 'TelegramClient', lambda *a, **k: client)
    asyncio.run(runtime.run_self('/test/user_101', saved_string()))
    assert completed == [True]


def test_wallex_fallback_is_toman_not_rial_and_does_not_receive_cg_key(monkeypatch):
    api = CryptoAPI()
    calls = []
    async def fetch(url, *, params=None, headers=None):
        calls.append((url, headers))
        if url.endswith('/market/stats'): raise CryptoAPIUnavailable('down')
        assert url == 'https://api.wallex.ir/v1/markets'
        assert headers is None
        return {'result': {'symbols': {'USDTTMN': {'baseAsset': 'USDT', 'quoteAsset': 'TMN',
                    'stats': {'lastPrice': '123456.5'}}}}}
    monkeypatch.setattr(api, '_get_json', fetch)
    assert asyncio.run(api.usdt_toman()) == 123456.5
    assert api._toman_source == 'والکس'
    assert len(calls) == 2
