import os
import sqlite3
import tempfile
import unittest

from database import models


class TabchiModelsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = models.TABCHI_DB_PATH
        models.TABCHI_DB_PATH = os.path.join(self.tmp.name, 'tabchi.sqlite3')
        models.init_tabchi_db()

    def tearDown(self):
        models.TABCHI_DB_PATH = self.old
        self.tmp.cleanup()

    def _banner(self, uid=100, targets=('group', 'private')):
        return models.create_banner(
            user_id=uid, name='تبلیغ صبح', type='text', file_id=None,
            text='سلام', caption=None, send_mode='normal', send_target=targets,
            interval=5, status='active', source_peer='mainbot', source_message_id=77,
        )

    def test_owner_isolation(self):
        bid = self._banner(100)
        self.assertIsNotNone(models.get_banner(bid, 100))
        self.assertIsNone(models.get_banner(bid, 200))
        self.assertFalse(models.update_banner(bid, 200, name='هک'))
        self.assertFalse(models.delete_banner(bid, 200))
        self.assertEqual(models.get_banner(bid, 100)['name'], 'تبلیغ صبح')

    def test_send_target_multiselect(self):
        bid = self._banner(100, ('group', 'private'))
        banner = models.get_banner(bid, 100)
        self.assertEqual(models.get_banner_send_targets(banner), ['group', 'private'])
        self.assertTrue(models.update_banner(bid, 100, send_target=['private']))
        self.assertEqual(models.get_banner_send_targets(models.get_banner(bid, 100)), ['private'])
        with self.assertRaises(ValueError):
            models.update_banner(bid, 100, send_target=[])

    def test_blacklist_is_per_user(self):
        first = models.add_blacklist(user_id=100, target='@SomeUser', target_type='user')
        models.add_blacklist(user_id=200, target='@other', target_type='user')
        self.assertEqual(models.list_blacklist(100)[0]['target'], '@someuser')
        self.assertEqual(models.blacklist_count(100), 1)
        self.assertFalse(models.delete_blacklist(first, 200))
        self.assertTrue(models.delete_blacklist(first, 100))
        self.assertEqual(models.blacklist_count(100), 0)
        self.assertEqual(models.blacklist_count(200), 1)

    def test_blacklist_matching(self):
        models.add_blacklist(user_id=100, target='@blocked', target_type='user')
        self.assertTrue(models.target_is_blacklisted(100, entity_id=42, peer_id=42, username='Blocked'))
        self.assertFalse(models.target_is_blacklisted(100, entity_id=43, peer_id=43, username='allowed'))
        models.add_blacklist(user_id=100, target='-100123456', target_type='group')
        self.assertTrue(models.target_is_blacklisted(100, entity_id=123456, peer_id=-100123456, username=None))

    def test_status_and_due(self):
        bid = self._banner(100)
        models.update_banner(bid, 100, next_send_at=1)
        due = models.list_due_banners(now_ts=2)
        self.assertEqual([x['id'] for x in due], [bid])
        models.set_banner_status(bid, 100, 'paused')
        self.assertEqual(models.list_due_banners(now_ts=10**20), [])

    def test_logs_and_owner_isolation(self):
        bid = self._banner(100)
        models.log_send(
            banner_id=bid, user_id=100, target_chat_id=-1001,
            target_title='X', status='skipped_blacklist', telegram_message_id=None,
        )
        logs = models.list_send_logs(bid, 100)
        self.assertEqual(logs[0]['status'], 'skipped_blacklist')
        self.assertEqual(models.list_send_logs(bid, 200), [])

    def test_legacy_explicit_targets_still_safe(self):
        bid = self._banner(100)
        tid = models.add_target(
            banner_id=bid, user_id=100, chat_id=-100123, raw_id=123,
            access_hash=999, username='demo', title='Demo', kind='channel',
        )
        self.assertEqual(len(models.list_targets(bid, 100)), 1)
        self.assertFalse(models.delete_target(tid, bid, 200))
        self.assertTrue(models.delete_banner(bid, 100))
        self.assertEqual(models.list_targets(bid, 100), [])

    def test_v005_database_migration_adds_send_target(self):
        legacy_path = os.path.join(self.tmp.name, 'legacy.sqlite3')
        conn = sqlite3.connect(legacy_path)
        conn.execute('''CREATE TABLE banners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            file_id TEXT,
            text TEXT,
            caption TEXT,
            send_mode TEXT NOT NULL,
            interval INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            source_peer TEXT,
            source_message_id INTEGER,
            last_sent_at REAL,
            next_send_at REAL
        )''')
        conn.commit(); conn.close()
        models.TABCHI_DB_PATH = legacy_path
        models.init_tabchi_db()
        conn = sqlite3.connect(legacy_path)
        cols = {r[1] for r in conn.execute('PRAGMA table_info(banners)').fetchall()}
        conn.close()
        self.assertIn('send_target', cols)


if __name__ == '__main__':
    unittest.main()
