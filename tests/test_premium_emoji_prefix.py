"""Offline acceptance tests with real Telethon public methods and TL entities.

Only the network boundary is simulated. No login, exported session, or live
Telegram send is used or claimed by these tests.
"""
import asyncio
import copy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from telethon import TelegramClient, errors, events, functions, types
from telethon.sessions import MemorySession

import config
from services import custom_emoji_service as custom
from services import premium_emoji_prefix as mod

DOC = mod.FALLBACK_DOCUMENT_ID
PEER = types.InputPeerUser(123, 456)
NOW = datetime(2026, 9, 16, tzinfo=timezone.utc)
MEDIA = types.InputMediaPhoto(types.InputPhoto(100, 200, b'reference'))


def run(coro):
    return asyncio.run(coro)


class OfflineClient(TelegramClient):
    def __init__(self, *, bot=False, install=True, premium=True):
        super().__init__(MemorySession(), 12345, 'offline-test-only')
        self._mb_entity_cache.extend([types.User(123, access_hash=456)], [])
        self.calls = []
        self.committed = []
        self.fail = None
        self.account = NS(bot=bot, premium=premium)
        if install:
            self.engine = mod.install_premium_prefix(self, account=self.account)

    async def _call(self, sender, request, ordered=False, flood_sleep_threshold=None):
        # Exercise real TL serialization, not a made-up formatting payload.
        bytes(request)
        self.calls.append(copy.deepcopy(request))
        if self.fail:
            exc = self.fail(request, len(self.calls))
            if exc:
                raise exc
        if isinstance(request, functions.messages.ForwardMessagesRequest):
            self.committed.append(request)
            return types.Updates([], [], [], NOW, 1)
        if isinstance(request, functions.messages.SendMessageRequest):
            self.committed.append(request)
            return types.UpdateShortSentMessage(len(self.committed), 1, 1, NOW,
                                                out=True, entities=request.entities)
        if isinstance(request, functions.messages.SendMultiMediaRequest):
            bodies = request.multi_media
        elif isinstance(request, (functions.messages.SendMediaRequest, functions.messages.EditMessageRequest)):
            bodies = [request]
        else:
            raise AssertionError('Unexpected network request: ' + type(request).__name__)
        updates = []
        for body in bodies:
            self.committed.append(body)
            mid = request.id if isinstance(request, functions.messages.EditMessageRequest) else len(self.committed)
            message = types.Message(mid, types.PeerUser(123), date=NOW, out=True,
                                    message=body.message, entities=body.entities)
            if isinstance(request, functions.messages.EditMessageRequest):
                updates.append(types.UpdateEditMessage(message, 1, 1))
            else:
                updates.extend([types.UpdateMessageID(mid, body.random_id),
                                types.UpdateNewMessage(message, 1, 1)])
        return types.Updates(updates, [], [], NOW, 1)


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_ENABLED', True)
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_PREFIX_ENABLED', True)
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_PREFIX_IDS', [DOC])


def assert_prefix(message, original):
    assert message.message == '✨ ' + original
    entity = message.entities[0]
    assert isinstance(entity, types.MessageEntityCustomEmoji)
    assert (entity.offset, entity.length, entity.document_id) == (0, 1, DOC)


def test_self_send_message_gets_prefix_and_returned_message_matches():
    async def scenario():
        client = OfflineClient()
        sent = await client.send_message(PEER, 'یک document_id عددی معتبر وارد کنید.', parse_mode=None)
        assert_prefix(sent, 'یک document_id عددی معتبر وارد کنید.')
        assert_prefix(client.calls[0], 'یک document_id عددی معتبر وارد کنید.')
        assert len(client.committed) == 1
    run(scenario())


@pytest.mark.parametrize('install', [False, True])
def test_bot_send_untouched_even_if_install_attempted(install):
    async def scenario():
        client = OfflineClient(bot=True, install=install)
        original = 'یک document_id عددی معتبر وارد کنید.'
        sent = await client.send_message(PEER, original)
        assert sent.message == original and not sent.entities
        assert not hasattr(client, '_premium_emoji_prefix')
    run(scenario())


