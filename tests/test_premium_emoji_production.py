"""Logic-only fixtures. No synthetic ID/alt here certifies live visuals."""
import asyncio
import logging
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from telethon import errors, TelegramClient
from telethon.sessions import MemorySession
from telethon.tl import types, functions

import config
from services import custom_emoji_service as custom
from services import premium_emoji_injector as mod
from test_premium_emoji_injector import FakeClient, catalogue, engine, record, run, extracted

A, B, C = mod.CHANNEL_DOCUMENT_IDS[:3]


def ready_client(*records):
    client = FakeClient()
    injector = mod.install_premium_emoji_injector(client, premium=True)
    injector.catalogue = catalogue(*records)
    return client, injector


@pytest.mark.parametrize('style', ['exact-smart', 'smart', 'exact', 'unknown', 'gang', 'love'])
def test_default_and_legacy_styles_never_mutate_semantics(monkeypatch, style):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_STYLE', style)
    injector = engine(record('🔥', A), record('😂', B))
    assert run(injector.inject('موفقیت ✅ عشق ❤️')) == ('موفقیت ✅ عشق ❤️', [])
    text, entities = run(injector.inject('موفقیت 🔥 خنده 😂'))
    assert text == 'موفقیت 🔥 خنده 😂'
    assert [r['emoji_text'] for r in extracted(text, entities)] == ['🔥', '😂']


def test_only_explicit_creative_mode_changes_glyph(monkeypatch):
    injector = engine(record('🔥', A))
    assert run(injector.inject('✅')) == ('✅', [])
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_STYLE', 'creative')
    assert run(injector.inject('✅'))[0] == '🔥'


def test_curated_priority_over_animation_and_numeric_id(monkeypatch):
    monkeypatch.setattr(mod, 'CURATED_PREMIUM_MAP', {'🔥': [A, B]})
    injector = engine(record('🔥', B, animated=True), record('🔥', A, animated=False))
    assert run(injector.inject('🔥'))[1][0].document_id == A
    assert injector.catalogue.priority('🔥', record('🔥', A), 'default')[1] == 0


def test_uncurated_tie_uses_source_order_not_id_magnitude():
    # A > C numerically, but A appears first in the observed channel source.
    assert A > C
    injector = engine(record('🔥', C), record('🔥', A))
    assert run(injector.inject('🔥'))[1][0].document_id == A


def test_curated_mismatch_and_outside_source_rejected(monkeypatch):
    monkeypatch.setattr(mod, 'CURATED_PREMIUM_MAP', {'🔥': [A, 123]})
    injector = engine(record('❤️', A), record('🔥', B))
    assert injector.catalogue.curated == {}
    assert set(injector.catalogue.curated_rejected.values()) == {'alt_mismatch', 'not_in_channel_sources'}
    assert run(injector.inject('🔥'))[1][0].document_id == B


def test_keyword_selects_only_reviewed_same_alt_variant(monkeypatch):
    monkeypatch.setattr(mod, 'CURATED_PREMIUM_MAP', {'😎': [A, B]})
    monkeypatch.setattr(mod, 'CURATED_VARIANT_MAP', {'gang': {'😎': [B]}, 'love': {'😎': [A]}})
    injector = engine(record('😎', A), record('😎', B))
    assert run(injector.inject('گنگ 😎'))[1][0].document_id == B
    assert run(injector.inject('عشق 😎'))[1][0].document_id == A
    assert run(injector.inject('گنگ 😎'))[0] == 'گنگ 😎'


def test_common_inventory_and_recognition_beyond_inventory():
    required = '👍 👎 🙏 🤍 🫡 🤔 😊 🙂 😉 😏 😍 🥰 😘 😂 🤣 😭 🥲 😅 😁 😈 😡 🤬 🔥 ⚡ ✨ ⭐ 🌟 💫 ❤️ 🖤 💙 💜 💚 💛 💔 💕 💖 💘 👑 💎 💰 🏆 💯 🎯 🚀 ✅ ❌ ✔️ 🤝 🫶 👌 🤟 🤘 💪 😎 🗿 💀 🎉 🎊'.split()
    assert set(required) <= set(mod.COMMON_EMOJIS)
    assert len(mod.COMMON_EMOJIS) >= 180
    for emoji in required + ['🦄', '👩🏽\u200d💻', '🇮🇷', '1️⃣']:
        assert mod.EMOJI_PATTERN.fullmatch(emoji)
        text, entities = run(engine(record(emoji, A)).inject(emoji))
        assert text == emoji and len(entities) == 1


