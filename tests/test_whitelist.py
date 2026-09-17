"""Whitelist policy tests: no live Telegram requests or account data."""
import asyncio
import sqlite3
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from telethon.tl.types import User, Chat, Channel, ChatPhotoEmpty, PeerUser, PeerChannel
from telethon.utils import get_peer_id

from database import models
from handlers.tabchi import TabchiController
from handlers.panel import render_whitelist
from services import sender, whitelist_service as whitelist, scheduler


def group(ident=303, title='Test Group', username='testgroup'):
    return Channel(id=ident, title=title, photo=ChatPhotoEmpty(), date=None,
                   megagroup=True, username=username)


def account(ident=202, **kwargs):
    return User(id=ident, first_name='Test User', username='testuser', **kwargs)


class Client:
    def __init__(self, *entities):
        self.entities = entities
        self.send_message = AsyncMock(return_value=NS(id=42))
        self.forward_messages = AsyncMock(return_value=NS(id=42))
        self.get_messages = AsyncMock(return_value=NS(id=12, media='photo'))

    def is_connected(self):
        return True

    async def iter_dialogs(self):
        for entity in self.entities:
            yield NS(entity=entity)


def entry(entity):
    return {'peer_id': get_peer_id(entity), 'kind': sender._dialog_category(entity),
            'title': sender._target_title(entity)}


def banner(**kwargs):
    return dict(user_id=101, send_target=['group', 'private'], send_mode='normal',
                type='text', text='Banner', **kwargs)


def run(coro):
    return asyncio.run(coro)


def test_policy_owner_isolation_idempotence_and_last_delete():
    assert models.whitelist_policy(101) == (False, set())
    models.add_whitelist_entries(101, [entry(account())])
    models.add_whitelist_entries(101, [entry(account())])
    assert len(models.list_whitelist(101)) == 1
    row = models.list_whitelist(101)[0]
    assert models.whitelist_policy(101) == (True, {202})
    assert models.whitelist_policy(999) == (False, set())
    assert not models.delete_whitelist(row['id'], 999)
    assert models.delete_whitelist(row['id'], 101)
    assert models.whitelist_policy(101) == (True, set())
    models.set_whitelist_enabled(101, False)
    assert models.whitelist_policy(101) == (False, set())


def test_schema_upgrade_preserves_existing_banners():
    models.init_tabchi_db()
    bid = models.create_banner(user_id=101, name='Existing', type='text', file_id=None,
        text='Banner', caption=None, send_mode='normal', send_target=['private'],
        interval=5, status='active', source_peer=None, source_message_id=None)
    with sqlite3.connect(models.TABCHI_DB_PATH) as conn:
        conn.execute('DROP TABLE tabchi_whitelist')
        conn.execute('DROP TABLE tabchi_whitelist_settings')
    models.init_tabchi_db()
    models.init_tabchi_db()
    assert models.get_banner(bid, 101)['text'] == 'Banner'
    assert models.whitelist_policy(101) == (False, set())