@pytest.mark.parametrize('name', ['PREMIUM_EMOJI_ENABLED', 'PREMIUM_EMOJI_PREFIX_ENABLED'])
def test_disabled_flag_preserves_text(name, monkeypatch):
    monkeypatch.setattr(config, name, False)
    async def scenario():
        sent = await OfflineClient().send_message(PEER, 'سلام 😂🔥')
        assert sent.message == 'سلام 😂🔥' and not sent.entities
    run(scenario())


@pytest.mark.parametrize('entity', [
    types.MessageEntityBold(3, 4), types.MessageEntityItalic(3, 4),
    types.MessageEntityUnderline(3, 4), types.MessageEntityStrike(3, 4),
    types.MessageEntitySpoiler(3, 4), types.MessageEntityTextUrl(3, 4, 'https://example.com'),
    types.MessageEntityUrl(3, 4), types.MessageEntityCode(3, 4),
    types.MessageEntityPre(3, 4, 'python'), types.MessageEntityCustomEmoji(3, 2, DOC + 1),
])
def test_all_formatting_shifted_utf16_without_mutating_input(entity):
    original = copy.deepcopy(entity.to_dict())
    text, entities = mod.PremiumEmojiPrefix().inject('😀 سلام 😂🔥', [entity])
    assert text == '✨ 😀 سلام 😂🔥'
    assert entities[1].offset == 5 and entities[1].length == entity.length
    assert entities[1].to_dict() == {**original, 'offset': 5}
    assert entity.to_dict() == original
    assert custom.utf16_length('✨ ') == 2


def test_astral_placeholder_uses_utf16(monkeypatch):
    monkeypatch.setattr(mod, 'PLACEHOLDER', '💎')
    engine = mod.PremiumEmojiPrefix()
    text, entities = engine.inject('😀 bold', [types.MessageEntityBold(3, 4)])
    assert text == '💎 😀 bold'
    assert entities[0].length == 2 and entities[1].offset == 6
    assert engine.inject(text, entities) == (text, entities)


def test_existing_unicode_emoji_is_not_replaced_and_known_id_accepted():
    engine = mod.PremiumEmojiPrefix()
    text, entities = engine.inject('سلام 😂🔥')
    assert text == '✨ سلام 😂🔥'
    assert len(entities) == 1 and entities[0].document_id == DOC
    assert custom.parse_document_id(DOC) == DOC


def test_entity_builder_is_reused(monkeypatch):
    calls = []
    original = custom.create_custom_emoji
    def build(text, document_id):
        calls.append((text, document_id))
        return original(text, document_id)
    monkeypatch.setattr(custom, 'create_custom_emoji', build)
    mod.PremiumEmojiPrefix().inject('سلام')
    assert calls == [('✨', DOC)]


@pytest.mark.parametrize('mode,text,expected', [
    ('md', '**bold** 😀', 'bold 😀'), ('html', '<b>bold</b> 😀', 'bold 😀'),
    (None, '**literal** 😀', '**literal** 😀'),
])
def test_real_telethon_parse_modes(mode, text, expected):
    async def scenario():
        sent = await OfflineClient().send_message(PEER, text, parse_mode=mode)
        assert_prefix(sent, expected)
        if mode:
            assert isinstance(sent.entities[1], types.MessageEntityBold)
            assert (sent.entities[1].offset, sent.entities[1].length) == (2, 4)
    run(scenario())


@pytest.mark.parametrize('via_send_message', [False, True])
def test_caption_and_nested_send_get_only_one_prefix(via_send_message):
    async def scenario():
        client = OfflineClient()
        if via_send_message:
            sent = await client.send_message(PEER, 'عکس 😂', file=MEDIA, parse_mode=None)
        else:
            sent = await client.send_file(PEER, MEDIA, caption='عکس 😂', parse_mode=None)
        assert_prefix(sent, 'عکس 😂')
        assert len(client.calls) == 1 and len(client.committed) == 1
    run(scenario())


@pytest.mark.parametrize('caption', ['', None])
def test_empty_caption_untouched(caption):
    async def scenario():
        client = OfflineClient()
        await client.send_file(PEER, MEDIA, caption=caption)
        assert client.calls[0].message == '' and not client.calls[0].entities
        assert len(client.committed) == 1
    run(scenario())


def test_already_prefixed_message_is_idempotent():
    engine = mod.PremiumEmojiPrefix(ids=[DOC, DOC + 1])
    text, entities = engine.inject('سلام')
    assert engine.inject(text, entities) == (text, entities)
    assert engine.index == 1
    assert engine.inject('✨ سلام', [])[0] == '✨ ✨ سلام'


