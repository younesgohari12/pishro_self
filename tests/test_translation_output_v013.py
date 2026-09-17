import asyncio
import hashlib
from unittest.mock import AsyncMock
import pytest
import config
from services import translator
from database import models
from handlers import translate
from services.ai_assistant import request_limiter


@pytest.mark.parametrize('raw', ['{"text": "Hello"}', '```json\n{"text": "Hello"}\n```',
                                '{"translated_text":"Hello"}', '"Hello"', 'Hello'])
def test_wrapper_is_not_shown_to_user(raw):
    assert translator.clean_translation(raw, 'سلام') == 'Hello'


def test_intentionally_translated_json_and_code_are_preserved():
    raw = '{"text":"Hello"}'
    assert translator.clean_translation(raw, '{"text":"سلام"}') == raw
    raw = '```python\nprint("Hello")\n```'
    assert translator.clean_translation(raw, '```python\nprint("سلام")\n```') == raw
    assert translator.clean_translation('Hello\nworld!', 'سلام\nدنیا!') == 'Hello\nworld!'


def test_bad_old_cache_is_bypassed_and_real_handler_sends_plain_translation(monkeypatch):
    material = f'v2:avalai:{config.SELF_TRANSLATE_MODEL}:Persian:English:سلام'
    old_key = 'tr:' + hashlib.sha256(material.encode()).hexdigest()
    models.set_ai_cache(old_key, '{"text":"old bad result"}', ttl_seconds=86400)
    mock = AsyncMock(return_value='{"text": "Hello"}')
    monkeypatch.setattr(translator, 'chat_completion', mock)
    request_limiter.reset(101)
    result = asyncio.run(translate.execute_translate_text(101, '.ترجمه فارسی به انگلیسی سلام'))
    assert result == 'Hello'
    assert mock.call_args.args[0][-1] == {'role': 'user', 'content': 'سلام'}
