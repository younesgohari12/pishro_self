import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import aiohttp
import pytest
import config
from services import translator, avalai_ai
from services.ai_assistant import request_limiter
from handlers import translate
from test_security_runtime import fund, run, Event


@pytest.mark.parametrize('command,reply,source,target,body', [
    ('.ترجمه فارسی', 'Hello world', 'auto', 'Persian', 'Hello world'),
    ('.ترجمه انگیلیسی', 'سلام', 'auto', 'English', 'سلام'),
    ('.ترجمه ترکی استانبولی', 'سلام', 'auto', 'Turkish', 'سلام'),
    ('.ترجمه فارسی به انگیلیسی سلام به همه\nخوش آمدید', None, 'Persian', 'English', 'سلام به همه\nخوش آمدید'),
    ('.ترجمه فارسی به انگلیسی\nسلام دنیا', None, 'Persian', 'English', 'سلام دنیا'),
    ('.ترجمه English to Traditional Chinese Hello', None, 'English', 'Traditional Chinese', 'Hello'),
    ('.ترجمه سواحیلی به فارسی habari', None, 'سواحیلی', 'Persian', 'habari'),
    ('.ترجمه sw به af habari', None, 'sw', 'af', 'habari'),
    ('.ترجمه فارسی به انگلیسی', 'متن ریپلای', 'Persian', 'English', 'متن ریپلای'),
    ('.ترجمه فارسی به انگلیسی متن مستقیم', 'متن ریپلای', 'Persian', 'English', 'متن مستقیم'),
    ('.ترجمه', 'Hello', 'auto', 'Persian', 'Hello'),
    ('.ترجمه فارسی', None, 'auto', 'Persian', ''),
    ('.ترجمه فارسی', '', 'auto', 'Persian', ''),
])
def test_requested_grammar(command, reply, source, target, body):
    assert translator.parse_translate_command(command, reply_text=reply) == {'source': source, 'target': target, 'text': body}


def test_real_self_reply_command_fetches_text_and_replies_to_original(monkeypatch):
    import self as runtime
    from test_session_restore import Client, saved_string
    fund(101, 20)
    request_limiter.reset(101)
    api = AsyncMock(return_value='سلام دنیا')
    monkeypatch.setattr(translate, 'translate_text', api)
    reached = []
    async def scenario(client):
        client.send_message.reset_mock()  # disregard the existing startup notice
        event = NS(chat_id=202, raw_text='.ترجمه فارسی', is_reply=True,
                   get_reply_message=AsyncMock(return_value=NS(id=77, raw_text='Hello world')),
                   delete=AsyncMock())
        await client.handlers['translate_cmd'](event)
        event.get_reply_message.assert_awaited_once()
        api.assert_awaited_once_with('Hello world', source_language='auto', target_language='Persian')
        event.delete.assert_awaited_once()
        client.send_message.assert_awaited_once_with(202, 'سلام دنیا', parse_mode=None, reply_to=77)
        reached.append(True)
    client = Client(scenario=scenario)
    monkeypatch.setattr(runtime, 'TelegramClient', lambda *a, **k: client)
    asyncio.run(runtime.run_self('/test/user_101', saved_string()))
    assert reached == [True]


def test_main_bot_has_no_translation_entry_or_api_route(monkeypatch):
    import bot.core as core
    api = AsyncMock()
    monkeypatch.setattr(translate, 'translate_text', api)
    async def scenario(bot):
        event = Event(bot, text='.ترجمه فارسی به انگلیسی سلام')
        await bot.handlers['msg_handler'](event)
        assert 'سلف' in event.reply.call_args.args[0]
        event = Event(bot, data=b'translate_start')
        await bot.handlers['cb_handler'](event)
        assert 'سلف' in event.answer.call_args.args[0]
        rows = core.render_main_menu(101)[1]
        assert not any((getattr(b, 'data', b'') or b'').startswith(b'translate_') for row in rows for b in row)
    run(monkeypatch, scenario)
    api.assert_not_awaited()


