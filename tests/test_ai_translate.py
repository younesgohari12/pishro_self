import sqlite3

import pytest

from database import models
from services import ai_assistant
from services import translator
from services.translator import parse_translate_command
from services.web_search import needs_web_search


def test_ai_history_schema_and_memory_isolation():
    models.init_ai_db()
    conn = sqlite3.connect(models.TABCHI_DB_PATH)
    cols = [row[1] for row in conn.execute('PRAGMA table_info(ai_history)').fetchall()]
    conn.close()
    assert cols == ['id', 'user_id', 'role', 'message', 'created_at']

    models.add_ai_history(100, 'user', 'hello')
    models.add_ai_history(100, 'assistant', 'hi')
    models.add_ai_history(200, 'user', 'secret')
    assert [r['message'] for r in models.get_ai_history(100)] == ['hello', 'hi']
    assert [r['message'] for r in models.get_ai_history(200)] == ['secret']


def test_ai_history_keeps_last_20_messages():
    for i in range(30):
        models.add_ai_history(123, 'user' if i % 2 == 0 else 'assistant', f'm{i}')
    models.prune_ai_history(123, keep=20)
    rows = models.get_ai_history(123, limit=20)
    assert len(rows) == 20
    assert rows[0]['message'] == 'm10'
    assert rows[-1]['message'] == 'm29'


def test_translate_parser_explicit_and_auto():
    p = parse_translate_command('.ترجمه فارسی به انگلیسی سلام دنیا', default_language='Persian')
    assert p == {'source': 'Persian', 'target': 'English', 'text': 'سلام دنیا'}

    p = parse_translate_command('.ترجمه English to Persian hello world', default_language='German')
    assert p == {'source': 'English', 'target': 'Persian', 'text': 'hello world'}

    p = parse_translate_command('.ترجمه hello', default_language='German')
    assert p == {'source': 'auto', 'target': 'German', 'text': 'hello'}


def test_web_search_detection_is_selective():
    assert needs_web_search('قیمت بیت کوین الان چنده؟')
    assert needs_web_search('latest version of Python?')
    assert needs_web_search('خبر امروز چیست؟')
    assert not needs_web_search('یک تابع پایتون برای جمع دو عدد بنویس')
    assert not needs_web_search('فرق لیست و تاپل چیست؟')


@pytest.mark.asyncio
async def test_ai_uses_memory_and_persists_exchange(monkeypatch):
    models.init_ai_db()
    models.add_ai_history(77, 'user', 'اسم من یونس است')
    models.add_ai_history(77, 'assistant', 'خوشبختم یونس')

    captured = {}

    async def fake_chat(messages, **kwargs):
        captured['messages'] = messages
        return 'شما گفتید اسم‌تان یونس است.'

    monkeypatch.setattr(ai_assistant, 'chat_completion', fake_chat)
    monkeypatch.setattr(ai_assistant, 'needs_web_search', lambda _q: False)
    ai_assistant.request_limiter.reset(77)

    result = await ai_assistant.ask_ai(77, 'اسم من چی بود؟')
    assert 'یونس' in result.text
    payload = captured['messages']
    assert any(m['role'] == 'user' and 'اسم من یونس' in m['content'] for m in payload)

    rows = models.get_ai_history(77, 20)
    assert rows[-2]['message'] == 'اسم من چی بود؟'
    assert 'یونس' in rows[-1]['message']


@pytest.mark.asyncio
async def test_ai_searches_only_when_needed(monkeypatch):
    models.init_ai_db()
    calls = {'search': 0, 'chat': 0}

    async def fake_search(q):
        calls['search'] += 1
        return [{'title': 'Result', 'snippet': 'fresh fact', 'url': 'https://example.com'}]

    async def fake_chat(messages, **kwargs):
        calls['chat'] += 1
        assert any('untrusted reference data' in m['content'] for m in messages if m['role'] == 'system')
        return 'پاسخ تازه\n\nمنابع:\nhttps://example.com'

    monkeypatch.setattr(ai_assistant, 'search_web', fake_search)
    monkeypatch.setattr(ai_assistant, 'chat_completion', fake_chat)
    ai_assistant.request_limiter.reset(88)

    result = await ai_assistant.ask_ai(88, 'خبر امروز درباره Python چیست؟')
    assert result.searched_web is True
    assert calls == {'search': 1, 'chat': 1}


