import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
import config
import inline
import bot.core as core
from handlers import translate, crypto
from services import feature_flags


def data(button):
    value = getattr(button, 'data', None)
    if value is None:
        value = getattr(getattr(button, 'type', None), 'data', b'')
    return value.decode() if isinstance(value, bytes) else value


def test_disabled_options_hidden_from_both_real_menus():
    assert config.TRANSLATE_ENABLED is False and config.CRYPTO_ENABLED is False
    for text, rows in (core.render_main_menu(101), inline.build_main_menu(101, 'example_bot')):
        callbacks = [data(b) for row in rows for b in row]
        assert not any(feature_flags.disabled_callback_message(d) for d in callbacks)
        assert any(d in {'ai_menu', 'feat_ai'} for d in callbacks)
        assert all(rows)


def test_direct_commands_do_not_call_apis_or_consume_rate_limit(monkeypatch):
    monkeypatch.setattr(config, 'SELF_TRANSLATE_ENABLED', False)
    chat = AsyncMock()
    quote = AsyncMock()
    monkeypatch.setattr(translate, 'translate_text', chat)
    monkeypatch.setattr(crypto.crypto_api, 'quote', quote)
    async def run():
        assert await translate.execute_translate_text(101, '.ترجمه hello') == feature_flags.TRANSLATE_DISABLED
        assert await crypto.execute_crypto_text('.ارز بیت کوین') == feature_flags.CRYPTO_DISABLED
        assert await crypto.CryptoController(None).popular_text(101) == feature_flags.CRYPTO_DISABLED
    asyncio.run(run())
    chat.assert_not_awaited()
    quote.assert_not_awaited()


@pytest.mark.parametrize('module,cls,prefix', [
    (crypto, crypto.CryptoController, 'crypto'),
])
def test_stale_buttons_and_old_input_states_are_inert(module, cls, prefix):
    controller = cls(None)
    event = NS(sender_id=101, text='hello', reply=AsyncMock(), answer=AsyncMock(), edit=AsyncMock())
    async def run():
        controller.states[101] = {'step': 'language' if prefix == 'translate' else 'favorites'}
        assert await controller.handle_message(event)
        assert not controller.has_state(101)
        assert await controller.handle_callback(event, prefix + '_menu')
        assert not controller.has_state(101)
    asyncio.run(run())
    event.edit.assert_not_called()
    event.answer.assert_awaited_once()


def test_switches_allow_reenable_without_deleting_features(monkeypatch):
    monkeypatch.setattr(config, 'SELF_TRANSLATE_ENABLED', True)
    monkeypatch.setattr(config, 'CRYPTO_ENABLED', True)
    _, rows = inline.build_main_menu(101, 'example_bot')
    callbacks = {data(b) for row in rows for b in row}
    assert {'feat_translate', 'icrypto_menu', 'feat_ai'} <= callbacks


def test_main_bot_old_buttons_show_disabled_message(monkeypatch):
    from test_security_runtime import run, Event
    async def scenario(bot):
        for callback in (b'translate_start', b'crypto_popular'):
            event = Event(bot, data=callback)
            await bot.handlers['cb_handler'](event)
            assert ('غیرفعال' if b'crypto' in callback else 'سلف') in event.answer.call_args.args[0]
            event.edit.assert_not_called()
    run(monkeypatch, scenario)


def test_self_direct_commands_remain_disabled_with_funded_account(monkeypatch):
    monkeypatch.setattr(config, 'SELF_CRYPTO_ENABLED', False)
    monkeypatch.setattr(config, 'SELF_TRANSLATE_ENABLED', False)
    import self as runtime
    from test_session_restore import Client, saved_string
    from test_security_runtime import fund
    fund(101, 50)
    execute_crypto = AsyncMock()
    execute_translate = AsyncMock()
    monkeypatch.setattr(runtime, 'execute_crypto_text', execute_crypto)
    monkeypatch.setattr(runtime, 'execute_translate_text', execute_translate)
    async def scenario(client):
        for name, raw in [('crypto_cmd', '.ارز بیت کوین'), ('translate_cmd', '.ترجمه hello')]:
            event = NS(chat_id=101, raw_text=raw, delete=AsyncMock())
            await client.handlers[name](event)
            assert 'غیرفعال' in client.send_message.call_args.args[1]
            event.delete.assert_not_called()
    client = Client(scenario=scenario)
    monkeypatch.setattr(runtime, 'TelegramClient', lambda *a, **k: client)
    asyncio.run(runtime.run_self('/test/user_101', saved_string()))
    execute_crypto.assert_not_awaited()
    execute_translate.assert_not_awaited()


def test_inline_old_buttons_cannot_query_crypto(monkeypatch):
    monkeypatch.setattr(config, 'SELF_CRYPTO_ENABLED', False)
    monkeypatch.setattr(config, 'SELF_TRANSLATE_ENABLED', False)
    from test_security_runtime import Bot, Event
    query = AsyncMock()
    monkeypatch.setattr(inline, 'build_inline_crypto_popular', query)
    monkeypatch.setattr(inline, '_resolve_main_bot_username', AsyncMock(return_value='example_bot'))
    monkeypatch.setattr(inline, 'has_self_session', lambda uid: True)
    async def scenario(bot):
        for callback in (b'icrypto_popular', b'feat_translate'):
            event = Event(bot, data=callback)
            await bot.handlers['cb_handler'](event)
            assert 'غیرفعال' in event.answer.call_args.args[0]
            event.edit.assert_not_called()
    bot = Bot(scenario)
    monkeypatch.setattr(inline, 'TelegramClient', lambda *a, **k: bot)
    asyncio.run(inline.run_inline())
    query.assert_not_awaited()
