"""تست‌های سرویس تبدیل صوت به متن — زنجیره تأمین‌کننده + طبقه‌بندی خطا (v0.09.19).

ریشه: قابلیت STT فقط به AvalAI وابسته بود؛ اتمام اعتبار = شکست کامل قابلیت
با خطای خام انگلیسی. این تست‌ها رفتار جدید را قفل می‌کنند:
fallback خودکار، مدارشکن، پیام فارسی + راهنما، و سازگاری عقب (بدون پشتیبان
رفتار دقیقاً مثل قبل).
"""
import asyncio

import pytest

import config
from services import stt_service
from services.ai_error_classifier import classify_provider_error
from services.stt_service import (
    SttFallbackError,
    build_stt_chain,
    clear_provider_cooldowns,
    format_stt_failure,
    parse_stt_fallback_providers,
    transcribe_with_fallback,
)
from avalai_audio import AvalAIError

# دقیقاً همان متنی که کاربر از AvalAI گرفت
CREDIT_TEXT = (
    "Your account credit has been exhausted. You don't have enough credit to "
    "make this request. Please top up your account or add more credit at "
    "https://ava.al/billing to continue using the service."
)


# ============================ طبقه‌بند خطا ============================

def test_classifier_maps_real_credit_error_to_persian():
    kind, fa = classify_provider_error(CREDIT_TEXT)
    assert kind == 'credit'
    assert 'اعتبار' in fa


def test_classifier_http_402_is_credit_regardless_of_text():
    kind, fa = classify_provider_error('anything else', 402)
    assert kind == 'credit'
    assert 'اعتبار' in fa


def test_classifier_auth_rate_server_by_status():
    assert classify_provider_error('x', 401)[0] == 'auth'
    assert classify_provider_error('x', 403)[0] == 'auth'
    assert classify_provider_error('x', 429)[0] == 'rate'
    assert classify_provider_error('x', 500)[0] == 'server'
    assert classify_provider_error('x', 503)[0] == 'server'


def test_classifier_text_patterns_auth_rate_timeout():
    assert classify_provider_error('Invalid API Key provided')[0] == 'auth'
    assert classify_provider_error('Rate limit exceeded')[0] == 'rate'
    assert classify_provider_error('Request timed out')[0] == 'timeout'


def test_classifier_unknown_text_passes_through_unchanged():
    # سازگاری عقب: خطای ناشناخته باید همان متن قبلی را نشان دهد
    raw = 'weird gateway said boom'
    kind, fa = classify_provider_error(raw)
    assert kind == 'other'
    assert fa == raw


# ============================ پارسر کانفیگ ============================

def test_parse_empty_variants_return_empty_list():
    assert parse_stt_fallback_providers(None) == []
    assert parse_stt_fallback_providers('') == []
    assert parse_stt_fallback_providers('   ') == []


def test_parse_json_list_with_defaults():
    raw = ('[{"base_url":"https://api.groq.com/openai/v1","api_key":"gsk_x"},'
           '{"base_url":"https://api.openai.com/v1","api_key":"sk_y",'
           '"model":"whisper-1","label":"OpenAI"}]')
    providers = parse_stt_fallback_providers(raw)
    assert len(providers) == 2
    assert providers[0]['label'] == 'api.groq.com'  # label از URL
    assert providers[0]['model'] == 'whisper-1'  # مدل پیش‌فرض
    assert providers[1]['label'] == 'OpenAI'
    assert providers[1]['model'] == 'whisper-1'


def test_parse_python_literal_list_also_accepted():
    raw = "[{'base_url': 'https://x.test/v1', 'api_key': 'k1', 'model': 'm1'}]"
    providers = parse_stt_fallback_providers(raw)
    assert len(providers) == 1
    assert providers[0]['api_key'] == 'k1'


def test_parse_invalid_config_never_crashes():
    assert parse_stt_fallback_providers('not json {') == []
    assert parse_stt_fallback_providers('{"base_url": "x"}') == []  # dict، نه list
    assert parse_stt_fallback_providers('[42, "str", null]') == []


def test_parse_skips_entries_without_key_or_url():
    raw = ('[{"base_url":"https://a.test/v1"},'
           '{"api_key":"k"},'
           '{"base_url":"https://b.test/v1","api_key":"ok"}]')
    providers = parse_stt_fallback_providers(raw)
    assert len(providers) == 1
    assert providers[0]['base_url'] == 'https://b.test/v1'


