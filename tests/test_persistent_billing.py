"""Real SQLite acceptance tests: durable dues, not elapsed task runtime."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib
from datetime import datetime, timezone

import pytest
import db
from database import models
from services import usage_service as usage, balance_service as wallet, trial_manager
from services.transaction_service import connect
from test_security_runtime import fund


@pytest.fixture
def clock(monkeypatch):
    now = [1_800_000_000.0]
    monkeypatch.setattr(usage.time, 'time', lambda: now[0])
    return now


def state(uid=101):
    with connect() as conn:
        row = conn.execute('SELECT * FROM billing_clocks WHERE user_id=?', (uid,)).fetchone()
        return dict(row) if row else None


def raw_balance(uid=101):
    with connect() as conn:
        return conn.execute('SELECT diamonds FROM users WHERE telegram_id=?', (uid,)).fetchone()[0]


def charges():
    with connect() as conn:
        return [tuple(row) for row in conn.execute("SELECT amount,balance_before,balance_after FROM diamond_transactions WHERE description LIKE 'HALF_HOUR_USAGE:%'")]


def test_half_hour_boundaries_restart_and_offline_catchup(clock):
    fund(101, 20)
    usage.enroll_verified(101)
    start = clock[0]
    clock[0] += 1799.999
    assert usage.reconcile_all() == []
    clock[0] = start + 1800
    assert usage.reconcile_all()[0]['deducted'] == 1
    assert state()['next_due'] == start + 3600
    clock[0] = start + 5400 + 17  # simulated process downtime over two more dues
    importlib.reload(usage)  # no module state carries a timer
    assert usage.reconcile_all()[0]['deducted'] == 2
    assert raw_balance() == 17
    assert state()['next_due'] == start + 7200
    assert usage.reconcile_all() == []
    assert charges() == [(-1, 20, 19), (-2, 19, 17)]


def test_new_wallet_not_billed_without_verified_session(clock):
    fund(101, 20)
    clock[0] += 100000
    usage.reconcile_all()
    assert state() is None
    assert wallet.get_balance(101) == 20


def test_migration_consumes_legacy_carry_once_but_no_unknown_downtime(clock):
    fund(101, 20)
    db.update_user_settings(101, {'bill_seconds': 3590})
    usage.enroll_verified(101)
    assert raw_balance() == 19
    assert state()['next_due'] == clock[0] + 10
    usage.enroll_verified(101)
    assert raw_balance() == 19
    clock[0] += 10
    usage.reconcile_all()
    assert raw_balance() == 18


def test_sqlite_carry_wins_over_stale_legacy_json(clock):
    fund(101, 20)
    db.update_user_settings(101, {'bill_seconds': 3590})
    with connect() as conn:
        conn.execute('INSERT INTO wallet_usage VALUES (101, 100)')
    usage.enroll_verified(101)
    assert raw_balance() == 20
    assert state()['next_due'] == clock[0] + 1700


def test_manual_pause_and_restart_retain_partial_period(clock):
    fund(101, 20)
    usage.enroll_verified(101)
    clock[0] += 1700
    db.update_user_settings(101, {'self_enabled': False})
    clock[0] += 10000
    usage.enroll_verified(101)  # reconnect while manually disabled
    assert usage.reconcile_all() == []
    db.update_user_settings(101, {'self_enabled': True})
    assert state()['next_due'] == clock[0] + 100
    clock[0] += 100
    assert usage.reconcile_all()[0]['deducted'] == 1


def test_zero_exhaustion_no_debt_and_topup_starts_new_period(clock):
    fund(101, 2)
    usage.enroll_verified(101)
    clock[0] += 86400 * 30
    result = usage.reconcile_all()[0]
    assert result['deducted'] == 2 and not result['enabled']
    assert raw_balance() == 0 and state()['next_due'] is None
    clock[0] += 86400
    wallet.admin_adjust(101, 10)
    assert state()['next_due'] == clock[0] + 1800
    assert raw_balance() == 10


def test_spending_wallet_to_zero_does_not_reset_partial_period(clock):
    fund(101, 10)
    usage.enroll_verified(101)
    clock[0] += 1700
    wallet.admin_adjust(101, -10)
    assert state()['next_due'] is None
    clock[0] += 50000
    usage.enroll_verified(101)
    wallet.admin_adjust(101, 10)
    assert state()['next_due'] == clock[0] + 100
    clock[0] += 100
    assert usage.reconcile_all()[0]['deducted'] == 1


def test_due_is_settled_before_payment_or_transfer_mutates_balance(clock):
    fund(101, 1)
    usage.enroll_verified(101)
    clock[0] += 3600
    wallet.admin_adjust(101, 20)
    # Original 1 diamond is exhausted before top-up. No retroactive debt.
    assert raw_balance() == 20
    assert charges() == [(-1, 1, 0)]
    assert state()['next_due'] == clock[0] + 1800


def test_reading_wallet_enforces_due_without_waiting_for_scheduler(clock):
    fund(101, 10)
    usage.enroll_verified(101)
    clock[0] += 1800
    assert wallet.get_balance(101) == 9
    assert usage.reconcile_all() == []


def test_backwards_clock_and_repeated_restarts_never_double_charge(clock):
    fund(101, 10)
    usage.enroll_verified(101)
    start = clock[0]
    clock[0] += 1800
    usage.reconcile_all()
    clock[0] -= 4000
    usage.enroll_verified(101)
    assert raw_balance() == 9
    assert state()['next_due'] == start + 3600
    clock[0] = start + 3600
    usage.reconcile_all()
    assert raw_balance() == 8


def test_clock_wallet_ledger_rollback_together_on_write_failure(clock):
    fund(101, 10)
    usage.enroll_verified(101)
    before = state()
    clock[0] += 5400
    with connect() as conn:
        conn.execute("CREATE TRIGGER fail_clock BEFORE UPDATE ON billing_clocks BEGIN SELECT RAISE(ABORT,'clock fail'); END")
    with pytest.raises(Exception, match='clock fail'):
        usage.reconcile_all()
    assert state() == before and raw_balance() == 10 and charges() == []
    with connect() as conn:
        conn.execute('DROP TRIGGER fail_clock')
    assert usage.reconcile_all()[0]['deducted'] == 3


def test_twenty_concurrent_reconcilers_charge_once(clock):
    fund(101, 100)
    usage.enroll_verified(101)
    clock[0] += 7200
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda _: usage.reconcile_all(), range(20)))
    assert sum(row['deducted'] for batch in results for row in batch) == 4
    assert charges() == [(-4, 100, 96)]


def test_trial_expiry_crossed_while_server_offline_excludes_free_hours(clock, monkeypatch):
    fund(101, 10)
    monkeypatch.setattr(trial_manager, '_utcnow', lambda: datetime.fromtimestamp(clock[0], timezone.utc))
    trial_manager.activate_after_login(101)
    usage.enroll_verified(101)
    start = clock[0]
    assert state()['next_due'] == start + 86400 + 1800
    clock[0] += 86400 + 3600 + 5
    # refresh may mark the trial inactive before billing sees it.
    assert not trial_manager.is_active(101)
    assert usage.reconcile_all()[0]['deducted'] == 2
    assert state()['next_due'] == start + 86400 + 5400


def test_trial_grant_and_revocation_change_billable_interval(clock, monkeypatch):
    fund(101, 10)
    monkeypatch.setattr(trial_manager, '_utcnow', lambda: datetime.fromtimestamp(clock[0], timezone.utc))
    usage.enroll_verified(101)
    clock[0] += 1800
    trial_manager.grant_admin_trial(101, 1)
    assert raw_balance() == 9
    clock[0] += 300
    trial_manager.revoke(101)
    assert state()['next_due'] == clock[0] + 1800
    clock[0] += 1800
    assert usage.reconcile_all()[0]['deducted'] == 1


def test_admin_floor_zero_logs_actual_debit_after_pending_usage(clock):
    fund(101, 10)
    usage.enroll_verified(101)
    clock[0] += 1800
    assert wallet.admin_adjust(101, -100, floor_zero=True, description='admin-test') == 0
    with connect() as conn:
        assert tuple(conn.execute("SELECT amount,balance_before,balance_after FROM diamond_transactions WHERE description='admin-test'").fetchone()) == (-9, 9, 0)
    assert charges() == [(-1, 10, 9)]
