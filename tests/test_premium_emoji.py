"""Real main-bot routing, owner isolation, UTF-16, migration and rendering."""
import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from telethon import errors
from telethon.tl.types import (
    Message, MessageEntityCustomEmoji, MessageEntityBold, DocumentAttributeCustomEmoji,
    InputStickerSetEmpty, PeerUser,
)
from database import models
from handlers.premium_emoji import PremiumEmojiController, MENU_TEXT, PROMPT
from services import emoji_extractor as extractor
from test_security_runtime import Event, run
import bot.core as core

DOC1 = 5354913105224675749
DOC2 = 5389006280742418787


def message(text='💎🔥', entities=None, **kwargs):
    if entities is None:
        entities = [MessageEntityCustomEmoji(0, 2, DOC1), MessageEntityCustomEmoji(2, 2, DOC2)]
    return Message(id=77, peer_id=PeerUser(101), message=text, entities=entities, **kwargs)


def records():
    return extractor.extract_custom_emojis(message())


def callback_data(button):
    return getattr(button, 'data', getattr(getattr(button, 'type', None), 'data', b'')).decode()


def saved_button(event):
    buttons = event.reply.call_args.kwargs['buttons']
    return callback_data(buttons[0][0])


def event(bot=None, uid=101, text='', msg=None, data=b''):
    result = Event(bot, uid, text, data)
    result.message = msg
    return result


def fake_document(doc_id=DOC1, alt='💎'):
    return NS(id=doc_id, attributes=[DocumentAttributeCustomEmoji(alt, InputStickerSetEmpty())])


def test_extract_utf16_after_astral_character_and_zwj():
    text = '😀 سلام 👩🏽\u200d💻 و 💎'
    prefix = '😀 سلام '
    first = '👩🏽\u200d💻'
    entities = [
        MessageEntityCustomEmoji(extractor.utf16_length(text[:-1]), 2, DOC2),
        MessageEntityBold(0, 2),
        MessageEntityCustomEmoji(extractor.utf16_length(prefix), extractor.utf16_length(first), DOC1),
    ]
    found = extractor.extract_custom_emojis(message(text, entities))
    assert [(r['document_id'], r['emoji_text']) for r in found] == [(DOC1, first), (DOC2, '💎')]


def test_repeated_entities_preserved_in_output_but_saved_once():
    items = extractor.extract_custom_emojis(message('💎💎', [
        MessageEntityCustomEmoji(0, 2, DOC1), MessageEntityCustomEmoji(2, 2, DOC1)]))
    assert len(items) == 2
    assert str(DOC1) in extractor.result_pages(items)[0]
    assert models.save_custom_emojis(101, items, 77) == 1
    assert models.save_custom_emojis(101, items, 77) == 0
    assert models.custom_emoji_page(101)[1] == 1


@pytest.mark.parametrize('entities', [None, [], [MessageEntityBold(0, 2)]])
def test_ordinary_emojis_are_not_custom(entities):
    assert extractor.extract_custom_emojis(NS(message='💎', entities=entities)) == []


@pytest.mark.parametrize('offset,length,doc_id', [(-1, 2, DOC1), (0, 0, DOC1),
    (0, 5, DOC1), (1, 1, DOC1), (0, 1, DOC1), (0, 2, 0), (0, 2, 2**63)])
def test_malformed_entities_do_not_break_extraction(offset, length, doc_id):
    assert extractor.extract_custom_emojis(message('💎', [MessageEntityCustomEmoji(offset, length, doc_id)])) == []


@pytest.mark.parametrize('bad', ['1.5', '-1', '0', 'x', '9' * 400, True, 2**63, None])
def test_invalid_test_ids_rejected(bad):
    with pytest.raises(extractor.EmojiError):
        extractor.parse_document_id(bad)


def test_persian_numeric_id_is_exact_integer():
    assert extractor.parse_document_id('۵۳۵۴۹۱۳۱۰۵۲۲۴۶۷۵۷۴۹') == DOC1


def test_model_owner_isolation_and_persistence():
    models.save_custom_emojis(101, records(), 77)
    models.save_custom_emojis(202, records()[:1], 88)
    rows, total, page = models.custom_emoji_page(101)
    assert (total, page) == (2, 0)
    assert rows[0]['source_message_id'] == 77 and rows[0]['created_at']
    assert not models.delete_custom_emoji(rows[0]['id'], 202)
    assert models.delete_custom_emoji(rows[0]['id'], 101)
    models.init_custom_emojis_db()
    assert models.custom_emoji_page(101)[1] == 1
    assert models.custom_emoji_page(202)[1] == 1
    assert models.custom_emoji_page(999)[1] == 0