@pytest.mark.parametrize('emoji', ['👩🏽\u200d💻', '❤️\u200d🔥', '👍🏽', '1️⃣', '🇮🇷', '🏳️\u200d🌈',
    '🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f'])
def test_complete_sequences_get_one_valid_utf16_entity(emoji):
    prefix = '😀 سلام '
    text, entities = run(engine(record(emoji, A)).inject(prefix + emoji))
    assert text == prefix + emoji and len(entities) == 1
    assert entities[0].offset == custom.utf16_length(prefix)
    assert entities[0].length == custom.utf16_length(emoji)
    assert extracted(text, entities)[0]['emoji_text'] == emoji


@pytest.mark.parametrize('text,alt', [('👍🏽', '👍'), ('1️⃣', '1'), ('❤️\u200d🔥', '❤️'),
    ('🏴\U000e0067\U000e0062', '🏴'), ('🇮🇷🇺', '🇮🇷')])
def test_no_partial_sequence(text, alt):
    assert run(engine(record(alt, A)).inject(text)) == (text, [])


def test_variation_selectors_remain_byte_exact():
    injector = engine(record('❤️', A))
    assert run(injector.inject('❤')) == ('❤', [])
    assert run(injector.inject('❤️'))[0] == '❤️'
    assert run(injector.inject('❤\ufe0e')) == ('❤\ufe0e', [])


def test_any_existing_custom_emoji_skips_entire_message():
    injector = engine(record('🔥', A))
    existing = types.MessageEntityCustomEmoji(0, 2, B)
    assert run(injector.inject('🔥 🔥', [existing])) == ('🔥 🔥', [existing])
    assert not injector.cache


@pytest.mark.parametrize('kind', [types.MessageEntityBold, types.MessageEntityItalic,
    types.MessageEntityUnderline, types.MessageEntityStrike, types.MessageEntitySpoiler])
def test_formatting_types_survive_exact_injection(kind):
    text = 'سلام 🔥'
    existing = kind(0, custom.utf16_length(text))
    result, entities = run(engine(record('🔥', A)).inject(text, [existing]))
    assert result == text
    preserved = next(e for e in entities if isinstance(e, kind))
    assert preserved.to_dict() == existing.to_dict()
    assert preserved is not existing


@pytest.mark.parametrize('kind', [types.MessageEntityUrl, types.MessageEntityTextUrl,
                                 types.MessageEntityCode, types.MessageEntityPre])
def test_protected_entity_payload_preserved(kind):
    kwargs = {'url': 'https://example.org'} if kind is types.MessageEntityTextUrl else {}
    if kind is types.MessageEntityPre: kwargs = {'language': 'python'}
    existing = kind(0, 2, **kwargs)
    assert run(engine(record('🔥', A)).inject('🔥', [existing])) == ('🔥', [existing])


@pytest.mark.parametrize('mode,text', [('md', '**🔥**'), ('html', '<b>🔥</b>')])
def test_markup_default_exact(mode, text):
    client, _ = ready_client(record('🔥', A))
    run(client.send_message(1, text, parse_mode=mode))
    _, _, plain, _, entities, _ = client.sent[0]
    assert plain == '🔥'
    assert isinstance(entities[0], types.MessageEntityBold)
    assert extracted(plain, entities)[0]['emoji_text'] == '🔥'


