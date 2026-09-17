import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
import db
import login_manager as lm
from database import models
from services import admin_manager, balance_service
from handlers.admin import AdminController
from test_security_runtime import Event, fund, run
import bot.core as core


def test_recycled_username_never_inherits_admin_permissions():
    models.upsert_admin(user_id=555,username='former_admin',role='full',permissions='*')
    assert not admin_manager.is_admin(666,'former_admin')
    assert admin_manager.is_admin(555,'new_name')


def test_username_only_legacy_admin_is_not_self_claimable():
    # Simulate a legacy row; only the owner may bind it to a numeric identity.
    models.init_membership_support_db()
    with models._conn() as conn:
        conn.execute("INSERT INTO admins (username,role,permissions,created_at) VALUES ('legacy','full','[]','old')")
    admin_manager.note_identity(666,'legacy')
    assert not admin_manager.is_admin(666,'legacy')


def test_revoked_settings_permission_invalidates_existing_prompt():
    models.upsert_admin(user_id=555,username='helper',role='limited',permissions=['view_users'])
    controller=AdminController(NS(send_message=AsyncMock()),resolve_user=AsyncMock())
    controller.states[555]={'step':'SYS_HOLDER'}
    event=Event(NS(),uid=555,text='unauthorized change')
    asyncio.run(controller.handle_message(event))
    assert db.get_global()['card_holder']!='unauthorized change'


def test_old_buy_button_cannot_change_transfer_confirmation(monkeypatch):
    fund(101,100);fund(202,0)
    async def scenario(bot):
        cb=bot.handlers['cb_handler'];msg=bot.handlers['msg_handler']
        await cb(Event(bot,data=b'transfer_menu'))
        await msg(Event(bot,text='202'));await msg(Event(bot,text='10'))
        before=dict(core.flow_states[101])
        for data in [b'buy_plus',b'buy_minus',b'buy_manual',b'buy_paid']:
            await cb(Event(bot,data=data))
            assert core.flow_states[101]==before
    run(monkeypatch,scenario)


def test_old_login_cancel_cannot_delete_a_new_attempt(monkeypatch):
    monkeypatch.setattr(lm,'login_states',{})
    bot=NS(send_message=AsyncMock())
    async def scenario():
        await lm.start_login(bot,101,101)
        old=lm.login_states[101];old.update(step='WAIT_CODE',code_message_id=77)
        async def replace(*args,**kwargs):
            await lm.start_login(bot,101,101)
        event=Event(bot,data=b'code_101_cancel');event.answer.side_effect=replace
        await lm.handle_code_button(event,101,'cancel')
        assert 101 in lm.login_states and lm.login_states[101] is not old
    asyncio.run(scenario())


def test_login_send_error_cleanup_survives_notification_failure(monkeypatch):
    from telethon import errors
    monkeypatch.setattr(lm,'login_states',{});monkeypatch.setattr(lm,'_request_after',{})
    bot=NS(send_message=AsyncMock(return_value=NS(id=77)))
    client=NS(connect=AsyncMock(),disconnect=AsyncMock(),send_code_request=AsyncMock(side_effect=errors.PhoneNumberInvalidError(request=None)))
    monkeypatch.setattr(lm,'TelegramClient',lambda *args,**kwargs:client)
    async def scenario():
        await lm.start_login(bot,101,101)
        bot.send_message.side_effect=[NS(id=77),OSError('bot reply failed')]
        try:
            await lm.send_code_request(bot,101,101,contact=NS(user_id=101,phone_number='+989121234567'))
        except OSError:
            pass
        assert 101 not in lm.login_states
    asyncio.run(scenario())


def test_session_file_failed_replace_preserves_previous_bytes(tmp_path,monkeypatch):
    path=tmp_path/'user_101.txt';path.write_text('old-session')
    monkeypatch.setattr(lm.os,'replace',lambda *args:(_ for _ in ()).throw(OSError('disk failure')))
    with pytest.raises(OSError):lm.save_session_atomic(str(path),'new-session')
    assert path.read_text()=='old-session'
    assert [p.name for p in tmp_path.iterdir()]==['user_101.txt']


def test_abandoned_login_expires_and_disconnects(monkeypatch):
    monkeypatch.setattr(lm,'login_states',{});monkeypatch.setattr(lm,'LOGIN_TTL',0.01)
    client=NS(disconnect=AsyncMock());bot=NS(send_message=AsyncMock())
    async def scenario():
        await lm.start_login(bot,101,101)
        lm.login_states[101]['client']=client
        await asyncio.sleep(0.03)
        assert 101 not in lm.login_states
        client.disconnect.assert_awaited_once()
    asyncio.run(scenario())


def test_failed_old_phone_prompt_does_not_clear_new_attempt(monkeypatch):
    monkeypatch.setattr(lm,'login_states',{})
    bot=NS(send_message=AsyncMock())
    async def scenario():
        async def replace_then_fail(*args,**kwargs):
            bot.send_message.side_effect=None
            await lm.start_login(bot,101,101)
            raise OSError('old prompt failed')
        bot.send_message.side_effect=replace_then_fail
        with pytest.raises(OSError):await lm.start_login(bot,101,101)
        assert 101 in lm.login_states
    asyncio.run(scenario())


def test_live_membership_must_pass_before_session_saved(monkeypatch,tmp_path):
    from services import membership_service
    monkeypatch.setattr(lm,'login_states',{})
    monkeypatch.setattr(lm,'SESSIONS_DIR',str(tmp_path/'sessions'))
    monkeypatch.setattr(membership_service,'check_required_memberships',AsyncMock(return_value=(False,[])))
    client=NS(get_me=AsyncMock(return_value=NS(id=101,bot=False,deleted=False)),disconnect=AsyncMock())
    bot=NS(send_message=AsyncMock())
    async def scenario():
        await lm.start_login(bot,101,101)
        state=lm.login_states[101];state['client']=client
        await lm.complete_login(bot,101,101,state)
        assert not (tmp_path/'sessions').exists()
    asyncio.run(scenario())


def test_login_clears_other_input_flows_and_blocks_interleaving(monkeypatch):
    from handlers.support import SupportController
    captured=[]
    def support(bot):
        controller=SupportController(bot);captured.append(controller);return controller
    monkeypatch.setattr(core,'SupportController',support)
    monkeypatch.setattr(lm,'login_states',{})
    monkeypatch.setattr(core,'login_states',lm.login_states)
    async def scenario(bot):
        cb=bot.handlers['cb_handler']
        await cb(Event(bot,data=b'support_ticket'))
        assert captured[0].has_state(101)
        await cb(Event(bot,data=b'install_self'))
        assert not captured[0].has_state(101)
        await cb(Event(bot,data=b'support_ticket'))
        assert not captured[0].has_state(101)
        await cb(Event(bot,data=b'main_menu'))
        assert 101 not in lm.login_states
    run(monkeypatch,scenario)