@pytest.mark.parametrize('overload', ['id', 'message', 'convenience'])
def test_edit_single_prefix_with_real_telethon(overload):
    async def scenario():
        client = OfflineClient()
        original = await client.send_message(PEER, '**hello**')
        if overload == 'id':
            edited = await client.edit_message(PEER, original.id, original.message,
                                               formatting_entities=original.entities)
        elif overload == 'message':
            edited = await client.edit_message(original, original.message,
                                               formatting_entities=original.entities)
        else:
            edited = await original.edit(original.message, formatting_entities=original.entities)
        assert_prefix(edited, 'hello')
        assert len([e for e in edited.entities if isinstance(e, types.MessageEntityCustomEmoji)]) == 1
        assert original.entities[1].offset == 2
    run(scenario())


def test_edit_with_new_text_gets_prefix():
    async def scenario():
        client = OfflineClient()
        edited = await client.edit_message(PEER, 17, 'متن جدید')
        assert_prefix(edited, 'متن جدید')
    run(scenario())


@pytest.mark.parametrize('method', ['reply', 'respond', 'event_reply', 'event_respond'])
def test_message_and_event_convenience_paths(method):
    async def scenario():
        client = OfflineClient()
        message = types.Message(11, types.PeerUser(123), date=NOW, message='input', out=True)
        message._finish_init(client, {}, PEER)
        if method.startswith('event_'):
            target = events.NewMessage.Event(message)
            target._set_client(client)
            method_name = method.removeprefix('event_')
        else:
            target, method_name = message, method
        sent = await getattr(target, method_name)('پاسخ')
        assert_prefix(sent, 'پاسخ')
    run(scenario())


def test_message_copy_preserves_original_and_entities():
    async def scenario():
        client = OfflineClient()
        source = types.Message(42, types.PeerUser(123), message='سلام 😂',
                               entities=[types.MessageEntityBold(0, 4)])
        sent = await client.send_message(PEER, source)
        assert_prefix(sent, 'سلام 😂')
        assert source.message == 'سلام 😂' and source.entities[0].offset == 0
    run(scenario())


def test_forward_is_untouched():
    async def scenario():
        client = OfflineClient()
        before = client.forward_messages
        await client.forward_messages(PEER, [17], from_peer=PEER)
        assert client.forward_messages == before
        assert isinstance(client.calls[0], functions.messages.ForwardMessagesRequest)
        assert client.calls[0].id == [17] and client.engine.index == 0
    run(scenario())


@pytest.mark.parametrize('error', [errors.PremiumAccountRequiredError,
                                  errors.EmoticonInvalidError])
@pytest.mark.parametrize('operation', ['text', 'caption', 'nested_caption', 'edit'])
def test_premium_rejection_falls_back_once_without_duplicate(error, operation):
    async def scenario():
        client = OfflineClient(premium=False)
        client.fail = lambda request, n: error(request) if n == 1 else None
        if operation == 'caption':
            sent = await client.send_file(PEER, MEDIA, caption='**متن**')
        elif operation == 'nested_caption':
            sent = await client.send_message(PEER, '**متن**', file=MEDIA)
        elif operation == 'edit':
            sent = await client.edit_message(PEER, 14, '**متن**')
        else:
            sent = await client.send_message(PEER, '**متن**')
        assert sent.message == 'متن'
        assert len(client.calls) == 2 and len(client.committed) == 1
        assert isinstance(sent.entities[0], types.MessageEntityBold)
        assert sent.entities[0].offset == 0
        assert not any(isinstance(e, types.MessageEntityCustomEmoji) for e in sent.entities)
        assert client.engine.premium is False
    run(scenario())


def test_already_prefixed_edit_fallback_removes_only_our_prefix():
    async def scenario():
        client = OfflineClient()
        text, entities = client.engine.inject('💎 متن', [types.MessageEntityCustomEmoji(0, 2, DOC + 99)])
        client.fail = lambda r, n: errors.EmoticonInvalidError(r) if n == 1 else None
        sent = await client.edit_message(PEER, 17, text, formatting_entities=entities)
        assert sent.message == '💎 متن' and len(sent.entities) == 1
        assert sent.entities[0].document_id == DOC + 99 and sent.entities[0].offset == 0
    run(scenario())


