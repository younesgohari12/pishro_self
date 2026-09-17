import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

from database import models
from services import admin_manager, trial_manager


def _with_temp_db():
    tmp = tempfile.TemporaryDirectory()
    old = models.TABCHI_DB_PATH
    models.TABCHI_DB_PATH = os.path.join(tmp.name, 'suite.sqlite3')
    models.init_membership_support_db()
    return tmp, old


def test_trial_is_one_time_and_owner_scoped():
    tmp, old = _with_temp_db()
    try:
        ok, row = trial_manager.activate_after_login(
            101, username='u1', first_name='A', last_name='B', phone='+100000000'
        )
        assert ok is True
        assert row['used'] == 1 and row['active'] == 1
        assert trial_manager.is_active(101) is True
        assert trial_manager.revoke(101) is True
        assert trial_manager.is_active(101) is False
        ok2, _ = trial_manager.activate_after_login(101, username='u1')
        assert ok2 is False
        assert models.get_trial(202) is None
    finally:
        models.TABCHI_DB_PATH = old
        tmp.cleanup()


def test_trial_expiration_flips_active_only():
    tmp, old = _with_temp_db()
    try:
        models.ensure_trial_row(303, username='late')
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec='seconds')
        with models._lock, models._conn() as conn:
            conn.execute("UPDATE free_trial SET used=1,active=1,expire_time=? WHERE user_id=?", (past, 303))
            conn.commit()
        expired = trial_manager.expire_due_trials()
        assert 303 in expired
        row = models.get_trial(303)
        assert row['used'] == 1 and row['active'] == 0
    finally:
        models.TABCHI_DB_PATH = old
        tmp.cleanup()


def test_tickets_and_messages_are_persisted():
    tmp, old = _with_temp_db()
    try:
        models.touch_profile(11, username='alice', first_name='Alice')
        tid = models.create_ticket(11)
        mid = models.add_ticket_message(
            tid, sender_role='user', sender_id=11,
            message_type='photo', text='caption', telegram_message_id=55,
        )
        assert mid > 0
        assert models.get_ticket(tid)['user_id'] == 11
        assert models.ticket_count_for_user(11) == 1
        assert models.close_ticket(tid, 999) is True
        assert models.get_ticket(tid)['status'] == 'closed'
    finally:
        models.TABCHI_DB_PATH = old
        tmp.cleanup()


def test_support_contacts_add_remove():
    tmp, old = _with_temp_db()
    try:
        cid = models.add_support_contact(
            target='@support', target_id=777, username='support',
            display_name='Support', created_by=1,
        )
        rows = models.list_support_contacts()
        assert len(rows) == 1 and rows[0]['target_id'] == 777
        assert models.delete_support_contact(cid) is True
        assert models.list_support_contacts() == []
    finally:
        models.TABCHI_DB_PATH = old
        tmp.cleanup()


def test_limited_admin_permissions_are_json_and_enforced():
    tmp, old = _with_temp_db()
    try:
        aid = models.upsert_admin(
            user_id=555, username='helper', role='limited',
            permissions=['answer_tickets', 'view_users'],
        )
        row = models.get_admin_by_user(555)
        assert row['role'] == 'limited'
        assert json.loads(row['permissions']) == ['view_users', 'answer_tickets']
        assert admin_manager.has_permission(555, 'answer_tickets') is True
        assert admin_manager.has_permission(555, 'manage_admins') is False
        assert models.delete_admin(aid) is True
    finally:
        models.TABCHI_DB_PATH = old
        tmp.cleanup()


def test_full_admin_has_all_permissions():
    tmp, old = _with_temp_db()
    try:
        models.upsert_admin(user_id=556, username='full', role='full', permissions=[])
        for perm in admin_manager.ALL_PERMISSIONS:
            assert admin_manager.has_permission(556, perm)
    finally:
        models.TABCHI_DB_PATH = old
        tmp.cleanup()


def test_profile_fields_keep_registered_at_and_update_last_activity():
    tmp, old = _with_temp_db()
    try:
        first = models.touch_profile(42, username='x', registered_at='2024-01-01 00:00:00')
        second = models.touch_profile(42, username='y', first_name='Y')
        assert first['registered_at'] == '2024-01-01 00:00:00'
        assert second['registered_at'] == '2024-01-01 00:00:00'
        assert second['username'] == 'y'
    finally:
        models.TABCHI_DB_PATH = old
        tmp.cleanup()
