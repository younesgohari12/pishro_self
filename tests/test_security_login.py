import asyncio
import time
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from telethon import errors
from telethon.tl.types import auth
import login_manager as lm


@pytest.fixture(autouse=True)
def reset_login(tmp_path, monkeypatch):
    monkeypatch.setattr(lm, 'login_states', {})
    monkeypatch.setattr(lm, '_request_after', {})
    monkeypatch.setattr(lm, 'SESSIONS_DIR', str(tmp_path / 'sessions'))


def prepare(monkeypatch, response=None, error=None):
    bot=NS(send_message=AsyncMock(return_value=NS(id=77)))
    client=NS(connect=AsyncMock(), disconnect=AsyncMock(),
              send_code_request=AsyncMock(return_value=response or auth.SentCode(auth.SentCodeTypeApp(5),'hash'),side_effect=error),
              get_me=AsyncMock(), sign_in=AsyncMock(), session=NS(save=Mock(return_value='test-only')))
    constructor=Mock(return_value=client)
    monkeypatch.setattr(lm,'TelegramClient',constructor)
    return bot,client,constructor


@pytest.mark.parametrize('contact',[None, NS(user_id=202,phone_number='+989120000000'),NS(user_id=101,phone_number='+0000'),NS(user_id=101,phone_number='9'*30)])
def test_manual_and_other_contacts_never_request_code(monkeypatch,contact):
    bot,client,constructor=prepare(monkeypatch)
    async def run():
        await lm.start_login(bot,101,101)
        await lm.send_code_request(bot,101,101,'+989120000000',contact=contact)
    asyncio.run(run())
    constructor.assert_not_called()
    assert lm.login_states[101]['step']=='WAIT_PHONE'


def test_own_contact_real_sentcode_and_server_length(monkeypatch):
    bot,client,_=prepare(monkeypatch,response=auth.SentCode(auth.SentCodeTypeApp(6),'hash'))
    async def run():
        await lm.start_login(bot,101,101)
        await lm.send_code_request(bot,101,101,contact=NS(user_id=101,phone_number='989121234567'))
    asyncio.run(run())
    client.send_code_request.assert_awaited_once_with('+989121234567')
    assert lm.login_states[101]['step']=='WAIT_CODE'
    assert lm.login_states[101]['code_length']==6


@pytest.mark.parametrize('response',[NS(phone_code_hash='fake'), auth.SentCode(auth.SentCodeTypeApp(5),''),auth.SentCode(auth.SentCodeTypeApp(0),'hash')])
def test_unusable_response_never_shows_code_keyboard(monkeypatch,response):
    bot,client,_=prepare(monkeypatch,response=response)
    async def run():
        await lm.start_login(bot,101,101)
        await lm.send_code_request(bot,101,101,contact=NS(user_id=101,phone_number='+989121234567'))
    asyncio.run(run())
    assert 101 not in lm.login_states
    assert not any(call.kwargs.get('buttons')==lm.number_keyboard(101) for call in bot.send_message.call_args_list)


def test_unregistered_number_error_cleans_state(monkeypatch):
    bot,client,_=prepare(monkeypatch,error=errors.PhoneNumberUnoccupiedError(request=None))
    async def run():
        await lm.start_login(bot,101,101)
        await lm.send_code_request(bot,101,101,contact=NS(user_id=101,phone_number='+989121234567'))
    asyncio.run(run())
    assert 101 not in lm.login_states
    assert client.disconnect.await_count >= 1


def test_duplicate_contacts_send_once(monkeypatch):
    bot,client,_=prepare(monkeypatch)
    async def run():
        await lm.start_login(bot,101,101)
        await asyncio.gather(*(lm.send_code_request(bot,101,101,contact=NS(user_id=101,phone_number='+989121234567')) for _ in range(20)))
    asyncio.run(run())
    client.send_code_request.assert_awaited_once()


