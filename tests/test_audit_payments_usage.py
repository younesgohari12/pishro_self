import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
import db
from config import MIN_DIAMOND, PRICE_PER_DIAMOND, BILL_DIAMONDS_PER_HOUR
from database import models
from services import payment_service as pay, balance_service, usage_service, access_service, trial_manager, admin_manager
from services.transaction_service import connect
from test_security_runtime import fund,run,Event
import bot.core as core


@pytest.fixture
def payment(monkeypatch):
    monkeypatch.setattr(admin_manager,'ADMIN_ID',909)
    fund(101,0)
    return db.create_payment(101,MIN_DIAMOND,MIN_DIAMOND*PRICE_PER_DIAMOND)


def test_payment_credit_and_status_are_atomic_under_failure(payment):
    with connect() as conn:
        conn.execute("""CREATE TRIGGER fail_review BEFORE UPDATE ON wallet_payments
            BEGIN SELECT RAISE(ABORT,'simulated review failure'); END""")
    with pytest.raises(Exception,match='simulated review failure'):
        pay.review(payment,approve=True,reviewer_id=909)
    assert balance_service.get_balance(101)==0
    assert db.get_payment(payment)['status']=='pending'
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM diamond_transactions WHERE description LIKE 'PAYMENT:%'").fetchone()[0]==0
        conn.execute('DROP TRIGGER fail_review')
    pay.review(payment,approve=True,reviewer_id=909)
    assert balance_service.get_balance(101)==MIN_DIAMOND


def test_twenty_payment_approvals_credit_once(payment):
    with ThreadPoolExecutor(max_workers=20) as pool:
        results=list(pool.map(lambda _: pay.review(payment,approve=True,reviewer_id=909),range(20)))
    assert sum(not r['duplicate'] for r in results)==1
    assert balance_service.get_balance(101)==MIN_DIAMOND
    assert db.get_payment(payment)['status']=='approved'


def test_payment_rejection_and_unauthorized_review_never_credit(payment):
    with pytest.raises(pay.PaymentError):
        pay.review(payment,approve=True,reviewer_id=101)
    pay.review(payment,approve=False,reviewer_id=909)
    assert pay.review(payment,approve=True,reviewer_id=909)['duplicate']
    assert balance_service.get_balance(101)==0
    assert db.get_payment(payment)['status']=='rejected'


def test_pending_request_uniqueness_under_concurrency():
    fund(101,0)
    def create(_):
        try:return db.create_payment(101,MIN_DIAMOND,MIN_DIAMOND*PRICE_PER_DIAMOND)
        except pay.PaymentError:return None
    with ThreadPoolExecutor(max_workers=10) as pool:
        assert sum(r is not None for r in pool.map(create,range(10)))==1


@pytest.mark.parametrize('diamonds,total',[(0,40),(-1,-40),(True,40),(MIN_DIAMOND-1,40),(MIN_DIAMOND,1),(2**64,40)])
def test_payment_creation_validates_price_and_bounds(diamonds,total):
    with pytest.raises(pay.PaymentError):db.create_payment(101,diamonds,total)


def test_legacy_payments_import_once_preserves_approved_and_next_number(monkeypatch):
    monkeypatch.setattr(admin_manager,'ADMIN_ID',909)
    fund(101,0)
    records={'1234':{'uid':101,'diamonds':1000,'amount':40000,'status':'approved','created_at':'old','created_ts':1,'reviewed_at':'old'}}
    with open(db.PAYMENTS_FILE,'w') as handle:json.dump(records,handle)
    db.set_global({'next_pay_id':2000})
    assert db.get_payment(1234)['status']=='approved'
    assert pay.review(1234,approve=True,reviewer_id=909)['duplicate']
    assert balance_service.get_balance(101)==0
    assert db.create_payment(101,MIN_DIAMOND,MIN_DIAMOND*PRICE_PER_DIAMOND)==2000
    records['1234']['status']='pending'
    with open(db.PAYMENTS_FILE,'w') as handle:json.dump(records,handle)
    assert db.get_payment(1234)['status']=='approved'


def test_real_payment_handler_survives_statistics_failure(monkeypatch,payment):
    monkeypatch.setattr(core,'ADMIN_ID',909)
    monkeypatch.setattr(db,'add_stats',lambda *a,**k:(_ for _ in ()).throw(OSError('stats unavailable')))
    async def scenario(bot):
        cb=bot.handlers['cb_handler']
        await cb(Event(bot,uid=909,data=f'pay_ok_{payment}'.encode()))
        await cb(Event(bot,uid=909,data=f'pay_ok_{payment}'.encode()))
        assert balance_service.get_balance(101)==MIN_DIAMOND
        assert db.get_payment(payment)['status']=='approved'
    run(monkeypatch,scenario)


