"""Saved credentials are restored with Telegram mocked and real isolated DBs."""
import asyncio
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from telethon.crypto import AuthKey
from telethon.sessions import StringSession

import db
import self as runtime
import self_manager
from database import models
from services import balance_service, trial_manager, access_service
from services.session_restore import restore_account


def identity(uid=101, **kwargs):
    values = dict(id=uid, username='test_user', first_name='Test', last_name='', bot=False)
    values.update(kwargs)
    return NS(**values)


def saved_string():
    session = StringSession()
    session.set_dc(2, '149.154.167.50', 443)
    session.auth_key = AuthKey(bytes(range(256)))  # synthetic, never valid at Telegram
    return session.save()


@pytest.fixture
def storage(tmp_path, monkeypatch):
    path = tmp_path / 'sessions'
    path.mkdir()
    monkeypatch.setattr(self_manager, 'SESSIONS_DIR', str(path))
    monkeypatch.setattr(self_manager, 'running_sessions', {})
    monkeypatch.setattr(self_manager, 'active_clients', {})
    return path


class Client:
    def __init__(self, me=None, authorized=True, scenario=None):
        self.connect = AsyncMock()
        self.disconnect = AsyncMock()
        self.is_user_authorized = AsyncMock(return_value=authorized)
        self.get_me = AsyncMock(return_value=me or identity())
        self.send_message = AsyncMock()
        self.log_out = AsyncMock(side_effect=AssertionError('Never log out on restore'))
        self.sign_in = AsyncMock(side_effect=AssertionError('Never re-login on restore'))
        self.handlers = {}
        self.scenario = scenario

    def on(self, *args, **kwargs):
        def register(fn):
            self.handlers[fn.__name__] = fn
            return fn
        return register

    async def __call__(self, request):
        return NS(full_user=NS(about=''))

    async def run_until_disconnected(self):
        if self.scenario:
            await self.scenario(self)


def test_orphan_session_restores_real_runtime_without_new_login(storage, monkeypatch):
    path = storage / 'user_101.txt'
    path.write_text(saved_string(), encoding='utf-8')
    before = path.read_bytes()
    async def scenario(client):
        assert balance_service.get_user(101) is not None
        assert models.get_profile(101)['first_name'] == 'Test'
        assert 101 in db.get_all_user_ids()
        assert self_manager.get_client_for_user(101) is client
        assert 'panel_cmd' in client.handlers
        assert not access_service.can_run(101)  # connection does not invent credit
    client = Client(scenario=scenario)
    monkeypatch.setattr(runtime, 'TelegramClient', lambda *a, **k: client)
    async def run():
        tasks = await self_manager.start_all_sessions()
        assert len(tasks) == 1
        await asyncio.gather(*tasks)
        await asyncio.sleep(0)
        assert not self_manager.running_sessions
    asyncio.run(run())
    client.connect.assert_awaited_once()
    client.disconnect.assert_awaited_once()
    client.sign_in.assert_not_called()
    client.log_out.assert_not_called()
    assert path.read_bytes() == before
    assert not self_manager.active_clients


def test_legacy_json_balance_imports_once_and_existing_sqlite_wins():
    db._save_user('101', db._normalize_settings({
        'diamonds': 175, 'self_enabled': False, 'bill_seconds': 234,
        'muted_chats': [500], 'base_bio': 'Original',
    }))
    restore_account('user_101', identity())
    assert balance_service.get_balance(101) == 175
    balance_service.admin_adjust(101, -25)
    restore_account('user_101', identity())
    s = db.get_user_settings(101)
    assert s['diamonds'] == 150
    assert s['self_enabled'] is False
    assert s['bill_seconds'] == 234 and s['muted_chats'] == [500]
    assert s['base_bio'] == 'Original'
    assert not access_service.can_run(101)


def test_sqlite_only_account_recovers_json_and_preserves_balance():
    models.init_game_db()
    balance_service.ensure_user(101, initial_balance=75)
    assert db.get_all_user_ids() == []
    restore_account('user_101', identity())
    assert db.get_user_settings(101)['diamonds'] == 75
    assert access_service.can_run(101)


def test_recovery_never_renews_expired_trial():
    trial_manager.activate_after_login(101)
    trial_manager.revoke(101)
    before = models.get_trial(101)
    restore_account('user_101', identity())
    assert models.get_trial(101) == before
    assert not access_service.can_run(101)


@pytest.mark.parametrize('me', [identity(202), identity(bot=True), None])
def test_wrong_identity_cannot_create_accounts(me):
    with pytest.raises(ValueError):
        restore_account('user_101', me)
    assert db.get_all_user_ids() == []
    assert balance_service.get_user(101) is None
    assert balance_service.get_user(202) is None


@pytest.mark.parametrize('failure', ['unauthorized', 'wrong_id', 'network', 'get_me', 'storage'])
def test_failed_restore_preserves_file_and_disconnects(storage, monkeypatch, failure):
    path = storage / 'user_101.txt'
    path.write_text(saved_string())
    before = path.read_bytes()
    client = Client(identity(202 if failure == 'wrong_id' else 101), failure != 'unauthorized')
    if failure == 'network':
        client.connect.side_effect = OSError('offline')
    if failure == 'get_me':
        client.get_me.side_effect = OSError('offline')
    if failure == 'storage':
        monkeypatch.setattr(db, '_write_json', Mock(side_effect=OSError('disk full')))
    monkeypatch.setattr(runtime, 'TelegramClient', lambda *a, **k: client)
    asyncio.run(runtime.run_self(str(path.with_suffix('')), path.read_text()))
    client.disconnect.assert_awaited_once()
    client.log_out.assert_not_called()
    assert path.read_bytes() == before
    assert not client.handlers
    assert not self_manager.active_clients