def test_ai_system_prompt_and_default_language_persist():
    assert models.get_ai_system_prompt('default prompt') == 'default prompt'
    models.set_ai_system_prompt('تو یک دستیار تست حرفه‌ای هستی.')
    assert 'تست حرفه‌ای' in models.get_ai_system_prompt('fallback')

    assert models.get_ai_user_settings(5)['default_language'] == 'Persian'
    models.set_ai_default_language(5, 'German')
    assert models.get_ai_user_settings(5)['default_language'] == 'German'


@pytest.mark.asyncio
async def test_translation_uses_avalai_and_cache(monkeypatch):
    calls = {'n': 0}

    async def fake_chat(messages, **kwargs):
        calls['n'] += 1
        assert 'Translate into English' in messages[0]['content']
        return 'Hello'

    monkeypatch.setattr(translator, 'chat_completion', fake_chat)
    first = await translator.translate_text('سلام', source_language='Persian', target_language='English')
    second = await translator.translate_text('سلام', source_language='Persian', target_language='English')
    assert first == 'Hello'
    assert second == 'Hello'
    assert calls['n'] == 1


def test_source_integration_has_non_colliding_callback_namespaces():
    root = __import__('pathlib').Path(__file__).resolve().parents[1]
    core = (root / 'bot' / 'core.py').read_text(encoding='utf-8')
    self_source = (root / 'self.py').read_text(encoding='utf-8')
    inline_source = (root / 'inline.py').read_text(encoding='utf-8')

    assert "data.startswith('translate_')" in core
    assert 'b"translate_menu"' not in core
    assert 'TranslateController' not in core
    assert 'tr_confirm_' in core  # legacy transfer namespace remains untouched
    assert 'PATTERN_TRANSLATE' in self_source
    assert 'PATTERN_AI' in self_source
    assert 'feat_translate' in inline_source
    assert 'feat_ai' in inline_source


def test_translate_parser_handles_flexible_separator_and_missing_body():
    p = parse_translate_command('.ترجمه فارسی   به   انگلیسی سلام', default_language='Persian')
    assert p == {'source': 'Persian', 'target': 'English', 'text': 'سلام'}

    p = parse_translate_command('.ترجمه فارسی به انگلیسی', default_language='German')
    assert p == {'source': 'Persian', 'target': 'English', 'text': ''}


@pytest.mark.asyncio
async def test_immediate_repeat_is_persisted_without_new_api_call(monkeypatch):
    models.init_ai_db()
    models.add_ai_exchange(501, 'سلام', 'سلام! چطور می‌توانم کمک کنم؟')
    calls = {'chat': 0}

    async def fake_chat(messages, **kwargs):
        calls['chat'] += 1
        return 'نباید فراخوانی شود'

    monkeypatch.setattr(ai_assistant, 'chat_completion', fake_chat)
    monkeypatch.setattr(ai_assistant, 'needs_web_search', lambda _q: False)
    ai_assistant.request_limiter.reset(501)

    result = await ai_assistant.ask_ai(501, 'سلام')
    assert result.cached is True
    assert calls['chat'] == 0
    rows = models.get_ai_history(501, 20)
    assert [r['role'] for r in rows[-2:]] == ['user', 'assistant']
    assert rows[-2]['message'] == 'سلام'
    assert rows[-1]['message'] == 'سلام! چطور می‌توانم کمک کنم؟'


def test_atomic_exchange_prunes_to_20_and_keeps_pairs():
    models.init_ai_db()
    for i in range(12):
        models.add_ai_exchange(777, f'u{i}', f'a{i}')
    rows = models.get_ai_history(777, 20)
    assert len(rows) == 20
    assert rows[0]['message'] == 'u2'
    for idx in range(0, len(rows), 2):
        assert rows[idx]['role'] == 'user'
        assert rows[idx + 1]['role'] == 'assistant'


