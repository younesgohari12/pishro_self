import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from telethon.tl.types import User, Channel
from services import transfer_service as tr, identity_service as identities, balance_service
from services.transaction_service import connect
from handlers.transfer import TransferController
import handlers.transfer as group_module
import db
import bot.core as core
from test_security_runtime import Event, run, fund


def group_event(*, sender=101, target=202, text='انتقال 100', message_id=55, chat_id=-100123):
    author=User(id=sender)
    reply=NS(chat_id=chat_id,sender_id=target,get_sender=AsyncMock(return_value=User(id=target)))
    event=NS(is_group=True,is_private=False,raw_text=text,sender_id=sender,chat_id=chat_id,id=message_id,
             is_reply=True,fwd_from=None,get_sender=AsyncMock(return_value=author),
             get_reply_message=AsyncMock(return_value=reply),reply=AsyncMock())
    return event


def bot_for_users():
    return NS(get_entity=AsyncMock(side_effect=lambda target:User(id=202,username='recipient') if type(target) is str else User(id=target)))


@pytest.mark.parametrize('target',['@recipient','recipient','202','۲۰۲',202])
def test_transfer_resolves_username_and_numeric_id(target):
    fund(101,1000);fund(202,0)
    assert asyncio.run(tr.validate_recipient(bot_for_users(),101,target))==202


def test_username_lookup_does_not_create_registration_or_wallet():
    bot=bot_for_users()
    assert asyncio.run(identities.resolve_user(bot,'@recipient')).id==202
    assert not tr.registered_user(202)
    assert balance_service.get_user(202) is None
    with pytest.raises(tr.TransferError):asyncio.run(tr.validate_recipient(bot,101,'@recipient'))


def test_stale_username_cache_is_never_used():
    fund(101,1000);fund(202,0);fund(303,0)
    db.update_user_settings(202,{'username':'recycled'})
    bot=NS(get_entity=AsyncMock(return_value=User(id=303,username='recycled')))
    assert asyncio.run(tr.validate_recipient(bot,101,'@recycled'))==303
    bot.get_entity.assert_awaited_once_with('@recycled')


@pytest.mark.parametrize('entity',[User(id=101),User(id=202,deleted=True),User(id=202,bot=True),NS(id=202)])
def test_username_transfer_rejects_self_deleted_bot_and_channel(entity):
    fund(101,1000);fund(202,0)
    with pytest.raises(tr.TransferError):
        asyncio.run(tr.validate_recipient(NS(get_entity=AsyncMock(return_value=entity)),101,'@recipient'))
    assert balance_service.get_balance(101)==1000


def test_converter_allows_bot_but_transfer_does_not():
    fund(101,1000);fund(202,0)
    bot=NS(get_entity=AsyncMock(return_value=User(id=202,bot=True,username='test_bot')))
    assert asyncio.run(identities.resolve_user(bot,'@test_bot',allow_bots=True)).id==202
    with pytest.raises(tr.TransferError):asyncio.run(tr.validate_recipient(bot,101,'@test_bot'))


@pytest.mark.parametrize('value',['@123','-100','https://example.com/user','a b','@@user','x'*100,True])
def test_invalid_identity_input_never_calls_telegram(value):
    bot=NS(get_entity=AsyncMock())
    with pytest.raises(identities.IdentityError):asyncio.run(identities.resolve_user(bot,value))
    bot.get_entity.assert_not_awaited()


def test_private_transfer_binds_numeric_identity_after_username_resolution(monkeypatch):
    fund(101,1000);fund(202,0);fund(303,0)
    async def scenario(bot):
        cb=bot.handlers['cb_handler'];msg=bot.handlers['msg_handler']
        bot.get_entity.side_effect=lambda target:User(id=202,username='recipient') if type(target)is str else User(id=target)
        await cb(Event(bot,data=b'transfer_menu'));await msg(Event(bot,text='@recipient'))
        assert core.flow_states[101]['target']==202
        await msg(Event(bot,text='100'))
        key=core.flow_states[101]['request_id']
        bot.get_entity.side_effect=lambda target:User(id=303) if type(target)is str else User(id=target)
        await cb(Event(bot,data=f'tr_confirm_{key}'.encode()))
        assert balance_service.get_balance(202)==90 and balance_service.get_balance(303)==0
    run(monkeypatch,scenario)


def test_real_main_menu_converter_lookup_and_cancel(monkeypatch):
    async def scenario(bot):
        cb=bot.handlers['cb_handler'];msg=bot.handlers['msg_handler']
        bot.get_entity.side_effect=lambda value:User(id=202,username='recipient')
        await cb(Event(bot,data=b'username_lookup'))
        event=Event(bot,text='recipient');await msg(event)
        assert '202' in event.reply.call_args.args[0]
        assert balance_service.get_user(202) is None
        await msg(Event(bot,text='لغو'))
        assert 101 not in core.flow_states
    run(monkeypatch,scenario)