def test_failed_batch_rolls_back_destinations_and_enablement():
    models.init_tabchi_db()
    with sqlite3.connect(models.TABCHI_DB_PATH) as conn:
        conn.execute("""CREATE TRIGGER fail_whitelist BEFORE INSERT ON tabchi_whitelist
                        WHEN NEW.peer_id=203 BEGIN SELECT RAISE(ABORT,'test'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        models.add_whitelist_entries(101, [entry(account()), entry(account(203))])
    assert models.whitelist_policy(101) == (False, set())


@pytest.mark.parametrize('bad', [0, True, 2**63, -202, '202'])
def test_invalid_private_destinations_rejected(bad):
    with pytest.raises(ValueError):
        models.add_whitelist_entries(101, [{'peer_id': bad, 'kind': 'private'}])
    assert models.whitelist_policy(101) == (False, set())


@pytest.mark.parametrize('reference', ['202', '۲۰۲', '@TestUser', 'testuser',
    'https://t.me/testuser', 't.me/testuser/42', 'Test User'])
def test_user_reference_formats(reference):
    assert run(whitelist.resolve_entries(Client(account()), reference)) == [entry(account())]


@pytest.mark.parametrize('reference', ['303', '-1000000000303', 'https://t.me/c/303/4',
    '@testgroup', 'https://t.me/s/testgroup/42', 'Test Group'])
def test_group_reference_formats(reference):
    assert run(whitelist.resolve_entries(Client(group()), reference)) == [entry(group())]


def test_batch_dedup_and_unknown_target_rejects_entire_selection():
    client = Client(account(), group())
    result = run(whitelist.resolve_entries(client, '202\n@testuser\n@testgroup'))
    assert {r['peer_id'] for r in result} == {202, get_peer_id(group())}
    with pytest.raises(whitelist.WhitelistError):
        run(whitelist.resolve_entries(client, '202\n@unknown'))


@pytest.mark.parametrize('text', ['t.me/+invite', 'https://elsewhere.test/testuser',
    't.me/joinchat/invite', '@invalid!name', '0', '\n', '1\n' * 21])
def test_invalid_or_unsupported_reference_is_rejected(text):
    with pytest.raises(whitelist.WhitelistError):
        run(whitelist.resolve_entries(Client(account()), text))


def test_raw_numeric_id_collision_and_duplicate_title_require_disambiguation():
    client = Client(account(303), group())
    with pytest.raises(whitelist.WhitelistError):
        run(whitelist.resolve_entries(client, '303'))
    assert run(whitelist.resolve_entries(client, '-1000000000303')) == [entry(group())]
    with pytest.raises(whitelist.WhitelistError):
        run(whitelist.resolve_entries(Client(group(), group(304)), 'Test Group'))


def test_forward_and_contact_resolve_visible_origin():
    for entity, peer in [(account(), PeerUser(202)), (group(), PeerChannel(303))]:
        message = NS(fwd_from=NS(from_id=peer))
        assert run(whitelist.resolve_entries(Client(entity), 'ignored body', message=message)) == [entry(entity)]
    assert run(whitelist.resolve_entries(Client(account()), contact=NS(user_id=202))) == [entry(account())]
    for kwargs in [{'message': NS(fwd_from=NS(from_id=None))}, {'contact': NS(user_id=0)}]:
        with pytest.raises(whitelist.WhitelistError):
            run(whitelist.resolve_entries(Client(account()), '@testuser', **kwargs))


@pytest.mark.parametrize('entity', [account(bot=True), account(deleted=True), account(is_self=True),
    Channel(id=444, title='Broadcast', photo=ChatPhotoEmpty(), date=None, broadcast=True)])
def test_non_user_or_group_destinations_are_excluded(entity):
    with pytest.raises(whitelist.WhitelistError):
        run(whitelist.resolve_entries(Client(entity), str(entity.id)))


def test_existing_basic_group_accepts_canonical_negative_id():
    entity = Chat(id=404, title='Basic', photo=ChatPhotoEmpty(), participants_count=2, date=None, version=1)
    assert run(whitelist.resolve_entries(Client(entity), '-404')) == [entry(entity)]


def test_whitelist_intersects_banner_categories_and_blacklist_wins():
    client = Client(account(), account(203), group())
    models.add_whitelist_entries(101, [entry(account()), entry(group())])
    b = banner()
    b['send_target'] = ['private']
    targets = run(sender.collect_banner_targets(client, b, 101))
    assert [t['chat_id'] for t in targets] == [202]
    models.add_blacklist(user_id=101, target='202', target_type='user')
    targets = run(sender.collect_banner_targets(client, b, 101))
    assert targets[0]['blacklisted']
    with pytest.raises(sender.DeliverySkipped):
        run(sender.send_banner_to_target(client, b, targets[0]))
    client.send_message.assert_not_awaited()


def test_username_reassignment_never_changes_stored_recipient():
    original = account()
    models.add_whitelist_entries(101, [entry(original)])
    original.username = 'newname'
    targets = run(sender.collect_banner_targets(Client(original, account(203)), banner(), 101))
    assert [t['chat_id'] for t in targets] == [202]


def test_empty_enabled_blocks_all_and_explicit_disable_restores_dialog_selection():
    client = Client(account(), group())
    models.set_whitelist_enabled(101, True)
    assert run(sender.collect_banner_targets(client, banner(), 101)) == []
    models.set_whitelist_enabled(101, False)
    assert len(run(sender.collect_banner_targets(client, banner(), 101))) == 2


@pytest.mark.parametrize('mode', ['remove', 'blacklist', 'empty_enable'])
def test_policy_rechecked_after_source_lookup_before_send(mode):
    models.init_tabchi_db()
    client = Client(account())
    b = banner(source_peer='source', source_message_id=12)
    if mode != 'empty_enable':
        models.add_whitelist_entries(101, [entry(account())])
    targets = run(sender.collect_banner_targets(client, b, 101))
    async def source(*args, **kwargs):
        if mode == 'remove':
            models.delete_whitelist(models.list_whitelist(101)[0]['id'], 101)
        elif mode == 'blacklist':
            models.add_blacklist(user_id=101, target='202', target_type='user')
        else:
            models.set_whitelist_enabled(101, True)
        return NS(id=12)
    client.get_messages.side_effect = source
    with pytest.raises(sender.DeliverySkipped):
        run(sender.send_banner_to_target(client, b, targets[0]))
    client.send_message.assert_not_awaited()
    client.forward_messages.assert_not_awaited()


@pytest.mark.parametrize('mode,typ', [('normal', 'text'), ('normal', 'photo'), ('forward', 'text')])
def test_allowed_target_still_sends_all_supported_paths(mode, typ):
    client = Client(account())
    models.add_whitelist_entries(101, [entry(account())])
    b = banner(source_peer='source', source_message_id=12)
    b.update(send_mode=mode, type=typ)
    target = run(sender.collect_banner_targets(client, b, 101))[0]
    assert run(sender.send_banner_to_target(client, b, target)).id == 42
    assert client.send_message.await_count + client.forward_messages.await_count == 1


def event(uid=101, **kwargs):
    result = NS(sender_id=uid, chat_id=uid, raw_text='', text='', message=None,
        contact=None, edit=AsyncMock(), reply=AsyncMock(), answer=AsyncMock())
    for k, v in kwargs.items():
        setattr(result, k, v)
    return result


def test_controller_add_cancel_and_owner_scoped_delete(monkeypatch):
    monkeypatch.setattr('self_manager.get_client_for_user', lambda uid: Client(account()))
    controller = TabchiController(NS(), 'mainbot', lambda uid: True)
    async def scenario():
        await controller.handle_callback(event(), 'tb_wl_add')
        await controller.handle_message(event(raw_text='202\n@testuser'))
        assert models.whitelist_policy(101) == (True, {202})
        row = models.list_whitelist(101)[0]
        await controller.handle_callback(event(999), f"tb_wl_del_{row['id']}")
        assert models.whitelist_policy(101) == (True, {202})
        await controller.handle_callback(event(), 'tb_wl_add')
        await controller.handle_message(event(raw_text='لغو'))
        assert not controller.has_state(101)
        await controller.handle_callback(event(), f"tb_wl_del_{row['id']}")
        assert models.whitelist_policy(101) == (True, set())
    run(scenario())


def test_cancel_during_resolution_never_adds_destinations(monkeypatch):
    controller = TabchiController(NS(), 'mainbot', lambda uid: True)
    class CancellingClient(Client):
        async def iter_dialogs(self):
            await controller.handle_callback(event(), 'tb_whitelist')
            yield NS(entity=account())
    monkeypatch.setattr('self_manager.get_client_for_user', lambda uid: CancellingClient())
    async def scenario():
        await controller.handle_callback(event(), 'tb_wl_add')
        await controller.handle_message(event(raw_text='202'))
        assert models.whitelist_policy(101) == (False, set())
    run(scenario())


def test_controller_unknown_batch_and_group_callbacks_make_no_changes(monkeypatch):
    monkeypatch.setattr('self_manager.get_client_for_user', lambda uid: Client(account()))
    controller = TabchiController(NS(), 'mainbot', lambda uid: True)
    async def scenario():
        await controller.handle_callback(event(chat_id=-999), 'tb_wl_enable')
        assert models.whitelist_policy(101) == (False, set())
        await controller.handle_callback(event(), 'tb_wl_add')
        await controller.handle_message(event(raw_text='202\n99999999'))
        assert models.whitelist_policy(101) == (False, set())
        assert controller.has_state(101)
    run(scenario())


def test_pagination_can_access_every_entry():
    for offset in range(0, 45, 15):
        models.add_whitelist_entries(101, [entry(account(1000+i)) for i in range(offset, offset+15)])
    seen = set()
    for page in range(3):
        text, buttons = render_whitelist(101, page)
        for row in buttons:
            for button in row:
                data = getattr(button, 'data', getattr(getattr(button, 'type', None), 'data', b'')).decode()
                if data.startswith('tb_wl_del_'):
                    seen.add(int(data.rsplit('_', 1)[1]))
    assert seen == {row['id'] for row in models.list_whitelist(101)}
    assert render_whitelist(101, 999) == render_whitelist(101, 2)


def test_scheduler_logs_policy_skip_without_sending_or_pausing(monkeypatch):
    b = dict(id=1, user_id=101, interval=5)
    monkeypatch.setattr(models, 'list_due_banners', Mock(side_effect=[[b], asyncio.CancelledError()]))
    monkeypatch.setattr(scheduler, 'can_run', lambda uid: True)
    monkeypatch.setattr('self_manager.get_client_for_user', lambda uid: Client(account()))
    monkeypatch.setattr(scheduler, 'collect_banner_targets', AsyncMock(return_value=[{'chat_id': 202, 'title': 'Test'}]))
    monkeypatch.setattr(scheduler, 'send_banner_to_target', AsyncMock(side_effect=sender.DeliverySkipped()))
    log, mark, pause = Mock(), Mock(), Mock()
    monkeypatch.setattr(models, 'log_send', log)
    monkeypatch.setattr(models, 'mark_banner_cycle', mark)
    monkeypatch.setattr(models, 'set_banner_status', pause)
    monkeypatch.setattr(scheduler.asyncio, 'sleep', AsyncMock())
    with pytest.raises(asyncio.CancelledError):
        run(scheduler.run_scheduler())
    assert log.call_args.kwargs['status'] == 'skipped_policy'
    mark.assert_called_once_with(1, 101, 5)
    pause.assert_not_called()