@pytest.mark.asyncio
async def test_large_text_is_complete_chunked_and_cached_only_after_success(monkeypatch):
    parts = []
    async def fake(messages, **kwargs):
        assert kwargs['model'] == config.SELF_TRANSLATE_MODEL
        assert kwargs['require_complete'] and kwargs['max_tokens'] == 4096
        text = messages[-1]['content']
        parts.append(text)
        return text
    monkeypatch.setattr(translator, 'chat_completion', fake)
    source = ('A whole sentence.\n' * 500).strip()
    result = await translator.translate_text(source, target_language='English')
    assert ''.join(parts) == source
    assert len(parts) > 1
    count = len(parts)
    assert await translator.translate_text(source, target_language='English') == result
    assert len(parts) == count


@pytest.mark.asyncio
async def test_truncated_output_is_split_and_retried_not_published(monkeypatch):
    calls = []
    async def fake(messages, **kwargs):
        text = messages[-1]['content']
        calls.append(text)
        if len(text) > 200:
            raise avalai_ai.AvalAITruncatedError('too long')
        return text
    monkeypatch.setattr(translator, 'chat_completion', fake)
    text = 'abcdef' * 80
    result = await translator.translate_text(text, target_language='English')
    assert result.replace('\n', '') == text
    assert len(calls) > 1


@pytest.mark.asyncio
async def test_failure_after_first_chunk_never_creates_partial_cache(monkeypatch):
    calls = []
    async def fake(messages, **kwargs):
        calls.append(messages)
        if len(calls) == 2:
            raise avalai_ai.AvalAIChatError('test outage')
        return 'complete part'
    monkeypatch.setattr(translator, 'chat_completion', fake)
    with pytest.raises(avalai_ai.AvalAIChatError):
        await translator.translate_text('a' * 2400)
    from database import models
    with models._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_cache WHERE cache_key LIKE 'tr:%'").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_too_long_input_rejected_before_network(monkeypatch):
    api = AsyncMock()
    monkeypatch.setattr(translator, 'chat_completion', api)
    with pytest.raises(ValueError, match='حداکثر'):
        await translator.translate_text('a' * (config.SELF_TRANSLATE_MAX_CHARS + 1))
    api.assert_not_awaited()


@pytest.mark.asyncio
async def test_official_google_contract_and_auto_source(monkeypatch):
    monkeypatch.setattr(config, 'SELF_TRANSLATE_PROVIDER', 'google')
    monkeypatch.setattr(config, 'GOOGLE_TRANSLATE_API_KEY', 'synthetic-test-key')
    sent = []
    class Response:
        status = 200
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def json(self): return {'data': {'translations': [{'translatedText': 'سلام &amp; دنیا'}]}}
    class Session:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def post(self, url, **kwargs):
            sent.append((url, kwargs))
            return Response()
    monkeypatch.setattr(translator.aiohttp, 'ClientSession', Session)
    assert await translator.translate_text('Hello & world', target_language='Persian') == 'سلام & دنیا'
    url, kwargs = sent[0]
    assert url == 'https://translation.googleapis.com/language/translate/v2'
    assert kwargs['json'] == {'q': 'Hello & world', 'target': 'fa', 'format': 'text', 'model': 'nmt'}
    assert kwargs['params'] == {'key': 'synthetic-test-key'}


@pytest.mark.asyncio
async def test_provider_errors_cannot_expose_raw_api_secrets_to_chat(monkeypatch):
    request_limiter.reset(101)
    monkeypatch.setattr(translate, 'translate_text', AsyncMock(side_effect=avalai_ai.AvalAIChatError('SECRET_VALUE')))
    response = await translate.execute_translate_text(101, '.ترجمه فارسی به انگلیسی سلام')
    assert 'SECRET_VALUE' not in response and '⚠️' in response
