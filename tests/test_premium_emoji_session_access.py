"""Offline access/security regression tests; never live or visual evidence."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from telethon import errors

import config
import self_manager
from handlers import admin as admin_module
from tools import premium_emoji_live_smoke as live
from services import session_restore
from test_premium_emoji_curation import setup_live

SECRET = 'PRIVATE_SESSION_SENTINEL_DO_NOT_PRINT'


def args_for(tmp_path, monkeypatch, *selectors):
    sessions = tmp_path / 'custom-location' / 'sessions'
    sessions.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, 'SESSIONS_DIR', str(sessions))
    monkeypatch.setattr(config, 'DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setattr(self_manager, 'active_clients', {})
    return live.build_parser().parse_args([
        *selectors, '--curation', '--output-dir', str(tmp_path / 'output')])


def client_for(monkeypatch):
    client, resolver, sleep = setup_live(monkeypatch)
    client.get_me.return_value = NS(id=123, premium=True, bot=False)
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=True)
    client.is_connected = Mock(return_value=True)
    factory = Mock(return_value=client)
    monkeypatch.setattr(live, 'TelegramClient', factory)
    monkeypatch.setattr(live, 'StringSession', lambda saved: object())
    return client, factory, resolver, sleep


@pytest.mark.parametrize('selector', ['--user-id', '--session-name'])
def test_config_directory_resolution_and_existing_validator(tmp_path, monkeypatch, selector):
    args = args_for(tmp_path, monkeypatch, selector, '123' if selector == '--user-id' else 'user_123')
    original = session_restore.session_user_id
    validator = Mock(side_effect=original)
    monkeypatch.setattr(session_restore, 'session_user_id', validator)
    path, uid = live.resolve_session_source(args)
    assert path == Path(config.SESSIONS_DIR) / 'user_123.txt'
    assert uid == 123
    validator.assert_called_once_with('user_123')


@pytest.mark.parametrize('name', ['../user_123', '/tmp/user_123', 'user_123/../other',
    '..\\user_123', 'user_123.txt', 'user_0', 'user_0123', 'user_-123', 'other', ''])
def test_bad_session_names_rejected_without_read(tmp_path, monkeypatch, name):
    args = args_for(tmp_path, monkeypatch, '--session-name', name)
    with pytest.raises(ValueError):
        live.resolve_session_source(args)


def test_session_symlink_escape_rejected(tmp_path, monkeypatch):
    args = args_for(tmp_path, monkeypatch, '--user-id', '123')
    external = tmp_path / 'outside.txt'
    external.write_text(SECRET)
    (Path(config.SESSIONS_DIR) / 'user_123.txt').symlink_to(external)
    with pytest.raises(ValueError): live.resolve_session_source(args)


@pytest.mark.parametrize('selectors', [[], ['--user-id','123','--session-name','user_123'],
    ['--user-id','123','--session-string-file',SECRET],
    ['--session-name','user_123','--session-string-file',SECRET],
    ['--user-id', SECRET], ['--unknown', SECRET]])
def test_cli_requires_one_source_and_never_echoes_bad_input(selectors, capsys):
    with pytest.raises(SystemExit) as exc:
        live.build_parser().parse_args(selectors)
    assert exc.value.code == 2
    output = capsys.readouterr()
    assert SECRET not in output.out + output.err
    assert 'exactly one' in output.err


@pytest.mark.parametrize('sources', [{}, {'user_id':123,'session_name':'user_123'}])
def test_programmatic_source_exclusivity(sources):
    with pytest.raises(ValueError): live.resolve_session_source(NS(**sources))


def test_owned_client_read_only_disconnect_and_safe_artifacts(tmp_path, monkeypatch, capsys, caplog):
    args = args_for(tmp_path, monkeypatch, '--user-id', '123')
    path, _ = live.resolve_session_source(args)
    path.write_text(SECRET)
    before = path.stat().st_mtime_ns
    client, factory, _, _ = client_for(monkeypatch)
    asyncio.run(live.main(args))
    client.connect.assert_awaited_once()
    client.disconnect.assert_awaited_once()
    assert path.read_text() == SECRET and path.stat().st_mtime_ns == before
    files = list(Path(args.output_dir).iterdir())
    assert len(files) == 4
    assert all(SECRET not in p.read_text() for p in files)
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err + caplog.text
    logger = factory.call_args.kwargs['base_logger']
    logger.warning(SECRET)
    assert SECRET not in caplog.text


@pytest.mark.parametrize('failure', ['unauthorized','wrong_user','bot','connect','validation'])
def test_owned_client_disconnects_on_failure(tmp_path, monkeypatch, failure):
    args = args_for(tmp_path, monkeypatch, '--user-id','123')
    live.resolve_session_source(args)[0].write_text(SECRET)
    client, _, _, _ = client_for(monkeypatch)
    if failure == 'unauthorized': client.is_user_authorized.return_value = False
    if failure == 'wrong_user': client.get_me.return_value.id = 456
    if failure == 'bot': client.get_me.return_value.bot = True
    if failure == 'connect': client.connect.side_effect = RuntimeError(SECRET)
    if failure == 'validation': monkeypatch.setattr(live,'curation_run',AsyncMock(side_effect=RuntimeError(SECRET)))
    with pytest.raises(RuntimeError): asyncio.run(live.main(args))
    client.disconnect.assert_awaited_once()
    assert not Path(args.output_dir).exists()


def test_cli_exception_message_session_never_printed(tmp_path, monkeypatch, capsys, caplog):
    args_for(tmp_path, monkeypatch, '--user-id','123')
    (Path(config.SESSIONS_DIR) / 'user_123.txt').write_text(SECRET)
    client, _, _, _ = client_for(monkeypatch)
    client.connect.side_effect = RuntimeError(SECRET)
    assert live.cli(['--user-id','123','--curation']) == 1
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err + caplog.text
    assert 'RuntimeError' in captured.err and 'Traceback' not in captured.err


@pytest.mark.parametrize('failure', [None,'unauthorized','validation','cancelled'])
def test_borrowed_client_reused_never_disconnected_or_file_read(tmp_path, monkeypatch, failure):
    args = args_for(tmp_path, monkeypatch, '--user-id','123')
    # There is deliberately no session file: in-process curation needs only client.
    client, factory, _, _ = client_for(monkeypatch)
    self_manager.register_client(123, client)
    if failure == 'unauthorized': client.is_user_authorized.return_value = False
    if failure == 'validation': monkeypatch.setattr(live,'curation_run',AsyncMock(side_effect=RuntimeError(SECRET)))
    if failure == 'cancelled': monkeypatch.setattr(live,'curation_run',AsyncMock(side_effect=asyncio.CancelledError()))
    if failure:
        with pytest.raises((RuntimeError,asyncio.CancelledError)): asyncio.run(live.main(args))
    else: asyncio.run(live.main(args))
    factory.assert_not_called()
    client.connect.assert_not_called()
    client.disconnect.assert_not_called()
    assert self_manager.get_client_for_user(123) is client


@pytest.mark.parametrize('samples', [False,True])
def test_runtime_writes_four_artifacts_inside_data_and_me_only(tmp_path, monkeypatch, samples):
    args_for(tmp_path, monkeypatch, '--user-id','123')
    client, factory, _, _ = client_for(monkeypatch)
    self_manager.register_client(123, client)
    directory, status = asyncio.run(live.runtime_curation(123, send_samples=samples))
    assert directory.parent == Path(config.DATA_DIR) / 'premium_emoji_curation'
    assert directory.name.startswith('run-')
    assert len(list(directory.iterdir())) == 4
    assert json.loads((directory/'PREMIUM_EMOJI_MAPPING_STATUS.json').read_text()) == status
    assert status['curated_emoji_count'] == 0
    assert client.send_message.await_count == (2 if samples else 0)
    assert all(call.args[0] == 'me' for call in client.send_message.call_args_list)
    factory.assert_not_called()
    client.disconnect.assert_not_called()


def test_runtime_offline_never_falls_back_to_session(tmp_path, monkeypatch):
    args = args_for(tmp_path, monkeypatch, '--user-id','123')
    live.resolve_session_source(args)[0].write_text(SECRET)
    _, factory, _, _ = client_for(monkeypatch)
    with pytest.raises(RuntimeError, match='Self client is not online'):
        asyncio.run(live.runtime_curation(123))
    factory.assert_not_called()
    assert not Path(config.DATA_DIR).exists()


def event(text, owner=True, private=True):
    return NS(raw_text=text, sender_id=admin_module.ADMIN_ID if owner else -1,
        is_private=private, reply=AsyncMock())


def test_nonowner_and_group_commands_cannot_run(monkeypatch):
    controller = admin_module.AdminController.__new__(admin_module.AdminController)
    launch = AsyncMock()
    monkeypatch.setattr(live, 'runtime_curation', launch)
    for e in [event('/emoji_curation 123',owner=False),event('/emoji_curation_samples 123',owner=False),
              event('/emoji_curation 123',private=False),event('/emoji_curation ../123')]:
        assert asyncio.run(controller.handle_emoji_curation(e)) is True
        e.reply.assert_awaited_once()
    launch.assert_not_called()


def test_admin_offline_rejected(tmp_path, monkeypatch):
    args_for(tmp_path,monkeypatch,'--user-id','123')
    controller = admin_module.AdminController.__new__(admin_module.AdminController)
    e = event('/emoji_curation 123')
    assert asyncio.run(controller.handle_emoji_curation(e))
    assert e.reply.call_args.args == ('Self client is not online',)


@pytest.mark.parametrize('command,samples', [('/emoji_curation',False),('/emoji_curation_samples',True)])
def test_admin_explicit_sample_command_no_panel_state(tmp_path, monkeypatch, command, samples):
    args_for(tmp_path,monkeypatch,'--user-id','123')
    client, _, _, _ = client_for(monkeypatch)
    self_manager.register_client(123,client)
    controller = admin_module.AdminController.__new__(admin_module.AdminController)
    e = event(command+' 123')
    assert asyncio.run(controller.handle_message(e))
    assert client.send_message.await_count == (2 if samples else 0)
    client.disconnect.assert_not_called()


def test_admin_exception_sanitized_and_busy_reset(tmp_path, monkeypatch):
    args_for(tmp_path,monkeypatch,'--user-id','123')
    client, _, _, _ = client_for(monkeypatch)
    self_manager.register_client(123,client)
    controller = admin_module.AdminController.__new__(admin_module.AdminController)
    launch=AsyncMock(side_effect=ValueError(SECRET))
    monkeypatch.setattr(live,'runtime_curation',launch)
    e=event('/emoji_curation 123')
    asyncio.run(controller.handle_emoji_curation(e))
    assert e.reply.call_args.args[0] == 'Emoji curation failed: ValueError'
    assert controller._emoji_curation_busy is False
    controller._emoji_curation_busy=True
    asyncio.run(controller.handle_emoji_curation(event('/emoji_curation_samples 123')))
    assert launch.await_count == 1


def test_runtime_large_floodwait_stops_without_retry_or_disconnect(tmp_path,monkeypatch):
    args_for(tmp_path,monkeypatch,'--user-id','123')
    client, _, _, sleep = client_for(monkeypatch)
    self_manager.register_client(123,client)
    client.send_message.side_effect=errors.FloodWaitError(None, 120)
    directory, _ = asyncio.run(live.runtime_curation(123,send_samples=True))
    assert client.send_message.await_count == 1
    sleep.assert_not_called()
    client.disconnect.assert_not_called()
    assert json.loads((directory/'PREMIUM_EMOJI_SAMPLES.json').read_text())['retry_after']==120


@pytest.mark.parametrize('owner', [False,True])
def test_registered_bot_command_routes_without_admin_state(tmp_path, monkeypatch, owner):
    from test_security_runtime import Event, run as run_bot
    args_for(tmp_path,monkeypatch,'--user-id','123')
    client, _, _, _ = client_for(monkeypatch)
    self_manager.register_client(123,client)
    async def scenario(bot):
        e=Event(bot,uid=admin_module.ADMIN_ID if owner else 101,text='/emoji_curation 123')
        await bot.handlers['msg_handler'](e)
        if owner:
            assert 'Curation saved:' in e.reply.call_args.args[0]
            client.is_user_authorized.assert_awaited_once()
        else:
            assert 'Unauthorized' in e.reply.call_args.args[0]
            client.is_user_authorized.assert_not_called()
        client.send_message.assert_not_called()
    run_bot(monkeypatch,scenario)


def test_disconnected_registered_client_is_not_reconnected(tmp_path,monkeypatch):
    args=args_for(tmp_path,monkeypatch,'--user-id','123')
    client,factory,_,_=client_for(monkeypatch)
    client.is_connected.return_value=False
    self_manager.register_client(123,client)
    with pytest.raises(RuntimeError,match='not online'):
        asyncio.run(live.main(args))
    client.connect.assert_not_called()
    client.disconnect.assert_not_called()
    factory.assert_not_called()


def test_runtime_output_symlink_escape_rejected(tmp_path,monkeypatch):
    args_for(tmp_path,monkeypatch,'--user-id','123')
    client,_,_,_=client_for(monkeypatch)
    self_manager.register_client(123,client)
    root=Path(config.DATA_DIR)
    root.mkdir()
    outside=tmp_path/'outside'
    outside.mkdir()
    (root/'premium_emoji_curation').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError): asyncio.run(live.runtime_curation(123))
    assert not list(outside.iterdir())
    client.send_message.assert_not_called()