def test_group_transfer_fees_and_duplicate_command():
    fund(101,1000);fund(202,0)
    event=group_event();controller=TransferController(bot_for_users())
    assert asyncio.run(controller.handle_message(event))
    assert asyncio.run(controller.handle_message(event))
    assert (balance_service.get_balance(101),balance_service.get_balance(202))==(900,90)
    assert 'برداشت دوباره' in event.reply.call_args.args[0]
    assert 'موجودی' not in event.reply.call_args.args[0]
    assert db.get_global()['stats']['fees_collected']==10
    with connect() as conn:assert conn.execute('SELECT COUNT(*) FROM wallet_transfers').fetchone()[0]==1


def test_twenty_simultaneous_group_updates_debit_once():
    fund(101,1000);fund(202,0)
    controller=TransferController(bot_for_users())
    async def scenario():
        await asyncio.gather(*(controller.handle_message(group_event()) for _ in range(20)))
    asyncio.run(scenario())
    assert (balance_service.get_balance(101),balance_service.get_balance(202))==(900,90)


def test_changed_amount_or_reply_cannot_reuse_message_to_charge_again():
    fund(101,1000);fund(202,0);fund(303,0)
    controller=TransferController(bot_for_users())
    for event in [group_event(),group_event(text='انتقال 200'),group_event(target=303)]:
        asyncio.run(controller.handle_message(event))
    assert (balance_service.get_balance(101),balance_service.get_balance(202),balance_service.get_balance(303))==(900,90,0)


@pytest.mark.parametrize('text',['انتقال -100','انتقال 0','انتقال 9','انتقال 10.5','انتقال 1e3','انتقال nan','انتقال '+'9'*100,'انتقال 100 extra','انتقال'])
def test_invalid_group_amounts_never_debit(text):
    fund(101,1000);fund(202,0)
    asyncio.run(TransferController(bot_for_users()).handle_message(group_event(text=text)))
    assert balance_service.get_balance(101)==1000


def test_persian_digits_group_command():
    fund(101,1000);fund(202,0)
    asyncio.run(TransferController(bot_for_users()).handle_message(group_event(text='انتقال ۱۰۰')))
    assert balance_service.get_balance(202)==90


@pytest.mark.parametrize('case',['no_reply','missing_reply','external_reply','anonymous_sender','bot_sender','channel_target','bot_target','deleted_target','self','unregistered_target','forwarded_command'])
def test_group_target_and_author_guards(case):
    fund(101,1000);fund(202,0)
    event=group_event()
    if case=='no_reply':event.is_reply=False
    if case=='missing_reply':event.get_reply_message.return_value=None
    if case=='external_reply':event.get_reply_message.return_value.chat_id=-100777
    if case=='anonymous_sender':event.get_sender.return_value=NS(id=101)
    if case=='bot_sender':event.get_sender.return_value=User(id=101,bot=True)
    if case=='channel_target':event.get_reply_message.return_value.get_sender.return_value=NS(id=202)
    if case=='bot_target':event.get_reply_message.return_value.get_sender.return_value=User(id=202,bot=True)
    if case=='deleted_target':event.get_reply_message.return_value.get_sender.return_value=User(id=202,deleted=True)
    if case=='self':event=group_event(target=101)
    if case=='unregistered_target':event=group_event(target=999)
    if case=='forwarded_command':event.fwd_from=NS()
    asyncio.run(TransferController(bot_for_users()).handle_message(event))
    assert balance_service.get_balance(101)==1000


def test_group_insufficient_balance_and_membership(monkeypatch):
    fund(101,50);fund(202,0)
    asyncio.run(TransferController(bot_for_users()).handle_message(group_event()))
    assert balance_service.get_balance(101)==50
    fund(101,1000)
    monkeypatch.setattr(group_module,'check_required_memberships',AsyncMock(return_value=(False,[])))
    asyncio.run(TransferController(bot_for_users()).handle_message(group_event()))
    assert balance_service.get_balance(101)==1000


def test_post_commit_receipt_failure_cannot_replay_transfer():
    fund(101,1000);fund(202,0)
    event=group_event();event.reply.side_effect=OSError('Telegram unavailable')
    controller=TransferController(bot_for_users())
    asyncio.run(controller.handle_message(event));asyncio.run(controller.handle_message(group_event()))
    assert balance_service.get_balance(101)==900


def test_real_group_handler_routes_transfer(monkeypatch):
    fund(101,1000);fund(202,0)
    async def scenario(bot):
        await bot.handlers['game_group_handler'](group_event())
        assert balance_service.get_balance(202)==90
    run(monkeypatch,scenario)
