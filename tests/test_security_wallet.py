import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import db
from database import models
from services import balance_service, transfer_service as tr, referral_service
from services.transaction_service import connect


def fund(uid, amount=100):
    db.touch_user(uid, first_name='Test')
    balance_service.set_balance_from_core(uid, amount)


def test_reported_phantom_id_does_not_create_wallet_or_debit():
    fund(101)
    for target in (48482837474, 8283747271910):
        with pytest.raises(tr.TransferError):
            tr.transfer(101, target, 10, 'a'*32)
        assert balance_service.get_user(target) is None
    assert balance_service.get_balance(101) == 100


def test_old_phantom_wallet_is_not_registration():
    fund(101)
    db.update_user_settings(48482837474, {'diamonds':9})
    with pytest.raises(tr.TransferError):
        tr.transfer(101, 48482837474, 10, 'a'*32)
    assert balance_service.get_balance(101) == 100


@pytest.mark.parametrize('value', [-10, 0, 9, True, 10.5, '1e3', 'nan', 'inf', '-10', '+10', '10.0', '1,000', '9'*300, 2**63])
def test_invalid_amounts_never_mutate(value):
    fund(101); fund(202, 0)
    with pytest.raises(tr.TransferError):
        tr.transfer(101, 202, value, 'a'*32)
    assert (balance_service.get_balance(101), balance_service.get_balance(202)) == (100, 0)


def test_transfer_fee_ledger_and_replay():
    fund(101); fund(202, 0)
    result = tr.transfer(101, 202, 10, 'a'*32)
    assert (result['amount'], result['fee'], result['received']) == (10,1,9)
    assert tr.transfer(101,202,10,'a'*32)['duplicate'] is True
    assert (balance_service.get_balance(101), balance_service.get_balance(202)) == (90,9)
    with connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM wallet_transfers').fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM diamond_transactions WHERE description LIKE 'WALLET_TRANSFER:%'").fetchone()[0] == 2


def test_insufficient_balance_self_and_overflow():
    fund(101,10); fund(202, tr.MAX_BALANCE)
    for target, amount in [(101,10),(202,11),(202,10)]:
        with pytest.raises(tr.TransferError):
            tr.transfer(101,target,amount,'a'*32)
    assert balance_service.get_balance(101) == 10


def test_failure_during_second_ledger_entry_rolls_back_everything():
    fund(101); fund(202,0)
    with connect() as conn:
        conn.execute("""CREATE TRIGGER fail_credit BEFORE INSERT ON diamond_transactions
           WHEN NEW.user_id=202 BEGIN SELECT RAISE(ABORT, 'simulated disk failure'); END""")
    with pytest.raises(Exception, match='simulated disk failure'):
        tr.transfer(101,202,10,'a'*32)
    assert (balance_service.get_balance(101),balance_service.get_balance(202)) == (100,0)
    with connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM wallet_transfers').fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM diamond_transactions WHERE description LIKE 'WALLET_TRANSFER:%'").fetchone()[0] == 0


def test_twenty_duplicate_confirmations_charge_once():
    fund(101); fund(202,0)
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda _: tr.transfer(101,202,10,'a'*32), range(20)))
    assert sum(not r['duplicate'] for r in results) == 1
    assert balance_service.get_balance(101) == 90


def test_competing_transfers_cannot_overspend():
    fund(101,10); fund(202,0)
    def attempt(i):
        try:
            tr.transfer(101,202,10,f'{i:032x}')
            return True
        except tr.TransferError:
            return False
    with ThreadPoolExecutor(max_workers=20) as pool:
        assert sum(pool.map(attempt, range(20))) == 1
    assert balance_service.get_balance(101) == 0
    assert balance_service.get_balance(202) == 9


def test_request_key_cannot_change_owner_or_amount():
    fund(101); fund(202,0); fund(303)
    tr.transfer(101,202,10,'a'*32)
    for sender, amount in [(303,10),(101,11)]:
        with pytest.raises(tr.TransferError):
            tr.transfer(sender,202,amount,'a'*32)


def test_live_recipient_check_rejects_deleted_bot_and_lookup_failure():
    from telethon.tl.types import User
    fund(101); fund(202)
    for entity in [User(id=202,deleted=True),User(id=202,bot=True),User(id=303),SimpleNamespace(id=202)]:
        with pytest.raises(tr.TransferError):
            asyncio.run(tr.validate_recipient(SimpleNamespace(get_entity=AsyncMock(return_value=entity)),101,'202'))
    with pytest.raises(tr.TransferError):
        asyncio.run(tr.validate_recipient(SimpleNamespace(get_entity=AsyncMock(side_effect=ValueError())),101,'202'))
    bot=SimpleNamespace(get_entity=AsyncMock(return_value=User(id=202)))
    assert asyncio.run(tr.validate_recipient(bot,101,'202')) == 202


def test_referral_reward_commits_only_once_even_without_json_flag():
    fund(101,0); fund(202,0)
    db.update_user_settings(202, {'referred_by':101})
    with ThreadPoolExecutor(max_workers=20) as pool:
        results=list(pool.map(lambda _: referral_service.claim(202),range(20)))
    assert sum(r is not None for r in results) == 1
    from config import REF_REWARD
    assert balance_service.get_balance(101) == REF_REWARD
    assert referral_service.claim(202) is None


def test_referral_fake_self_and_historical_claim_rejected():
    fund(101,0); fund(202,0)
    for patch in [{'referred_by':48482837474}, {'referred_by':202}, {'referred_by':101,'ref_credited':True}]:
        db.update_user_settings(202,patch)
        assert referral_service.claim(202) is None
    assert balance_service.get_balance(101) == 0


def test_wallet_database_error_cannot_succeed_in_json(monkeypatch):
    fund(101,100)
    def fail(*args, **kwargs):
        raise OSError('storage unavailable')
    monkeypatch.setattr(balance_service,'set_balance_from_core',fail)
    with pytest.raises(OSError):
        db.update_user_settings(101, {'diamonds':200})
    assert db._get_internal(101)['diamonds'] != 200
    assert balance_service.get_balance(101) == 100


@pytest.mark.parametrize('amount',[-1, True, float('nan'), 1.5, 2**63])
def test_wallet_rejects_invalid_balance_before_normalization(amount):
    fund(101)
    with pytest.raises(ValueError):
        db.update_user_settings(101, {'diamonds':amount})
    assert balance_service.get_balance(101) == 100