@pytest.mark.asyncio
async def test_web_context_cache_prevents_duplicate_search_calls(monkeypatch):
    calls = {'search': 0}

    async def fake_search(_q):
        calls['search'] += 1
        return [{'title': 'R', 'snippet': 'fresh', 'url': 'https://example.com/r'}]

    monkeypatch.setattr(ai_assistant, 'search_web', fake_search)
    first = await ai_assistant._fresh_search_context('خبر امروز تست')
    second = await ai_assistant._fresh_search_context('خبر   امروز   تست')
    assert first == second
    assert calls['search'] == 1


@pytest.mark.asyncio
async def test_empty_web_results_are_not_reported_as_fresh_search(monkeypatch):
    async def fake_search(_q):
        return []

    captured = {}

    async def fake_chat(messages, **kwargs):
        captured['messages'] = messages
        return 'سرچ زنده در دسترس نبود.'

    monkeypatch.setattr(ai_assistant, 'search_web', fake_search)
    monkeypatch.setattr(ai_assistant, 'chat_completion', fake_chat)
    monkeypatch.setattr(ai_assistant, 'needs_web_search', lambda _q: True)
    ai_assistant.request_limiter.reset(909)

    result = await ai_assistant.ask_ai(909, 'خبر امروز چیست؟')
    assert result.search_failed is True
    assert any('live web search is unavailable' in m['content'] for m in captured['messages'] if m['role'] == 'system')


@pytest.mark.asyncio
async def test_retired_bing_provider_fails_cleanly_without_network(monkeypatch):
    from services import web_search

    monkeypatch.setattr(web_search, 'SEARCH_API_KEY', 'test-key')
    monkeypatch.setattr(web_search, 'SEARCH_API_PROVIDER', 'bing')
    with pytest.raises(web_search.WebSearchError, match='بازنشسته'):
        await web_search.search_web('test')


@pytest.mark.asyncio
async def test_two_users_can_run_ai_concurrently_without_memory_mix(monkeypatch):
    import asyncio

    gate = asyncio.Event()
    entered = set()

    async def fake_chat(messages, **kwargs):
        question = messages[-1]['content']
        entered.add(question)
        if len(entered) == 2:
            gate.set()
        await asyncio.wait_for(gate.wait(), timeout=1)
        return f'answer:{question}'

    monkeypatch.setattr(ai_assistant, 'chat_completion', fake_chat)
    monkeypatch.setattr(ai_assistant, 'needs_web_search', lambda _q: False)
    ai_assistant.request_limiter.reset(1001)
    ai_assistant.request_limiter.reset(1002)

    r1, r2 = await asyncio.gather(
        ai_assistant.ask_ai(1001, 'user-one'),
        ai_assistant.ask_ai(1002, 'user-two'),
    )
    assert r1.text == 'answer:user-one'
    assert r2.text == 'answer:user-two'
    assert [r['message'] for r in models.get_ai_history(1001)] == ['user-one', 'answer:user-one']
    assert [r['message'] for r in models.get_ai_history(1002)] == ['user-two', 'answer:user-two']