# ============================ ساخت زنجیره ============================

def test_chain_primary_only_when_no_fallback():
    chain = build_stt_chain('key', 'https://api.avalai.ir/v1', 'whisper-1', '')
    assert [p['label'] for p in chain] == ['AvalAI']


def test_chain_primary_then_fallbacks_in_order():
    fb = '[{"base_url":"https://g.test/v1","api_key":"k1","label":"Groq","model":"whisper-large-v3"}]'
    chain = build_stt_chain('key', 'https://api.avalai.ir/v1', 'whisper-1', fb)
    assert [p['label'] for p in chain] == ['AvalAI', 'Groq']


def test_chain_dedupes_same_url_and_model_as_primary():
    fb = '[{"base_url":"https://api.avalai.ir/v1","api_key":"k2","model":"whisper-1"}]'
    chain = build_stt_chain('key', 'https://api.avalai.ir/v1', 'whisper-1', fb)
    assert [p['label'] for p in chain] == ['AvalAI']


def test_chain_without_primary_key_uses_fallbacks_only():
    fb = '[{"base_url":"https://g.test/v1","api_key":"k1","label":"Groq"}]'
    chain = build_stt_chain('', 'https://api.avalai.ir/v1', 'whisper-1', fb)
    assert [p['label'] for p in chain] == ['Groq']


def test_chain_empty_when_nothing_configured():
    assert build_stt_chain('', '', '', '') == []


# ============================ fallback اجرا ============================

class FakeClientError(AvalAIError):
    pass


def _patch_transcribe(monkeypatch, behavior):
    """behavior: dict label → AvalAIError یا 'ok:<text>' — شمارنده تماس‌ها هم برمی‌گردد."""
    calls = {'count': {}}

    async def fake_transcribe(file_path, *, api_key, base_url, model,
                              timeout_seconds, mime_type=None):
        # برچسب را از URL تشخیص بده (همان‌طور که زنجیره ساخته شده)
        label = 'AvalAI' if 'avalai' in base_url else (
            'Groq' if 'groq' in base_url else base_url)
        calls['count'][label] = calls['count'].get(label, 0) + 1
        outcome = behavior[label]
        if isinstance(outcome, Exception):
            raise outcome
        assert outcome.startswith('ok:')
        return outcome[3:]

    monkeypatch.setattr(stt_service, 'transcribe_audio', fake_transcribe)
    return calls


CHAIN_TWO = None


def _chain_two():
    return build_stt_chain(
        'avalai-key', 'https://api.avalai.ir/v1', 'whisper-1',
        '[{"base_url":"https://api.groq.com/openai/v1","api_key":"gsk_x",'
        '"model":"whisper-large-v3","label":"Groq"}]',
    )


def setup_function(function):
    clear_provider_cooldowns()


@pytest.mark.asyncio
async def test_primary_success_does_not_touch_fallback(monkeypatch):
    calls = _patch_transcribe(monkeypatch, {'AvalAI': 'ok:سلام متن'})
    text, label = await transcribe_with_fallback(
        '/tmp/x.ogg', chain=_chain_two(), timeout_seconds=60)
    assert text == 'سلام متن'
    assert label == 'AvalAI'
    assert calls['count'] == {'AvalAI': 1}


@pytest.mark.asyncio
async def test_credit_exhausted_falls_back_and_succeeds(monkeypatch):
    # سناریوی دقیق کاربر: AvalAI بدون اعتبار → Groq پاسخ می‌دهد
    calls = _patch_transcribe(monkeypatch, {
        'AvalAI': FakeClientError(CREDIT_TEXT, status_code=402),
        'Groq': 'ok:متن از گروق',
    })
    text, label = await transcribe_with_fallback(
        '/tmp/x.ogg', chain=_chain_two(), timeout_seconds=60)
    assert text == 'متن از گروق'
    assert label == 'Groq'
    assert calls['count']['AvalAI'] == 1