def test_cancel_inflight_request_does_not_restore_state(monkeypatch):
    bot,client,_=prepare(monkeypatch)
    async def response(*args):
        lm.cleanup_state(101)
        return auth.SentCode(auth.SentCodeTypeApp(5),'hash')
    client.send_code_request.side_effect=response
    async def run():
        await lm.start_login(bot,101,101)
        await lm.send_code_request(bot,101,101,contact=NS(user_id=101,phone_number='+989121234567'))
    asyncio.run(run())
    assert 101 not in lm.login_states


def test_other_identity_cannot_save_session_for_install(monkeypatch,tmp_path):
    bot,client,_=prepare(monkeypatch)
    client.get_me.return_value=NS(id=202,bot=False,deleted=False)
    async def run():
        await lm.start_login(bot,101,101)
        state=lm.login_states[101]; state['client']=client
        await lm.complete_login(bot,101,101,state)
    asyncio.run(run())
    client.session.save.assert_not_called()
    assert not (tmp_path/'sessions').exists()


def test_callback_owner_message_and_step_binding(monkeypatch):
    bot,client,_=prepare(monkeypatch)
    async def run():
        await lm.start_login(bot,101,101)
        state=lm.login_states[101]
        state.update(client=client,step='WAIT_CODE',code='12345',code_message_id=77,code_length=5)
        for uid,msg,step in [(202,77,'WAIT_CODE'),(101,88,'WAIT_CODE'),(101,77,'WAIT_2FA')]:
            state['step']=step
            event=NS(sender_id=uid,chat_id=101,message_id=msg,answer=AsyncMock())
            await lm.handle_code_button(event,101,'ok')
        client.sign_in.assert_not_awaited()
    asyncio.run(run())


def test_expired_and_group_login_never_send_code(monkeypatch):
    bot,client,constructor=prepare(monkeypatch)
    async def run():
        await lm.start_login(bot,-100,101)
        assert not lm.login_states
        await lm.start_login(bot,101,101)
        lm.login_states[101]['expires_at']=time.monotonic()-1
        await lm.send_code_request(bot,101,101,contact=NS(user_id=101,phone_number='+989121234567'))
    asyncio.run(run())
    constructor.assert_not_called()


def test_successful_same_account_login_saves_session_and_starts_service(monkeypatch,tmp_path):
    import self_manager
    bot,client,_=prepare(monkeypatch)
    client.get_me.return_value=NS(id=101,bot=False,deleted=False,first_name='Test',last_name='',username='tester')
    start=AsyncMock()
    monkeypatch.setattr(self_manager,'start_session',start)
    async def run():
        await lm.start_login(bot,101,101)
        state=lm.login_states[101];state['client']=client
        await lm.complete_login(bot,101,101,state)
    asyncio.run(run())
    assert (tmp_path/'sessions'/'user_101.txt').read_text()=='test-only'
    assert (tmp_path/'sessions'/'user_101.txt').stat().st_mode & 0o777 == 0o600
    start.assert_awaited_once_with('user_101')
    assert 101 not in lm.login_states


def test_two_factor_transition_and_wrong_code_retry(monkeypatch):
    bot,client,_=prepare(monkeypatch)
    async def run():
        await lm.start_login(bot,101,101)
        state=lm.login_states[101]
        state.update(client=client,phone='+989121234567',hash='hash',code='12345',step='WAIT_CODE')
        event=NS(sender_id=101,chat_id=101,client=bot,answer=AsyncMock(),edit=AsyncMock())
        client.sign_in.side_effect=errors.PhoneCodeInvalidError(request=None)
        await lm.verify_code(event,101,state)
        assert state['step']=='WAIT_CODE' and state['code']==''
        client.sign_in.side_effect=errors.SessionPasswordNeededError(request=None)
        state['code']='12345'
        await lm.verify_code(event,101,state)
        assert state['step']=='WAIT_2FA'
        client.session.save.assert_not_called()
    asyncio.run(run())
