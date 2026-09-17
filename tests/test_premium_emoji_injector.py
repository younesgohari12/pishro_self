"""Offline injection and real Telethon routing; fixture IDs are not live mappings."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest
from telethon import TelegramClient, errors
from telethon.sessions import MemorySession
from telethon.extensions import markdown, html
from telethon.tl import functions, types
import config
from services import custom_emoji_service as custom
from services import premium_emoji_injector as mod

DOC = 5415647572037500278

def record(alt='🔥', doc_id=DOC, free=False, animated=True):
    return custom.CustomEmojiDocument(doc_id, alt, free, animated)

def catalogue(*records):
    result = mod.EmojiCatalogue()
    result._publish(custom.EmojiResolution(tuple(records), {}, {}))
    result.expires_at = float('inf')
    return result


def engine(*records, premium=True):
    return mod.PremiumEmojiInjector(None, premium=premium, catalogue=catalogue(*records))

def run(coro):
    return asyncio.run(coro)

def extracted(text, entities):
    return custom.extract_custom_emojis(NS(message=text, entities=entities))


def test_simple_conversion_and_no_emoji():
    injector = engine(record())
    text, entities = run(injector.inject('انجام شد 🔥'))
    assert text == 'انجام شد 🔥'
    assert isinstance(entities[0], types.MessageEntityCustomEmoji)
    assert extracted(text, entities)[0]['emoji_text'] == '🔥'
    assert entities[0].document_id == DOC
    assert run(injector.inject('سلام بدون ایموجی')) == ('سلام بدون ایموجی', [])


def test_utf16_after_astral_and_zwj():
    text = '😀 👩🏽\u200d💻 سلام 🔥 ❤️'
    out, entities = run(engine(record(), record('❤️', DOC + 1)).inject(text))
    assert out == text
    records = extracted(out, entities)
    assert [r['emoji_text'] for r in records] == ['🔥', '❤️']
    assert records[0]['offset'] == custom.utf16_length(text.split('🔥')[0])
    assert records[1]['length'] == 2


def test_remaps_containing_and_following_entities(monkeypatch):
    monkeypatch.setattr(config, "PREMIUM_EMOJI_STYLE", "creative")
    text = 'موفقیت ✅ لینک'
    bold = types.MessageEntityBold(0, custom.utf16_length(text))
    link = types.MessageEntityTextUrl(custom.utf16_length('موفقیت ✅ '), 4, 'https://example.org')
    result, entities = run(engine(record()).inject(text, [bold, link]))
    assert result == 'موفقیت 🔥 لینک'
    updated_bold = next(e for e in entities if isinstance(e, types.MessageEntityBold))
    updated_link = next(e for e in entities if isinstance(e, types.MessageEntityTextUrl))
    assert updated_bold.length == custom.utf16_length(result)
    assert updated_link.offset == custom.utf16_length('موفقیت 🔥 ')
    assert updated_link.url == link.url
    assert link.offset == custom.utf16_length('موفقیت ✅ ')
    assert bold.length == custom.utf16_length(text)


@pytest.mark.parametrize('text,expected', [
    ('عصبانی 🔥', '😈'), ('موفقیت 🔥', '⚡'), ('عشق ✨', '❤️'), ('لوکس ✨', '👑'),
])
def test_sentiment_deterministic(text, expected, monkeypatch):
    monkeypatch.setattr(config, "PREMIUM_EMOJI_STYLE", "creative")
    injector = engine(record('😈'), record('⚡', DOC + 1), record('❤️', DOC + 2), record('👑', DOC + 3))
    first = run(injector.inject(text))
    assert first[0].endswith(expected)
    assert run(injector.inject(text))[0] == first[0]


def test_unrelated_sentiment():
    assert run(engine(record('😈')).inject('عصبانی 😂')) == ('عصبانی 😂', [])


def test_exact_and_disabled(monkeypatch):
    injector = engine(record())
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_STYLE', 'exact')
    assert run(injector.inject('✅')) == ('✅', [])
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_ENABLED', False)
    assert run(injector.inject('🔥')) == ('🔥', [])


@pytest.mark.parametrize('text', ['❤️\u200d🔥', '🤟🏽', '🔥\ufe0e'])
def test_no_partial_grapheme(text):
    assert run(engine(record(), record('❤️', DOC + 1), record('🤟', DOC + 2)).inject(text)) == (text, [])


@pytest.mark.parametrize('kind', [types.MessageEntityCode, types.MessageEntityPre,
    types.MessageEntityTextUrl, types.MessageEntityCustomEmoji])
def test_protected_spans(kind):
    params = {'language': 'python'} if kind is types.MessageEntityPre else {}
    if kind is types.MessageEntityTextUrl: params['url'] = 'https://example.org'
    if kind is types.MessageEntityCustomEmoji: params['document_id'] = DOC + 99
    existing = kind(0, 2, **params)
    assert run(engine(record()).inject('🔥', [existing])) == ('🔥', [existing])


def test_idempotent():
    injector = engine(record())
    text, entities = run(injector.inject('🔥🔥'))
    again, second = run(injector.inject(text, entities))
    assert again == text and len(second) == 2
    assert [e.document_id for e in second] == [DOC, DOC]


def test_malformed_utf16_fallback():
    bad = types.MessageEntityBold(1, 1)
    assert run(engine(record()).inject('🔥', [bad])) == ('🔥', [bad])


def test_processing_and_lookup_fallback(monkeypatch):
    injector = engine(record())
    previous = injector.catalogue.candidates
    monkeypatch.setattr(injector.catalogue, 'candidates', lambda *args: 1 / 0)
    assert run(injector.inject('🔥')) == ('🔥', [])
    monkeypatch.setattr(injector.catalogue, 'candidates', previous)
    monkeypatch.setattr(custom, 'create_custom_emoji', lambda *args: 1 / 0)
    assert run(injector.inject('🔥')) == ('🔥', [])


def test_free_only_for_non_premium():
    assert run(engine(record(), premium=False).inject('🔥')) == ('🔥', [])
    assert len(run(engine(record(free=True), premium=False).inject('🔥'))[1]) == 1


def test_bounded_cache_and_fresh_entities():
    injector = engine(record())
    _, first = run(injector.inject('🔥'))
    first[0].offset = 99
    assert run(injector.inject('🔥'))[1][0].offset == 0
    for i in range(260): run(injector.inject(f'{i} 🔥'))
    assert 0 < len(injector.cache) <= 256
    assert injector.cache_bytes <= mod.MAX_CACHE_BYTES
    assert len(run(injector.inject('🔥' * 150))[1]) == 100


def test_catalogue_singleflight_and_pool(monkeypatch):
    async def main():
        resolver = AsyncMock(return_value=custom.EmojiResolution((record(),), {DOC + 1: 'invalid_alt'}, {}))
        monkeypatch.setattr(custom, 'resolve_custom_emoji_catalogue', resolver)
        monkeypatch.setattr(mod, 'PREMIUM_EMOJI_POOL', {name: [] for name in mod.CATEGORY_EMOJIS})
        catalogue = mod.EmojiCatalogue()
        results = await asyncio.gather(*(catalogue.warmup(None) for _ in range(10)))
        assert all(r == (record(),) for r in results)
        assert resolver.await_count == 1
        assert mod.PREMIUM_EMOJI_POOL['fire'] == [DOC]
        assert mod.PREMIUM_EMOJI_POOL['angry'] == [DOC]
    run(main())


def test_failure_cooldown_and_cancellation(monkeypatch):
    resolver = AsyncMock(side_effect=RuntimeError('offline'))
    monkeypatch.setattr(custom, 'resolve_custom_emoji_catalogue', resolver)
    catalogue = mod.EmojiCatalogue()
    assert run(catalogue.warmup(None)) == ()
    assert run(catalogue.warmup(None)) == ()
    assert resolver.await_count == 1
    catalogue.expires_at = 0
    resolver.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError): run(catalogue.warmup(None))


def test_shared_service_batches_metadata():
    async def request(req):
        return [NS(id=i, mime_type='video/webm', attributes=[
            types.DocumentAttributeCustomEmoji('🔥', types.InputStickerSetEmpty(), free=True)])
            for i in req.document_id]
    client = AsyncMock(side_effect=request)
    records = run(custom.resolve_custom_emoji_documents(client, range(1, 211)))
    assert len(records) == 210 and client.await_count == 3
    assert records[0] == custom.CustomEmojiDocument(1, '🔥', True, True, 'video/webm')


class FakeClient:
    def __init__(self):
        self.sent = []
        self.failure = None
    async def _parse_message_text(self, text, mode):
        if mode is None: return text, []
        return (html if mode == 'html' else markdown).parse(text)
    async def send_message(self, entity, message='', *, parse_mode=(), formatting_entities=None, **kwargs):
        self.sent.append(('message', entity, message, parse_mode, formatting_entities, kwargs))
        if self.failure:
            exc, self.failure = self.failure, None
            raise exc
        return self.sent[-1]
    async def send_file(self, entity, file, *, caption=None, parse_mode=(), formatting_entities=None, **kwargs):
        self.sent.append(('file', entity, caption, parse_mode, formatting_entities, kwargs))
        return self.sent[-1]
    async def edit_message(self, entity, message=None, text=None, *, parse_mode=(), formatting_entities=None, **kwargs):
        self.sent.append(('edit', entity, text, parse_mode, formatting_entities, kwargs))
        return self.sent[-1]


def install(client):
    injector = mod.install_premium_emoji_injector(client, premium=True)
    injector.catalogue = catalogue(record())
    return injector


@pytest.mark.parametrize('mode,text', [('md', '**موفقیت ✅**'), ('html', '<b>موفقیت ✅</b>')])
def test_markup_and_send_options(mode, text, monkeypatch):
    monkeypatch.setattr(config, "PREMIUM_EMOJI_STYLE", "creative")
    client = FakeClient()
    install(client)
    run(client.send_message(1, text, parse_mode=mode, reply_to=7, silent=True, schedule=123))
    _, _, text, mode, entities, options = client.sent[0]
    assert text == 'موفقیت 🔥' and mode is None
    assert any(isinstance(e, types.MessageEntityBold) for e in entities)
    assert extracted(text, entities)[0]['emoji_text'] == '🔥'
    assert options == dict(reply_to=7, silent=True, schedule=123)


def test_album_and_explicit_entities(monkeypatch):
    monkeypatch.setattr(config, "PREMIUM_EMOJI_STYLE", "creative")
    client = FakeClient()
    install(client)
    run(client.send_file(1, ['a.jpg', 'b.jpg'], caption=['✅', '**plain**']))
    _, _, captions, mode, entities, _ = client.sent[0]
    assert captions == ['🔥', 'plain'] and mode is None
    assert isinstance(entities[0][0], types.MessageEntityCustomEmoji)
    assert isinstance(entities[1][0], types.MessageEntityBold)
    run(client.send_message(1, '**🔥**', formatting_entities=[]))
    assert client.sent[-1][2] == '**🔥**'
    assert client.sent[-1][4][0].offset == 2


def test_message_clone_and_idempotent_install(monkeypatch):
    monkeypatch.setattr(config, "PREMIUM_EMOJI_STYLE", "creative")
    client, main_bot = FakeClient(), FakeClient()
    first = install(client)
    method = client.send_message
    assert mod.install_premium_emoji_injector(client, premium=True) is first
    assert client.send_message is method
    message = types.Message(id=1, peer_id=types.PeerUser(1), message='✅',
                            entities=[types.MessageEntityBold(0, 1)])
    run(client.send_message(1, message))
    prepared = client.sent[0][2]
    assert prepared is not message and message.message == '✅'
    assert prepared.message == '🔥' and prepared.entities[0].length == 2
    run(main_bot.send_message(1, '🔥'))
    assert main_bot.sent[0][4] is None


def test_rejected_send_retries_original_once(monkeypatch):
    monkeypatch.setattr(config, "PREMIUM_EMOJI_STYLE", "creative")
    client = FakeClient()
    install(client)
    client.failure = errors.DocumentInvalidError(request=None)
    run(client.send_message(1, '**✅**', parse_mode='md', reply_to=3))
    assert len(client.sent) == 2 and client.sent[0][2] == '🔥'
    assert client.sent[1][2:5] == ('**✅**', 'md', None)
    run(client.send_message(1, '🔥'))
    assert client.sent[-1][4] is None


@pytest.mark.parametrize('failure', [TimeoutError(), errors.FloodWaitError(request=None, capture=30)])
def test_transport_errors_never_retry(failure):
    client = FakeClient()
    install(client)
    client.failure = failure
    with pytest.raises(type(failure)): run(client.send_message(1, '🔥'))
    assert len(client.sent) == 1


def test_real_telethon_send_reply_respond_edit_media(monkeypatch):
    monkeypatch.setattr(config, "PREMIUM_EMOJI_STYLE", "creative")
    async def main():
        client = TelegramClient(MemorySession(), 12345, 'test-only')
        install(client)
        peer = types.InputPeerUser(77, 1)
        requests = []
        now = datetime.now(timezone.utc)
        async def transport(sender, request, **kwargs):
            requests.append(request)
            if isinstance(request, functions.messages.SendMessageRequest):
                return types.UpdateShortSentMessage(42, 1, 1, now, out=True, entities=request.entities)
            msg = types.Message(id=42, peer_id=types.PeerUser(77), message=request.message,
                                date=now, entities=request.entities)
            update = (types.UpdateEditMessage(msg, 1, 1) if isinstance(request, functions.messages.EditMessageRequest)
                      else types.UpdateNewMessage(msg, 1, 1))
            return types.Updates([update], [], [], now, 1)
        client._call = transport
        sent = await client.send_message(peer, '**✅**')
        assert sent.message == '🔥'
        incoming = types.Message(id=9, peer_id=types.PeerUser(77), message='hello', date=now)
        incoming._finish_init(client, {}, peer)
        await incoming.reply('✅')
        await incoming.respond('✅')
        await sent.edit('موفقیت ✅')
        await client.send_file(peer, types.InputMediaPhoto(types.InputPhoto(1, 1, b'ref')), caption='✅')
        assert len(requests) == 5
        assert isinstance(requests[3], functions.messages.EditMessageRequest)
        assert isinstance(requests[4], functions.messages.SendMediaRequest)
        assert requests[1].reply_to.reply_to_msg_id == 9
        for request in requests:
            assert extracted(request.message, request.entities)[0]['emoji_text'] == '🔥'
    run(main())


@pytest.mark.parametrize('text', ['`🔥`', '```python\nx="🔥"\n```', 'https://example.org/🔥'])
def test_literal_code_and_urls_in_plain_output_preserved(text):
    assert run(engine(record()).inject(text)) == (text, [])


def test_album_partial_failure_is_never_replayed():
    class Client(FakeClient):
        async def send_file(self, entity, file, *, caption=None, parse_mode=(), formatting_entities=None):
            self.sent.append(file)
            raise errors.DocumentInvalidError(request=None)
    client = Client()
    install(client)
    with pytest.raises(errors.DocumentInvalidError):
        run(client.send_file(1, ['a.jpg', 'b.jpg'], caption=['🔥', '🔥']))
    assert len(client.sent) == 1
