from pathlib import Path

from database import models


def _use_temp_db(tmp_path: Path):
    models.TABCHI_DB_PATH = str(tmp_path / 'tabchi-test.sqlite3')
    models.init_tabchi_db()
    models.init_message_saver_db()


def test_save_settings_are_per_user(tmp_path):
    _use_temp_db(tmp_path)
    models.set_save_destination(1001, '@archive_a')
    models.set_save_status(1001, True)
    models.set_save_destination(2002, '@archive_b')

    a = models.get_save_settings(1001)
    b = models.get_save_settings(2002)
    assert a['status'] == 1 and a['destination'] == '@archive_a'
    assert b['status'] == 0 and b['destination'] == '@archive_b'


def test_deleted_messages_are_owner_scoped(tmp_path):
    _use_temp_db(tmp_path)
    models.cache_message_record(
        owner_id=1001, chat_id=55, sender_id=77, sender_username='alice',
        message_id=9, message_type='text', text='hello', caption=None,
        media_id=None, media_path=None, chat_title='Test',
        message_date='2026-09-11T00:00:00+00:00', extra_json='{}'
    )
    row = models.find_cached_deleted_candidates(owner_id=1001, message_id=9, chat_id=55)[0]
    models.record_deleted_message(row)

    assert len(models.list_deleted_messages(1001)) == 1
    assert models.list_deleted_messages(2002) == []
    assert models.get_deleted_message_stats(1001)['total'] == 1
    assert models.get_deleted_message_stats(2002)['total'] == 0


def test_ambiguous_delete_can_be_detected(tmp_path):
    _use_temp_db(tmp_path)
    for chat_id in (11, 22):
        models.cache_message_record(
            owner_id=1001, chat_id=chat_id, sender_id=88, sender_username=None,
            message_id=5, message_type='text', text=f'chat-{chat_id}', caption=None,
            media_id=None, media_path=None, chat_title=str(chat_id),
            message_date=None, extra_json='{}'
        )
    candidates = models.find_cached_deleted_candidates(owner_id=1001, message_id=5)
    assert {r['chat_id'] for r in candidates} == {11, 22}
    exact = models.find_cached_deleted_candidates(owner_id=1001, message_id=5, chat_id=22)
    assert len(exact) == 1 and exact[0]['chat_id'] == 22


def test_cache_cleanup_does_not_delete_history(tmp_path):
    _use_temp_db(tmp_path)
    models.cache_message_record(
        owner_id=1001, chat_id=55, sender_id=77, sender_username='alice',
        message_id=10, message_type='photo', text=None, caption='cap',
        media_id='media', media_path='/tmp/media', chat_title='Test',
        message_date=None, extra_json='{}'
    )
    row = models.find_cached_deleted_candidates(owner_id=1001, message_id=10, chat_id=55)[0]
    models.record_deleted_message(row)
    assert models.clear_message_cache(1001) == 1
    assert models.find_cached_deleted_candidates(owner_id=1001, message_id=10, chat_id=55) == []
    assert models.get_deleted_message_stats(1001)['total'] == 1