@pytest.mark.asyncio
async def test_same_user_ai_requests_are_serialized(monkeypatch):
    import asyncio

    active = 0
    max_active = 0

    async def fake_chat(messages, **kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        question = messages[-1]['content']
        active -= 1
        return f'a:{question}'

    monkeypatch.setattr(ai_assistant, 'chat_completion', fake_chat)
    monkeypatch.setattr(ai_assistant, 'needs_web_search', lambda _q: False)
    ai_assistant.request_limiter.reset(1003)

    await asyncio.gather(
        ai_assistant.ask_ai(1003, 'q1'),
        ai_assistant.ask_ai(1003, 'q2'),
    )
    assert max_active == 1
    rows = models.get_ai_history(1003, 20)
    assert [r['role'] for r in rows] == ['user', 'assistant', 'user', 'assistant']


@pytest.mark.asyncio
async def test_avalai_chat_client_builds_openai_compatible_request(monkeypatch):
    import json
    from services import avalai_ai

    captured = {}

    class FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def text(self):
            return json.dumps({'choices': [{'message': {'content': 'سلام'}}]})

    class FakeSession:
        def __init__(self, *, timeout):
            captured['timeout'] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def post(self, url, *, headers, json):
            captured['url'] = url
            captured['headers'] = headers
            captured['json'] = json
            return FakeResponse()

    monkeypatch.setattr(avalai_ai, 'AVALAI_API_KEY', 'test-secret')
    monkeypatch.setattr(avalai_ai, 'AVALAI_BASE_URL', 'https://api.example.test/v1/')
    monkeypatch.setattr(avalai_ai.aiohttp, 'ClientSession', FakeSession)

    text = await avalai_ai.chat_completion(
        [{'role': 'user', 'content': 'سلام'}],
        model='test-model',
        temperature=0.2,
        max_tokens=321,
    )
    assert text == 'سلام'
    assert captured['url'] == 'https://api.example.test/v1/chat/completions'
    assert captured['headers']['Authorization'] == 'Bearer test-secret'
    assert captured['json']['model'] == 'test-model'
    assert captured['json']['messages'] == [{'role': 'user', 'content': 'سلام'}]
    assert captured['json']['temperature'] == 0.2
    assert captured['json']['max_tokens'] == 321


@pytest.mark.asyncio
async def test_avalai_http_error_is_wrapped(monkeypatch):
    from services import avalai_ai

    class FakeResponse:
        status = 429

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def text(self):
            return '{"error":{"message":"rate limited"}}'

    class FakeSession:
        def __init__(self, *, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(avalai_ai, 'AVALAI_API_KEY', 'test-secret')
    monkeypatch.setattr(avalai_ai.aiohttp, 'ClientSession', FakeSession)
    with pytest.raises(avalai_ai.AvalAIChatError) as exc:
        await avalai_ai.chat_completion([{'role': 'user', 'content': 'x'}])
    assert exc.value.status_code == 429
    # v0.09.19: خطاهای شناخته‌شده فارسی می‌شوند (طبقه‌بند مشترک)
    assert 'محدودیت' in str(exc.value)


@pytest.mark.asyncio
async def test_avalai_credit_error_shows_persian_message(monkeypatch):
    """خطای اتمام اعتبار (402) باید فارسی و قابل‌فهم باشد، نه خام انگلیسی."""
    from services import avalai_ai

    class FakeResponse:
        status = 402

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def text(self):
            return ('{"error":{"message":"Your account credit has been '
                    'exhausted. Please top up at https://ava.al/billing"}}')

    class FakeSession:
        def __init__(self, *, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(avalai_ai, 'AVALAI_API_KEY', 'test-secret')
    monkeypatch.setattr(avalai_ai.aiohttp, 'ClientSession', FakeSession)
    with pytest.raises(avalai_ai.AvalAIChatError) as exc:
        await avalai_ai.chat_completion([{'role': 'user', 'content': 'x'}])
    assert exc.value.status_code == 402
    assert 'اعتبار' in str(exc.value)


@pytest.mark.asyncio
async def test_avalai_unknown_error_passes_through_raw(monkeypatch):
    """سازگاری عقب: خطای ناشناخته مثل قبل همان متن خام را نشان می‌دهد."""
    from services import avalai_ai

    class FakeResponse:
        status = 418

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def text(self):
            return '{"error":{"message":"weird gateway said boom"}}'

    class FakeSession:
        def __init__(self, *, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(avalai_ai, 'AVALAI_API_KEY', 'test-secret')
    monkeypatch.setattr(avalai_ai.aiohttp, 'ClientSession', FakeSession)
    with pytest.raises(avalai_ai.AvalAIChatError) as exc:
        await avalai_ai.chat_completion([{'role': 'user', 'content': 'x'}])
    assert exc.value.status_code == 418
    assert 'weird gateway said boom' in str(exc.value)
