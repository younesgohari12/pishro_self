"""Exercise the real handlers registered by run_bot with Telegram mocked."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import db
import bot.core as core
import login_manager as lm
from services import balance_service
from telethon.tl.types import User


class Event:
    def __init__(self,bot,uid=101,text='',data=b''):
        self.client=bot; self.sender_id=uid; self.chat_id=uid; self.message_id=77
        self.sender=NS(id=uid,username='',first_name='Test',last_name='')
        self.is_private=True; self.text=text; self.raw_text=text; self.data=data; self.contact=None
        self.reply=AsyncMock(); self.answer=AsyncMock(); self.edit=AsyncMock(); self.delete=AsyncMock()
        self.get_sender=AsyncMock(return_value=self.sender)


class Bot:
    def __init__(self,scenario):
        self.handlers={};self.scenario=scenario
        self.start=AsyncMock();self.disconnect=AsyncMock()
        self.get_me=AsyncMock(return_value=NS(id=999,username='testbot'))
        self.send_message=AsyncMock(return_value=NS(id=77))
        self.get_entity=AsyncMock(side_effect=lambda uid:User(id=uid))
    def on(self,*args,**kwargs):
        def register(func):
            self.handlers[func.__name__]=func
            return func
        return register
    async def run_until_disconnected(self):
        await self.scenario(self)


def run(monkeypatch,scenario):
    bot=Bot(scenario)
    monkeypatch.setattr(core,'TelegramClient',lambda *args,**kwargs:bot)
    monkeypatch.setattr(core,'BOT_TOKEN','999:test')
    monkeypatch.setattr(core,'flow_states',{})
    asyncio.run(core.run_bot())


def fund(uid,amount):
    db.touch_user(uid,first_name='Test')
    balance_service.set_balance_from_core(uid,amount)


def test_real_transfer_handlers_reject_phantom_and_bind_confirmation(monkeypatch):
    fund(101,100);fund(202,0)
    async def scenario(bot):
        cb=bot.handlers['cb_handler']; msg=bot.handlers['msg_handler']
        await cb(Event(bot,data=b'transfer_menu'))
        await msg(Event(bot,text='48482837474'))
        assert core.flow_states[101]['step']=='TR_TARGET'
        await msg(Event(bot,text='202'))
        assert core.flow_states[101]['step']=='TR_AMOUNT'
        await msg(Event(bot,text='10'))
        key=core.flow_states[101]['request_id']
        await cb(Event(bot,data=b'tr_confirm'))
        assert balance_service.get_balance(101)==100
        await cb(Event(bot,data=f'tr_confirm_{key}'.encode()))
        assert (balance_service.get_balance(101),balance_service.get_balance(202))==(90,9)
        await cb(Event(bot,data=b'transfer_menu'))
        await msg(Event(bot,text='202'));await msg(Event(bot,text='10'))
        await cb(Event(bot,data=f'tr_confirm_{key}'.encode()))
        assert balance_service.get_balance(101)==90
        await msg(Event(bot,text='لغو'))
        assert 101 not in core.flow_states
    run(monkeypatch,scenario)


def test_cancel_while_final_recipient_check_is_inflight(monkeypatch):
    fund(101,100);fund(202,0)
    async def scenario(bot):
        cb=bot.handlers['cb_handler'];msg=bot.handlers['msg_handler']
        await cb(Event(bot,data=b'transfer_menu'))
        await msg(Event(bot,text='202'));await msg(Event(bot,text='10'))
        key=core.flow_states[101]['request_id']
        started=asyncio.Event();release=asyncio.Event()
        async def slow(uid):
            started.set();await release.wait();return User(id=uid)
        bot.get_entity.side_effect=slow
        task=asyncio.create_task(cb(Event(bot,data=f'tr_confirm_{key}'.encode())))
        await started.wait()
        await cb(Event(bot,data=f'tr_cancel_{key}'.encode()))
        release.set();await task
        assert balance_service.get_balance(101)==100
        assert balance_service.get_balance(202)==0
    run(monkeypatch,scenario)


def test_real_login_handler_blocks_manual_and_foreign_contact(monkeypatch):
    request=AsyncMock()
    monkeypatch.setattr(lm,'TelegramClient',request)
    monkeypatch.setattr(lm,'login_states',{})
    monkeypatch.setattr(core,'login_states',lm.login_states)
    async def scenario(bot):
        cb=bot.handlers['cb_handler'];msg=bot.handlers['msg_handler']
        await cb(Event(bot,data=b'install_self'))
        await msg(Event(bot,text='+989120000000'))
        contact=Event(bot);contact.contact=NS(user_id=202,phone_number='+989121234567')
        await msg(contact)
        request.assert_not_called()
        assert lm.login_states[101]['step']=='WAIT_PHONE'
    run(monkeypatch,scenario)


def test_referral_link_requires_registered_referrer_and_new_invitee(monkeypatch):
    fund(101,0);fund(202,0)
    async def scenario(bot):
        start=bot.handlers['start']
        await start(Event(bot,uid=202,text='/start ref_101'))
        assert db.get_user_settings(202)['referred_by']==0
        await start(Event(bot,uid=303,text='/start ref_48482837474'))
        assert db.get_user_settings(303)['referred_by']==0
        await start(Event(bot,uid=404,text='/start ref_101'))
        assert db.get_user_settings(404)['referred_by']==101
        from config import REF_REWARD
        assert balance_service.get_balance(101)==REF_REWARD
        await start(Event(bot,uid=404,text='/start ref_101'))
        assert balance_service.get_balance(101)==REF_REWARD
    run(monkeypatch,scenario)