@pytest.mark.parametrize('error', [TimeoutError, errors.DocumentInvalidError,
                                  errors.EntityBoundsInvalidError, errors.FloodWaitError])
def test_unrelated_error_never_retried(error):
    async def scenario():
        client = OfflineClient()
        client.fail = lambda r, n: TimeoutError() if error is TimeoutError else error(r)
        with pytest.raises(error):
            await client.send_message(PEER, 'سلام')
        assert len(client.calls) == 1 and not client.committed
    run(scenario())


def test_fallback_failure_is_not_retried_again():
    async def scenario():
        client = OfflineClient()
        client.fail = lambda r, n: errors.EmoticonInvalidError(r)
        with pytest.raises(errors.EmoticonInvalidError):
            await client.send_message(PEER, 'سلام')
        assert len(client.calls) == 2 and not client.committed
    run(scenario())


def test_round_robin_deduplicates_and_caps_pool():
    # Synthetic IDs test scheduling only; these are never configured in runtime.
    pool = [DOC, DOC + 1, DOC + 2]
    engine = mod.PremiumEmojiPrefix(ids=[*pool, DOC])
    assert [engine.inject('text')[1][0].document_id for _ in range(7)] == pool * 2 + [DOC]
    assert len(mod.PremiumEmojiPrefix(ids=list(range(1, 30))).ids) == 15


@pytest.mark.parametrize('ids', [[], [0, -1, 'bad', True, 2**63], None])
def test_known_id_is_fallback(ids):
    assert mod.PremiumEmojiPrefix(ids=ids).inject('سلام')[1][0].document_id == DOC


def test_install_is_per_client_and_idempotent_and_can_uninstall():
    async def scenario():
        client, other = OfflineClient(), OfflineClient(install=False)
        wrapped = client.send_message
        assert mod.install_premium_prefix(client, account=client.account) is client.engine
        assert client.send_message is wrapped
        assert (await other.send_message(PEER, 'other')).message == 'other'
        mod.uninstall_premium_prefix(client)
        assert (await client.send_message(PEER, 'plain')).message == 'plain'
    run(scenario())


def test_album_captions_formatting_and_empty_caption():
    async def scenario():
        client = OfflineClient()
        sent = await client.send_file(PEER, [MEDIA] * 3, caption=['**اول**', '', '😀 سوم'])
        assert_prefix(sent[0], 'اول')
        assert sent[0].entities[1].offset == 2
        assert sent[1].message == '' and not sent[1].entities
        assert_prefix(sent[2], '😀 سوم')
        assert len(client.calls) == 1 and len(client.committed) == 3
    run(scenario())


def test_later_album_rejection_does_not_replay_committed_album():
    async def scenario():
        client = OfflineClient()
        client.fail = lambda r, n: errors.EmoticonInvalidError(r) if n == 2 else None
        sent = await client.send_file(PEER, [MEDIA] * 12, caption=[f'item {i}' for i in range(12)])
        assert len(sent) == 12 and len(client.committed) == 12 and len(client.calls) == 3
        assert [len(r.multi_media) for r in client.calls] == [10, 2, 2]
        assert_prefix(sent[0], 'item 0')
        assert sent[10].message == 'item 10' and not sent[10].entities
    run(scenario())


def test_existing_custom_emoji_test_still_sends_requested_entity():
    async def scenario():
        client = OfflineClient()
        payload = custom.create_custom_emoji('💎', DOC)
        sent = await custom.send_custom_emoji(payload, client=client, peer=PEER)
        assert custom.contains_custom_emoji(sent, DOC)
        assert sent.message == '✨ 💎'
        assert len(sent.entities) == 2 and sent.entities[1].length == 2
    run(scenario())


def test_runtime_installs_only_prefix_and_bot_modules_are_not_installers():
    root = Path(__file__).resolve().parents[1]
    source = (root / 'self.py').read_text()
    assert 'install_premium_prefix(client, account=me)' in source
    assert 'install_premium_emoji_injector' not in source
    for name in ('bot/core.py', 'inline.py', 'handlers/premium_emoji.py'):
        assert 'install_premium_prefix' not in (root / name).read_text()
    assert 'premium_emoji_injector' not in (root / 'services/premium_emoji_prefix.py').read_text()