@pytest.mark.parametrize('operation', ['send_file', 'send_message_file', 'edit_file', 'message_media'])
def test_documentinvalid_media_is_never_retried(operation):
    class Client(FakeClient):
        async def send_file(self, entity, file, *, caption=None, parse_mode=(), formatting_entities=None):
            self.sent.append(caption)
            raise errors.DocumentInvalidError(request=None)
        async def edit_message(self, entity, message=None, text=None, *, file=None, parse_mode=(), formatting_entities=None):
            self.sent.append(text)
            raise errors.DocumentInvalidError(request=None)
    client = Client()
    injector = mod.install_premium_emoji_injector(client, premium=True)
    injector.catalogue = catalogue(record('🔥', A))
    client.failure = errors.DocumentInvalidError(request=None)
    if operation == 'send_file': coro = client.send_file(1, 'picture.jpg', caption='🔥')
    elif operation == 'send_message_file': coro = client.send_message(1, '🔥', file='picture.jpg')
    elif operation == 'edit_file': coro = client.edit_message(1, 2, '🔥', file='picture.jpg')
    else:
        message = types.Message(1, peer_id=types.PeerUser(1), message='🔥', media=types.MessageMediaPhoto())
        coro = client.send_message(1, message)
    with pytest.raises(errors.DocumentInvalidError): run(coro)
    assert len(client.sent) == 1


def test_plain_text_documentinvalid_can_fallback_once():
    client, _ = ready_client(record('🔥', A))
    client.failure = errors.DocumentInvalidError(request=None)
    run(client.send_message(1, '🔥'))
    assert len(client.sent) == 2 and client.sent[-1][4] is None


def test_nonpremium_filters_curated_paid_variants(monkeypatch):
    monkeypatch.setattr(mod, 'CURATED_PREMIUM_MAP', {'🔥': [A, B]})
    injector = engine(record('🔥', A), record('🔥', B, free=True), premium=False)
    assert run(injector.inject('🔥'))[1][0].document_id == B


def test_pending_warmup_never_delays_send(monkeypatch):
    async def main():
        entered, release = asyncio.Event(), asyncio.Event()
        async def resolve(*args):
            entered.set()
            await release.wait()
            return custom.EmojiResolution((record('🔥', A),), {}, {})
        monkeypatch.setattr(custom, 'resolve_custom_emoji_catalogue', resolve)
        cat = mod.EmojiCatalogue()
        injector = mod.PremiumEmojiInjector(None, premium=True, catalogue=cat)
        injector.start_warmup()
        await entered.wait()
        assert await asyncio.wait_for(injector.inject('🔥'), 0.1) == ('🔥', [])
        release.set()
        await cat.task
        assert (await injector.inject('🔥'))[1][0].document_id == A
    run(main())


def test_install_starts_warmup_before_first_send_and_close_cancels(monkeypatch):
    async def main():
        entered = asyncio.Event()
        async def resolve(*args):
            entered.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(custom, 'resolve_custom_emoji_catalogue', resolve)
        cat = mod.EmojiCatalogue()
        monkeypatch.setattr(mod, '_CATALOGUE', cat)
        client = TelegramClient(MemorySession(), 12345, 'test-only')
        injector = mod.install_premium_emoji_injector(client, premium=True)
        await entered.wait()
        assert cat.task is not None and not cat.task.done()
        await mod.close_premium_emoji_injector(client)
        assert cat.task.cancelled()
        assert cat.task_client is None
        assert not injector.cache
    run(main())


def test_cache_generation_and_memory_bound_and_no_missing_alt_leak():
    injector = engine(record('🔥', A))
    assert run(injector.inject('🔥'))[1][0].document_id == A
    injector.catalogue._publish(custom.EmojiResolution((record('🔥', B),), {}, {}))
    assert run(injector.inject('🔥'))[1][0].document_id == B
    for i in range(100): run(injector.inject(str(i) + 'x' * 8000 + '🔥'))
    assert injector.cache_bytes <= mod.MAX_CACHE_BYTES and len(injector.cache) <= 256
    before = len(injector.catalogue._ranked)
    for i in range(1000): injector.catalogue.candidates(f'missing{i}', True)
    assert len(injector.catalogue._ranked) == before