def test_usage_carry_and_charge_persist_without_json_write():
    fund(101,100)
    first=usage_service.accrue(101,3590)
    assert first['deducted']==1
    second=usage_service.accrue(101,20)
    assert second['deducted']==1 and second['seconds']==10
    assert usage_service.accrue(101,0)['deducted']==0
    assert balance_service.get_balance(101)==100-BILL_DIAMONDS_PER_HOUR


def test_usage_rollback_preserves_balance_and_carry():
    fund(101,100)
    usage_service.accrue(101,1790)
    with connect() as conn:
        conn.execute("CREATE TRIGGER fail_usage BEFORE UPDATE ON wallet_usage BEGIN SELECT RAISE(ABORT,'usage failure'); END")
    with pytest.raises(Exception,match='usage failure'):usage_service.accrue(101,20)
    assert balance_service.get_balance(101)==100
    with connect() as conn:
        assert conn.execute('SELECT seconds FROM wallet_usage WHERE user_id=101').fetchone()[0]==1790


def test_usage_actual_debit_and_runtime_stops_at_zero():
    fund(101,1)
    assert access_service.can_run(101)
    result=usage_service.accrue(101,3600)
    assert result['deducted']==1 and result['balance']==0 and not result['enabled']
    assert not access_service.can_run(101)


def test_trial_and_disabled_account_usage():
    fund(101,0)
    trial_manager.activate_after_login(101)
    assert access_service.can_run(101)
    assert usage_service.accrue(101,7200)['deducted']==0
    db.update_user_settings(101,{'self_enabled':False})
    assert not access_service.can_run(101)
    assert not usage_service.accrue(101,60)['enabled']
    trial_manager.revoke(101)
    db.update_user_settings(101,{'self_enabled':True})
    assert not access_service.can_run(101)


def test_session_start_rejects_empty_wallet_without_connecting(monkeypatch):
    import self_manager
    fund(101,0)
    mocked=AsyncMock()
    monkeypatch.setattr(self_manager,'get_session_string',mocked)
    assert asyncio.run(self_manager.start_session('user_101')) is False
    mocked.assert_not_called()


def test_nonfinite_elapsed_and_legacy_carry_do_not_crash():
    fund(101,100)
    for elapsed in [float('nan'),float('inf'),-1,True]:
        with pytest.raises(ValueError):usage_service.accrue(101,elapsed)
    db.update_user_settings(101,{'bill_seconds':float('inf')})
    assert usage_service.accrue(101,60)['seconds']==60


def test_scheduler_honors_full_server_wait(monkeypatch):
    from telethon import errors
    from services import scheduler
    from unittest.mock import Mock
    banner={'id':1,'user_id':101,'interval':5}
    monkeypatch.setattr(scheduler.models,'list_due_banners',Mock(side_effect=[[banner],asyncio.CancelledError()]))
    monkeypatch.setattr(scheduler,'can_run',lambda uid:True)
    monkeypatch.setattr(scheduler.self_manager,'get_client_for_user',lambda uid:NS(is_connected=lambda:True))
    monkeypatch.setattr(scheduler,'collect_banner_targets',AsyncMock(return_value=[{'chat_id':202,'title':'test'}]))
    monkeypatch.setattr(scheduler,'send_banner_to_target',AsyncMock(side_effect=errors.FloodWaitError(request=None,capture=40793)))
    defer=NS(call=__import__('unittest.mock',fromlist=['Mock']).Mock()).call
    monkeypatch.setattr(scheduler.models,'defer_banner',defer)
    monkeypatch.setattr(scheduler.models,'log_send',Mock())
    monkeypatch.setattr(scheduler.asyncio,'sleep',AsyncMock())
    with pytest.raises(asyncio.CancelledError):asyncio.run(scheduler.run_scheduler())
    defer.assert_called_once_with(1,101,40793)


def test_scheduler_never_sends_for_disabled_or_unfunded_account(monkeypatch):
    from services import scheduler
    from unittest.mock import Mock
    banner={'id':1,'user_id':101,'interval':5}
    monkeypatch.setattr(scheduler.models,'list_due_banners',Mock(side_effect=[[banner],asyncio.CancelledError()]))
    monkeypatch.setattr(scheduler,'can_run',lambda uid:False)
    sender=AsyncMock();monkeypatch.setattr(scheduler,'send_banner_to_target',sender)
    monkeypatch.setattr(scheduler.models,'defer_banner',Mock())
    monkeypatch.setattr(scheduler.asyncio,'sleep',AsyncMock())
    with pytest.raises(asyncio.CancelledError):asyncio.run(scheduler.run_scheduler())
    sender.assert_not_awaited()