def test_invalid_session_does_not_block_other_files(storage, monkeypatch):
    (storage / 'user_101.txt').write_text('invalid-test-session')
    (storage / 'user_202.txt').write_text(saved_string())
    client = Client(identity(202))
    constructor = Mock(return_value=client)
    monkeypatch.setattr(runtime, 'TelegramClient', constructor)
    async def run():
        tasks = await self_manager.start_all_sessions()
        await asyncio.gather(*tasks)
    asyncio.run(run())
    constructor.assert_called_once()
    assert db.get_all_user_ids() == [202]
    assert (storage / 'user_101.txt').read_text() == 'invalid-test-session'


def test_reader_accepts_bom_crlf_and_rejects_unsafe_names(storage):
    (storage / 'user_101.txt').write_bytes(b'\xef\xbb\xbf' + saved_string().encode() + b'\r\n')
    (storage / 'user_202.txt').write_bytes(b'\xff\xfe\x00')
    (storage / 'user_303.txt').mkdir()
    (storage / 'user_notes.txt').write_text('not a session')
    assert self_manager.list_sessions() == ['user_101', 'user_202']
    assert self_manager.get_session_string('user_101') == saved_string()
    assert self_manager.get_session_string('user_202') is None
    for name in ('../config', 'user_0', 'user_١٠١', 'user_1/../../config'):
        assert self_manager.get_session_string(name) is None


def test_duplicate_restore_starts_one_task_and_cancel_disconnects(storage, monkeypatch):
    (storage / 'user_101.txt').write_text(saved_string())
    ready = None
    async def scenario(client):
        ready.set()
        await asyncio.Event().wait()
    client = Client(scenario=scenario)
    monkeypatch.setattr(runtime, 'TelegramClient', lambda *a, **k: client)
    async def run():
        nonlocal ready
        ready = asyncio.Event()
        assert await self_manager.start_session('user_101', restore=True)
        assert not await self_manager.start_session('user_101', restore=True)
        await asyncio.wait_for(ready.wait(), 2)
        assert await self_manager.stop_session('user_101')
    asyncio.run(run())
    assert not self_manager.running_sessions and not self_manager.active_clients
    client.disconnect.assert_awaited_once()


def test_restarted_orphan_still_connects_with_zero_wallet(storage, monkeypatch):
    (storage / 'user_101.txt').write_text(saved_string())
    client = Client()
    monkeypatch.setattr(runtime, 'TelegramClient', lambda *a, **k: client)
    async def run():
        for _ in range(2):
            tasks = await self_manager.start_all_sessions()
            assert len(tasks) == 1
            await asyncio.gather(*tasks)
            await asyncio.sleep(0)
    asyncio.run(run())
    assert client.connect.await_count == 2
    assert balance_service.get_balance(101) == 0


@pytest.mark.parametrize('funded', [False, True])
def test_persistent_loop_charges_verified_service_without_live_client(monkeypatch, funded):
    from services import usage_service
    from test_security_runtime import fund
    fund(101, 1 if funded else 0)
    now = [100000.0]
    monkeypatch.setattr(usage_service.time, 'time', lambda: now[0])
    usage_service.enroll_verified(101)
    monkeypatch.setattr(self_manager, 'running_sessions', {})
    monkeypatch.setattr(self_manager, 'active_clients', {})
    original_sleep = asyncio.sleep
    calls = [0]
    async def tick(delay):
        assert delay == 1
        calls[0] += 1
        if calls[0] == 2:
            raise asyncio.CancelledError()
        now[0] += 1800
        await original_sleep(0)
    monkeypatch.setattr(usage_service.asyncio, 'sleep', tick)
    async def scenario():
        with pytest.raises(asyncio.CancelledError):
            await usage_service.run_billing_loop()
    asyncio.run(scenario())
    assert balance_service.get_balance(101) == 0
    assert db.get_user_settings(101)['self_enabled'] is True


def test_idle_auxiliary_handlers_do_not_mutate_messages(monkeypatch):
    from services import font_formatter, deleted_handler, copy_protected
    font = AsyncMock()
    deleted = AsyncMock()
    copied = AsyncMock()
    monkeypatch.setattr(deleted_handler, 'cache_message', deleted)
    monkeypatch.setattr(copy_protected, '_handle_destination_input', copied)
    db.update_user_settings(101, {'message_font_enabled': True})
    client = Client()
    client.edit_message = font
    font_formatter.register_message_font_handler(client, 101)
    deleted_handler.register_deleted_message_handlers(client, 101)
    copy_protected.register_copy_protected_handlers(client, 101)
    async def run():
        event = NS(chat_id=100, id=1)
        for name in ('_message_font_handler', 'message_cache_handler', 'copy_protected_capture_handler'):
            await client.handlers[name](event)
    asyncio.run(run())
    font.assert_not_awaited()
    deleted.assert_not_awaited()
    copied.assert_not_awaited()