def test_debug_diagnostics_have_metadata_and_are_deduplicated(caplog):
    injector = engine(record('🔥', A))
    with caplog.at_level(logging.INFO, logger=mod.__name__): run(injector.inject('🔥'))
    assert not any('selection' in r.message for r in caplog.records)
    with caplog.at_level(logging.DEBUG, logger=mod.__name__):
        run(injector.inject('سلام 🔥'))
        run(injector.inject('دیگر 🔥'))
    rows = [r.message for r in caplog.records if 'selection' in r.message]
    assert len(rows) == 1
    for key in ('unicode', 'document_id', 'alt', 'free', 'media_kind', 'source_priority', 'style'):
        assert key in rows[0]
    assert 'سلام' not in rows[0]


def fake_document(doc_id, alt='🔥', mime='image/webp'):
    return NS(id=doc_id, mime_type=mime, attributes=[
        types.DocumentAttributeCustomEmoji(alt, types.InputStickerSetEmpty(), free=True)])


def test_resolver_rejects_deleted_missing_attribute_and_bad_alt():
    client = AsyncMock(return_value=[fake_document(A), NS(id=B, attributes=[]), fake_document(C, 'not emoji')])
    resolution = run(custom.resolve_custom_emoji_catalogue(client, [A, B, C, 123]))
    assert [r.document_id for r in resolution.documents] == [A]
    assert len(resolution.rejected) == 3 and not resolution.unresolved
    assert resolution.rejected[123] == 'missing_or_deleted'


def test_one_invalid_id_does_not_poison_batch():
    async def request(req):
        if B in req.document_id: raise errors.DocumentInvalidError(request=req)
        return [fake_document(i) for i in req.document_id]
    resolution = run(custom.resolve_custom_emoji_catalogue(AsyncMock(side_effect=request), [A, B, C]))
    assert [r.document_id for r in resolution.documents] == [A, C]
    assert resolution.rejected == {B: 'document_invalid'}


def test_network_failure_is_unresolved_not_invalid():
    resolution = run(custom.resolve_custom_emoji_catalogue(AsyncMock(side_effect=TimeoutError()), [A, B]))
    assert not resolution.documents and not resolution.rejected
    assert set(resolution.unresolved) == {A, B}


def test_resolver_partial_progress_survives_timeout():
    async def request(req):
        if A not in req.document_id: await asyncio.Event().wait()
        return [fake_document(i) for i in req.document_id]
    ids = mod.CHANNEL_DOCUMENT_IDS[:110]
    result = run(custom.resolve_custom_emoji_catalogue(AsyncMock(side_effect=request), ids, timeout=0.02))
    assert len(result.documents) == 100 and len(result.unresolved) == 10
    assert not result.rejected


@pytest.mark.parametrize('media', [False, True])
def test_tabchi_copy_mode_uses_same_wrapped_client(monkeypatch, media):
    from services import sender
    source = NS(media=types.InputMediaPhoto(types.InputPhoto(1, 1, b'ref')))
    monkeypatch.setattr(sender, 'check_delivery_policy', lambda *args: None)
    monkeypatch.setattr(sender, '_source_message', AsyncMock(return_value=source))
    client, _ = ready_client(record('🔥', A))
    banner = dict(send_mode='copy', type='photo' if media else 'text', caption='🔥', text='🔥')
    run(sender.send_banner_to_target(client, banner, {'entity': 1}))
    assert extracted(client.sent[0][2], client.sent[0][4])[0]['emoji_text'] == '🔥'


def test_tabchi_forward_keeps_original_message(monkeypatch):
    from services import sender
    source = NS(media=None, message='🔥')
    monkeypatch.setattr(sender, 'check_delivery_policy', lambda *args: None)
    monkeypatch.setattr(sender, '_source_message', AsyncMock(return_value=source))
    client, _ = ready_client(record('🔥', A))
    client.forward_messages = AsyncMock()
    run(sender.send_banner_to_target(client, {'send_mode': 'forward'}, {'entity': 1}))
    client.forward_messages.assert_awaited_once_with(1, source)
    assert not client.sent