@pytest.mark.asyncio
async def test_all_providers_fail_raises_persian_summary_with_help(monkeypatch):
    _patch_transcribe(monkeypatch, {
        'AvalAI': FakeClientError(CREDIT_TEXT, status_code=402),
        'Groq': FakeClientError('Invalid API Key', status_code=401),
    })
    with pytest.raises(SttFallbackError) as info:
        await transcribe_with_fallback(
            '/tmp/x.ogg', chain=_chain_two(), timeout_seconds=60)
    message = str(info.value)
    assert 'اعتبار' in message
    assert 'AvalAI' in message and 'Groq' in message
    assert 'راه حل' in message  # راهنمای عملی فقط وقتی اعتبار درمیان است
    assert isinstance(info.value, AvalAIError)  # سازگار با except فعلی self.py
    assert info.value.status_code == 402


@pytest.mark.asyncio
async def test_circuit_breaker_skips_dead_provider_on_next_call(monkeypatch):
    calls = _patch_transcribe(monkeypatch, {
        'AvalAI': FakeClientError(CREDIT_TEXT, status_code=402),
        'Groq': 'ok:متن',
    })
    await transcribe_with_fallback('/tmp/x.ogg', chain=_chain_two(), timeout_seconds=60)
    first_avalai_calls = calls['count'].get('AvalAI', 0)
    await transcribe_with_fallback('/tmp/x.ogg', chain=_chain_two(), timeout_seconds=60)
    # مدارشکن: AvalAI دیگر صدا زده نشده
    assert calls['count'].get('AvalAI', 0) == first_avalai_calls


@pytest.mark.asyncio
async def test_circuit_breaker_still_tries_single_dead_provider(monkeypatch):
    calls = _patch_transcribe(monkeypatch, {
        'AvalAI': FakeClientError(CREDIT_TEXT, status_code=402),
    })
    chain = build_stt_chain('k', 'https://api.avalai.ir/v1', 'whisper-1', '')
    for _ in range(2):
        with pytest.raises(SttFallbackError):
            await transcribe_with_fallback('/tmp/x.ogg', chain=chain, timeout_seconds=60)
    # وقتی تنها گزینه است، حتی با cooldown امتحان می‌شود
    assert calls['count']['AvalAI'] == 2


@pytest.mark.asyncio
async def test_clear_provider_cooldowns_restores_primary(monkeypatch):
    calls = _patch_transcribe(monkeypatch, {
        'AvalAI': FakeClientError(CREDIT_TEXT, status_code=402),
        'Groq': 'ok:متن',
    })
    await transcribe_with_fallback('/tmp/x.ogg', chain=_chain_two(), timeout_seconds=60)
    clear_provider_cooldowns()
    await transcribe_with_fallback('/tmp/x.ogg', chain=_chain_two(), timeout_seconds=60)
    assert calls['count']['AvalAI'] == 2


@pytest.mark.asyncio
async def test_empty_chain_raises_config_error(monkeypatch):
    with pytest.raises(SttFallbackError) as info:
        await transcribe_with_fallback('/tmp/x.ogg', chain=[], timeout_seconds=60)
    assert 'پیکربندی نشده' in str(info.value)


def test_format_failure_marks_skipped_provider():
    body = format_stt_failure([
        ('AvalAI', 'credit', 'اعتبار سرویس تمام شده است (نیاز به شارژ یا تأمین‌کننده پشتیبان). (402)'),
        ('Groq', 'skipped', 'به‌دلیل خطای قبلی موقتاً کنار گذاشته شد (مدارشکن)'),
    ])
    assert '⏭' in body and '💳' in body
    assert 'راه حل' in body


# ============================ سازگاری کانفیگ/کلاینت ============================

def test_config_has_persistent_stt_fallback_key():
    assert hasattr(config, 'STT_FALLBACK_PROVIDERS')
    assert config.STT_FALLBACK_PROVIDERS == ''  # پیش‌فرض = رفتار قبلی
    assert 'STT_FALLBACK_PROVIDERS' in config._PERSISTENT_SEED_KEYS


def test_self_py_uses_service_instead_of_direct_client():
    # قرارداد معماری: self.py فقط سرویس را صدا می‌زند؛ کلاینت مستقیم حذف شده
    import ast as _ast
    source = open('self.py', encoding='utf-8').read()
    tree = _ast.parse(source)
    calls = [
        node.func.id for node in ast_walk(tree)
        if isinstance(node, _ast.Call) and isinstance(node.func, _ast.Name)
    ]
    assert 'transcribe_with_fallback' in calls
    assert 'transcribe_audio' not in calls


def ast_walk(tree):
    import ast as _ast
    return list(_ast.walk(tree))