def test_migration_preserves_existing_data_and_has_required_fields():
    models.init_tabchi_db()
    models.add_whitelist_entries(101, [{'peer_id': 202, 'kind': 'private', 'title': 'User'}])
    models.init_custom_emojis_db()
    models.init_custom_emojis_db()
    with sqlite3.connect(models.TABCHI_DB_PATH) as conn:
        columns = {row[1] for row in conn.execute('PRAGMA table_info(custom_emojis)')}
    assert {'id', 'owner_id', 'document_id', 'emoji_text', 'source_message_id', 'created_at'} <= columns
    assert models.whitelist_policy(101) == (True, {202})


def test_atomic_save_rolls_back_whole_batch():
    models.init_custom_emojis_db()
    with sqlite3.connect(models.TABCHI_DB_PATH) as conn:
        conn.execute(f"""CREATE TRIGGER fail_custom BEFORE INSERT ON custom_emojis
            WHEN NEW.document_id={DOC2} BEGIN SELECT RAISE(ABORT,'test'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        models.save_custom_emojis(101, records(), 77)
    assert models.custom_emoji_page(101)[1] == 0


def test_simultaneous_saves_do_not_duplicate():
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(lambda _: models.save_custom_emojis(101, records(), 77), range(10)))
    assert sum(results) == 2
    assert models.custom_emoji_page(101)[1] == 2


def test_long_extraction_chunks_and_database_pagination():
    items = [{'document_id': DOC1 + i, 'emoji_text': '💎'} for i in range(201)]
    pages = extractor.result_pages(items)
    assert len(pages) == 3
    assert all(extractor.utf16_length(page) < 4096 for page in pages)
    for item in items:
        assert sum(str(item['document_id']) in page for page in pages) == 1
    models.save_custom_emojis(101, items, 77)
    seen = set()
    for page in range(11):
        rows, total, actual_page = models.custom_emoji_page(101, page)
        assert total == 201 and actual_page == page
        seen.update(row['document_id'] for row in rows)
    assert len(seen) == 201
    assert models.custom_emoji_page(101, 999)[2] == 10


@pytest.mark.parametrize('kind', ['text', 'photo', 'video', 'document'])
def test_real_main_menu_extracts_text_and_media_captions(monkeypatch, kind):
    async def scenario(bot):
        cb, msg = bot.handlers['cb_handler'], bot.handlers['msg_handler']
        _, buttons = core.render_main_menu(101)
        assert 'em_menu' in [callback_data(b) for row in buttons for b in row]
        opening = event(bot, data=b'em_menu')
        await cb(opening)
        assert opening.edit.call_args.args[0] == MENU_TEXT
        await cb(event(bot, data=b'em_extract'))
        # Real Telethon messages use the same entities for text and captions.
        payload = message()
        if kind != 'text':
            payload.media = NS(kind=kind)
        incoming = event(bot, text='💎🔥', msg=payload)
        await msg(incoming)
        output = incoming.reply.call_args.args[0]
        assert str(DOC1) in output and str(DOC2) in output
        assert models.custom_emoji_page(101)[1] == 0
        save = saved_button(incoming)
        await cb(event(bot, data=save.encode()))
        assert models.custom_emoji_page(101)[1] == 2
        await cb(event(bot, data=save.encode()))
        assert models.custom_emoji_page(101)[1] == 2
    run(monkeypatch, scenario)


def test_real_routing_accepts_leading_slash_text_and_no_custom_emoji(monkeypatch):
    async def scenario(bot):
        cb, msg = bot.handlers['cb_handler'], bot.handlers['msg_handler']
        await cb(event(bot, data=b'em_extract'))
        incoming = event(bot, text='/hello 💎', msg=message('/hello 💎', [MessageEntityCustomEmoji(7, 2, DOC1)]))
        await msg(incoming)
        assert str(DOC1) in incoming.reply.call_args.args[0]
        regular = event(bot, text='🔥', msg=NS(id=78, message='🔥', entities=None))
        await msg(regular)
        assert regular.reply.call_args.args[0] == '❌ در این پیام Custom Emoji پیدا نشد.'
    run(monkeypatch, scenario)


def test_real_menu_exit_discards_pending_save_and_does_not_intercept_wallet(monkeypatch):
    async def scenario(bot):
        cb, msg = bot.handlers['cb_handler'], bot.handlers['msg_handler']
        await cb(event(bot, data=b'em_extract'))
        incoming = event(bot, text='💎🔥', msg=message())
        await msg(incoming)
        save = saved_button(incoming)
        await cb(event(bot, data=b'wallet_menu'))
        ignored = event(bot, text='💎🔥', msg=message())
        await msg(ignored)
        assert not any('اطلاعات ایموجی' in str(c.args) for c in ignored.reply.call_args_list)
        stale = event(bot, data=save.encode())
        await cb(stale)
        assert 'منقضی' in stale.answer.call_args.args[0]
        assert models.custom_emoji_page(101)[1] == 0
    run(monkeypatch, scenario)


def test_stolen_save_and_delete_callbacks_cannot_access_another_owner():
    controller = PremiumEmojiController(NS())
    async def scenario():
        await controller.handle_callback(event(), 'em_extract')
        incoming = event(text='💎🔥', msg=message())
        await controller.handle_message(incoming)
        save = saved_button(incoming)
        await controller.handle_callback(event(uid=202), save)
        assert models.custom_emoji_page(202)[1] == 0
        await controller.handle_callback(event(), save)
        row = models.custom_emoji_page(101)[0][0]
        await controller.handle_callback(event(uid=202), f"em_remove_{row['id']}_0")
        assert models.custom_emoji_page(101)[1] == 2
        foreign = event(uid=202)
        await controller.handle_callback(foreign, 'em_list_0')
        assert str(DOC1) not in foreign.edit.call_args.args[0]
    asyncio.run(scenario())


def test_old_extraction_save_does_not_save_new_result():
    controller = PremiumEmojiController(NS())
    async def scenario():
        await controller.handle_callback(event(), 'em_extract')
        first = event(msg=message())
        await controller.handle_message(first)
        old_save = saved_button(first)
        second = event(msg=message('💎', [MessageEntityCustomEmoji(0, 2, DOC1)]))
        await controller.handle_message(second)
        await controller.handle_callback(event(), old_save)
        assert models.custom_emoji_page(101)[1] == 0
        await controller.handle_callback(event(), saved_button(second))
        assert models.custom_emoji_page(101)[1] == 1
    asyncio.run(scenario())


def test_test_message_builds_verified_alt_and_correct_utf16_entity():
    bot = AsyncMock(return_value=[fake_document(alt='👩🏽\u200d💻')])
    text, entities = asyncio.run(extractor.build_test_message(bot, DOC1))
    entity = entities[0]
    assert isinstance(entity, MessageEntityCustomEmoji)
    assert entity.document_id == DOC1
    encoded = text.encode('utf-16-le')
    assert encoded[entity.offset * 2:(entity.offset + entity.length) * 2].decode('utf-16-le') == '👩🏽\u200d💻'
    assert bot.call_args.args[0].document_id == [DOC1]


@pytest.mark.parametrize('documents', [[], [NS(id=DOC1, attributes=[])], [fake_document(DOC2)]])
def test_invalid_telegram_document_never_becomes_test_entity(documents):
    with pytest.raises(extractor.EmojiError):
        asyncio.run(extractor.build_test_message(AsyncMock(return_value=documents), DOC1))


@pytest.mark.parametrize('stripped', [False, True])
def test_controller_sends_test_and_detects_stripped_entity(stripped):
    bot = AsyncMock(return_value=[fake_document()])
    entities = [] if stripped else [MessageEntityCustomEmoji(0, 2, DOC1)]
    bot.send_message = AsyncMock(return_value=NS(entities=entities))
    controller = PremiumEmojiController(bot)
    async def scenario():
        await controller.handle_callback(event(), 'em_test')
        incoming = event(text=str(DOC1))
        await controller.handle_message(incoming)
        bot.send_message.assert_awaited_once()
        call = bot.send_message.call_args
        assert call.args[0] == 101 and call.kwargs['parse_mode'] is None
        assert call.kwargs['formatting_entities'][0].document_id == DOC1
        if stripped:
            assert 'تأیید نشد' in incoming.reply.call_args.args[0]
        else:
            incoming.reply.assert_not_awaited()
        assert models.custom_emoji_page(101)[1] == 0
    asyncio.run(scenario())


def test_cancel_while_telegram_resolves_prevents_test_send():
    bot = AsyncMock()
    controller = PremiumEmojiController(bot)
    async def resolve(_):
        controller.cancel(101)
        return [fake_document()]
    bot.side_effect = resolve
    bot.send_message = AsyncMock()
    async def scenario():
        await controller.handle_callback(event(), 'em_test')
        await controller.handle_message(event(text=str(DOC1)))
        bot.send_message.assert_not_awaited()
    asyncio.run(scenario())


def test_flood_wait_is_reported_without_retry():
    bot = AsyncMock(side_effect=errors.FloodWaitError(request=None, capture=40793))
    bot.send_message = AsyncMock()
    controller = PremiumEmojiController(bot)
    async def scenario():
        await controller.handle_callback(event(), 'em_test')
        incoming = event(text=str(DOC1))
        await controller.handle_message(incoming)
        assert '40793' in incoming.reply.call_args.args[0]
        bot.assert_awaited_once()
        bot.send_message.assert_not_awaited()
    asyncio.run(scenario())


def test_feature_is_blocked_during_login_and_group_callbacks(monkeypatch):
    import login_manager as lm
    monkeypatch.setattr(lm, 'login_states', {})
    monkeypatch.setattr(core, 'login_states', lm.login_states)
    async def scenario(bot):
        cb = bot.handlers['cb_handler']
        await cb(event(bot, data=b'install_self'))
        opening = event(bot, data=b'em_menu')
        await cb(opening)
        assert 'ورود' in opening.answer.call_args.args[0]
        assert not opening.edit.called
        group = event(bot, data=b'em_menu')
        group.chat_id = -100123
        await cb(group)
        assert not group.edit.called
    run(monkeypatch, scenario)


def test_real_runtime_test_route_and_startup_migration(monkeypatch):
    from test_security_runtime import Bot
    request = AsyncMock(return_value=[fake_document()])
    async def api(self, query):
        return await request(query)
    monkeypatch.setattr(Bot, '__call__', api, raising=False)
    async def scenario(bot):
        with sqlite3.connect(models.TABCHI_DB_PATH) as conn:
            assert conn.execute("SELECT name FROM sqlite_master WHERE name='custom_emojis'").fetchone()
        bot.send_message.return_value = NS(entities=[MessageEntityCustomEmoji(0, 2, DOC1)])
        cb, msg = bot.handlers['cb_handler'], bot.handlers['msg_handler']
        await cb(event(bot, data=b'em_test'))
        await msg(event(bot, text=str(DOC1)))
        assert request.call_args.args[0].document_id == [DOC1]
        assert bot.send_message.call_args.kwargs['formatting_entities'][0].document_id == DOC1
    run(monkeypatch, scenario)


def test_expired_extraction_cannot_be_saved():
    controller = PremiumEmojiController(NS())
    async def scenario():
        await controller.handle_callback(event(), 'em_extract')
        incoming = event(msg=message())
        await controller.handle_message(incoming)
        controller.states[101]['pending']['expires'] = 0
        await controller.handle_callback(event(), saved_button(incoming))
        assert models.custom_emoji_page(101)[1] == 0
    asyncio.run(scenario())


def test_cancel_and_database_save_failure_preserve_expected_state(monkeypatch):
    controller = PremiumEmojiController(NS())
    real_save = models.save_custom_emojis
    def fail(*args):
        raise sqlite3.OperationalError('no disk space')
    async def scenario():
        await controller.handle_callback(event(), 'em_extract')
        incoming = event(msg=message())
        await controller.handle_message(incoming)
        save = saved_button(incoming)
        monkeypatch.setattr(models, 'save_custom_emojis', fail)
        failed = event()
        await controller.handle_callback(failed, save)
        assert 'انجام نشد' in failed.answer.call_args.args[0]
        assert controller.states[101]['pending']
        monkeypatch.setattr(models, 'save_custom_emojis', real_save)
        await controller.handle_callback(event(), save)
        assert models.custom_emoji_page(101)[1] == 2
        await controller.handle_message(event(text='لغو'))
        assert not controller.has_state(101)
    asyncio.run(scenario())


def test_admin_panel_command_is_not_consumed_as_emoji_input(monkeypatch):
    monkeypatch.setattr(core.admin_manager, 'is_admin', lambda *args, **kwargs: True)
    async def scenario(bot):
        cb, msg = bot.handlers['cb_handler'], bot.handlers['msg_handler']
        await cb(event(bot, data=b'em_extract'))
        command = event(bot, text='/panel', msg=message('/panel', []))
        await msg(command)
        command.reply.assert_not_awaited()
        await bot.handlers['admin_panel'](command)
        incoming = event(bot, text='💎🔥', msg=message())
        await msg(incoming)
        assert not any('اطلاعات ایموجی' in str(c.args) for c in incoming.reply.call_args_list)
    run(monkeypatch, scenario)


def test_shared_service_create_and_send_real_entity():
    from services.custom_emoji_service import create_custom_emoji, send_custom_emoji
    payload = create_custom_emoji('🙂', DOC1)
    assert isinstance(payload.entities[0], MessageEntityCustomEmoji)
    assert payload.entities[0].length == extractor.utf16_length('🙂') == 2
    client = NS(send_message=AsyncMock(return_value=NS(
        entities=[MessageEntityCustomEmoji(0, 2, DOC1)]
    )))

    async def scenario():
        sent = await send_custom_emoji(payload, client=client, peer=101)
        assert sent.entities[0].document_id == DOC1
        kwargs = client.send_message.call_args.kwargs
        assert kwargs['parse_mode'] is None
        assert isinstance(kwargs['formatting_entities'][0], MessageEntityCustomEmoji)
        assert kwargs['formatting_entities'][0].document_id == DOC1

    asyncio.run(scenario())


def test_self_flow_state_is_persistent_and_surface_scoped():
    models.set_custom_emoji_flow(101, 'self', 'extract', 500)
    models.set_custom_emoji_flow(101, 'bot', 'test', 600)
    assert models.get_custom_emoji_flow(101, 'self')['step'] == 'extract'
    assert models.get_custom_emoji_flow(101, 'self')['chat_id'] == 500
    assert models.get_custom_emoji_flow(101, 'bot')['step'] == 'test'
    models.clear_custom_emoji_flow(101, 'self')
    assert models.get_custom_emoji_flow(101, 'self') is None
    assert models.get_custom_emoji_flow(101, 'bot')['step'] == 'test'


def test_manager_migration_adds_emoji_field_without_losing_old_rows():
    with sqlite3.connect(models.TABCHI_DB_PATH) as conn:
        conn.execute('''CREATE TABLE custom_emojis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            document_id INTEGER NOT NULL,
            emoji_text TEXT NOT NULL,
            source_message_id INTEGER,
            created_at TEXT NOT NULL,
            UNIQUE(owner_id, document_id)
        )''')
        conn.execute(
            'INSERT INTO custom_emojis(owner_id,document_id,emoji_text,source_message_id,created_at) '
            'VALUES (?,?,?,?,?)',
            (101, DOC1, '💎', 77, '2026-09-12T00:00:00+00:00'),
        )
    models.init_custom_emojis_db()
    with sqlite3.connect(models.TABCHI_DB_PATH) as conn:
        row = conn.execute(
            'SELECT owner_id,document_id,emoji,emoji_text FROM custom_emojis WHERE owner_id=?',
            (101,),
        ).fetchone()
    assert row == (101, DOC1, '💎', '💎')


def test_self_panel_contains_custom_emoji_manager_actions():
    import inline as inline_mod
    _, main_buttons = inline_mod.build_main_menu(101, 'example_bot')
    main_data = [callback_data(button) for row in main_buttons for button in row if getattr(button, 'data', None)]
    assert 'cem_menu' in main_data

    _, emoji_buttons = inline_mod.build_self_emoji_menu()
    emoji_data = [callback_data(button) for row in emoji_buttons for button in row if getattr(button, 'data', None)]
    assert {'cem_extract', 'cem_test', 'cem_list_0'} <= set(emoji_data)


def test_detailed_extract_report_contains_all_requested_fields():
    text = extractor.detailed_result_text(records()[:1])
    assert 'Document ID' in text
    assert 'Offset:' in text
    assert 'Length:' in text
    assert 'Emoji Text:' in text
    assert 'Custom Emoji' in text
    assert 'قابل استفاده' in text