def test_optional_live_script_offline_contract(monkeypatch):
    from tools import premium_emoji_live_smoke as smoke
    resolver = AsyncMock(return_value=custom.EmojiResolution((record('🔥', A, free=True),), {}, {}))
    monkeypatch.setattr(custom, 'resolve_custom_emoji_catalogue', resolver)
    client = NS(get_me=AsyncMock(return_value=NS(premium=False)), send_message=AsyncMock())
    result = run(smoke.smoke(client, ids=[A]))
    assert result['valid_ids'] == 1 and result['samples'] == []
    client.send_message.assert_not_called()
    client.send_message.return_value = NS(id=123, entities=[types.MessageEntityCustomEmoji(0, 2, A)])
    result = run(smoke.smoke(client, ids=[A], send_samples=True))
    assert result['samples'][0]['entity_retained'] is True
    assert client.send_message.call_args.args[0] == 'me'
    assert 'NOT_PERFORMED' in result['visual_approval']
    client.send_message.reset_mock()
    client.send_message.side_effect = TimeoutError()
    result = run(smoke.smoke(client, ids=[A], send_samples=True))
    assert result['samples'][0]['error_type'] == 'TimeoutError'
    assert client.send_message.await_count == 1


def test_real_telethon_single_media_and_album_preserve_caption_entities(monkeypatch):
    async def main():
        cat = catalogue(record('🔥', A), record('❤️', B))
        monkeypatch.setattr(mod, '_CATALOGUE', cat)
        client = TelegramClient(MemorySession(), 12345, 'test-only')
        mod.install_premium_emoji_injector(client, premium=True)
        peer = types.InputPeerUser(77, 1)
        requests = []
        now = datetime.now(timezone.utc)
        async def transport(sender, request, **kwargs):
            requests.append(request)
            items = request.multi_media if isinstance(request, functions.messages.SendMultiMediaRequest) else [request]
            updates = []
            for index, item in enumerate(items, 1):
                message = types.Message(index, peer_id=types.PeerUser(77), message=item.message,
                                        entities=item.entities, date=now)
                updates.extend([types.UpdateMessageID(index, item.random_id), types.UpdateNewMessage(message, index, 1)])
            return types.Updates(updates, [], [], now, 1)
        client._call = transport
        media = types.InputMediaPhoto(types.InputPhoto(1, 1, b'ref'))
        await client.send_message(peer, '**🔥**', file=media)
        await client.send_file(peer, [media, media], caption=['**🔥**', '<i>❤️</i>'], parse_mode='html')
        assert isinstance(requests[0], functions.messages.SendMediaRequest)
        assert requests[0].message == '🔥'
        assert extracted(requests[0].message, requests[0].entities)[0]['document_id'] == A
        album = next(r for r in requests if isinstance(r, functions.messages.SendMultiMediaRequest))
        assert [item.message for item in album.multi_media] == ['**🔥**', '❤️']
        assert extracted(album.multi_media[0].message, album.multi_media[0].entities)[0]['emoji_text'] == '🔥'
        assert any(isinstance(e, types.MessageEntityItalic) for e in album.multi_media[1].entities)
    run(main())


def test_generator_album_entity_error_does_not_replay():
    class Client(FakeClient):
        async def send_file(self, entity, file, *, caption=None, parse_mode=(), formatting_entities=None):
            self.sent.append(caption)
            raise errors.EntityBoundsInvalidError(request=None)
    client = Client()
    injector = mod.install_premium_emoji_injector(client, premium=True)
    injector.catalogue = catalogue(record('🔥', A))
    with pytest.raises(errors.EntityBoundsInvalidError):
        run(client.send_file(1, (x for x in ['a.jpg', 'b.jpg']), caption='🔥'))
    assert len(client.sent) == 1



def test_arrows_and_uppercase_raw_urls():
    injector = engine(record('↔️', A), record('🔥', B))
    text, entities = run(injector.inject('↔️'))
    assert text == '↔️' and len(entities) == 1
    assert run(injector.inject('HTTPS://example.org/🔥')) == ('HTTPS://example.org/🔥', [])
