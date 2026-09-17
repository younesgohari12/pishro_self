from __future__ import annotations

import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone

from database import models
from services import balance_service, game_service
from services.game_parser import parse_bet
from services.transaction_service import connect


class GameServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = models.TABCHI_DB_PATH
        models.TABCHI_DB_PATH = os.path.join(self.tmp.name, 'game.sqlite3')
        models.init_game_db()
        models.update_game_settings(
            game_enabled=True, main_game_group='-100123456', game_tax=10,
            min_bet=100, max_bet=10000, expiration_seconds=600,
            prevent_multiple_open=True,
        )

    def tearDown(self):
        models.TABCHI_DB_PATH = self.old_path
        self.tmp.cleanup()

    def fund(self, uid: int, amount: int, username: str = ''):
        return balance_service.ensure_user(uid, username=username, initial_balance=amount)

    def create(self, uid=1, message_id=10, bet=100):
        self.fund(uid, 1000, f'u{uid}')
        return game_service.create_game(
            chat_id=-100123456, message_id=message_id, creator_id=uid,
            creator_username=f'u{uid}', bet_amount=bet,
        )

    def test_schema_and_persistent_settings(self):
        expected = {'users', 'games', 'diamond_transactions', 'game_settings'}
        with connect() as conn:
            tables = {r['name'] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue(expected.issubset(tables))
        models.update_game_settings(game_tax=17, expiration_seconds=1200)
        models.init_game_db()
        settings = models.get_game_settings()
        self.assertEqual(settings['game_tax'], 17)
        self.assertEqual(settings['expiration_seconds'], 1200)

    def test_core_wallet_gateway_logs_every_balance_change(self):
        self.assertEqual(balance_service.set_balance_from_core(99, 250), 250)
        self.assertEqual(balance_service.set_balance_from_core(99, 175), 175)
        txs = list(reversed(models.list_diamond_transactions(99)))
        self.assertEqual([t['type'] for t in txs], ['ADMIN_ADD', 'ADMIN_REMOVE'])
        self.assertEqual([t['amount'] for t in txs], [250, -75])
        self.assertEqual(txs[-1]['balance_after'], 175)

    def test_create_reserves_integer_stake_and_logs_it(self):
        game = self.create()
        self.assertEqual(game['status'], 'OPEN')
        self.assertEqual(balance_service.get_balance(1), 900)
        txs = models.list_diamond_transactions(1)
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]['type'], 'GAME_STAKE')
        self.assertEqual(txs[0]['amount'], -100)

    def test_creator_cannot_join_own_game(self):
        game = self.create()
        with self.assertRaises(game_service.OwnGame):
            game_service.join_game(
                game_id=game['id'], opponent_id=1, opponent_username='u1',
                chat_id=game['chat_id'], message_id=game['message_id'],
            )
        self.assertEqual(models.get_game(game['id'])['status'], 'OPEN')
        self.assertEqual(balance_service.get_balance(1), 900)

    def test_insufficient_opponent_is_rejected_without_mutation(self):
        game = self.create()
        self.fund(2, 70, 'u2')
        with self.assertRaises(game_service.InsufficientBalance) as caught:
            game_service.join_game(
                game_id=game['id'], opponent_id=2, opponent_username='u2',
                chat_id=game['chat_id'], message_id=game['message_id'],
            )
        self.assertEqual(caught.exception.balance, 70)
        self.assertEqual(balance_service.get_balance(2), 70)
        self.assertEqual(models.get_game(game['id'])['status'], 'OPEN')

    def test_callback_binding_is_checked(self):
        game = self.create()
        self.fund(2, 100)
        with self.assertRaises(game_service.CallbackMismatch):
            game_service.join_game(
                game_id=game['id'], opponent_id=2, opponent_username='u2',
                chat_id=game['chat_id'], message_id=999,
            )
        self.assertEqual(balance_service.get_balance(2), 100)

    def test_twenty_simultaneous_joins_allow_exactly_one(self):
        game = self.create(uid=1, message_id=20)
        contenders = list(range(2, 22))
        for uid in contenders:
            self.fund(uid, 100, f'u{uid}')
        barrier = threading.Barrier(len(contenders))
        successes = []
        failures = []

        def join(uid):
            barrier.wait()
            try:
                result = game_service.join_game(
                    game_id=game['id'], opponent_id=uid, opponent_username=f'u{uid}',
                    chat_id=game['chat_id'], message_id=game['message_id'],
                )
                successes.append(result)
            except game_service.GameError as exc:
                failures.append(exc.code)

        threads = [threading.Thread(target=join, args=(uid,)) for uid in contenders]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 19)
        final = models.get_game(game['id'])
        self.assertEqual(final['status'], 'FINISHED')
        self.assertIn(final['opponent_id'], contenders)
        with connect() as conn:
            txs = conn.execute('SELECT type,COUNT(*) AS c FROM diamond_transactions WHERE game_id=? GROUP BY type', (game['id'],)).fetchall()
            total = conn.execute('SELECT SUM(diamonds) AS n FROM users').fetchone()['n']
        counts = {row['type']: row['c'] for row in txs}
        self.assertEqual(counts, {'GAME_STAKE': 2, 'GAME_WIN': 1})
        self.assertEqual(total, 2980)  # 3000 initial - 20 tax

    def test_double_join_never_pays_twice(self):
        game = self.create()
        self.fund(2, 100)
        game_service.join_game(
            game_id=game['id'], opponent_id=2, opponent_username='u2',
            chat_id=game['chat_id'], message_id=game['message_id'],
        )
        with self.assertRaises(game_service.GameUnavailable):
            game_service.join_game(
                game_id=game['id'], opponent_id=2, opponent_username='u2',
                chat_id=game['chat_id'], message_id=game['message_id'],
            )
        with connect() as conn:
            wins = conn.execute("SELECT COUNT(*) AS c FROM diamond_transactions WHERE game_id=? AND type='GAME_WIN'", (game['id'],)).fetchone()['c']
        self.assertEqual(wins, 1)

    def test_cancel_refunds_once(self):
        game = self.create()
        result = game_service.cancel_game(
            game_id=game['id'], creator_id=1,
            chat_id=game['chat_id'], message_id=game['message_id'],
        )
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertEqual(balance_service.get_balance(1), 1000)
        with self.assertRaises(game_service.GameUnavailable):
            game_service.cancel_game(
                game_id=game['id'], creator_id=1,
                chat_id=game['chat_id'], message_id=game['message_id'],
            )
        refunds = [t for t in models.list_diamond_transactions(1) if t['type'] == 'GAME_REFUND']
        self.assertEqual(len(refunds), 1)

    def test_expiration_refunds_and_is_idempotent(self):
        game = self.create()
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec='seconds')
        with connect() as conn:
            conn.execute('UPDATE games SET expires_at=? WHERE id=?', (past, game['id']))
            conn.commit()
        self.assertEqual(len(game_service.expire_due_games()), 1)
        self.assertEqual(len(game_service.expire_due_games()), 0)
        self.assertEqual(balance_service.get_balance(1), 1000)
        self.assertEqual(models.get_game(game['id'])['status'], 'EXPIRED')

    def test_tax_is_snapshotted_per_game(self):
        models.update_game_settings(game_tax=10)
        game = self.create()
        models.update_game_settings(game_tax=30)
        self.fund(2, 100)
        final = game_service.join_game(
            game_id=game['id'], opponent_id=2, opponent_username='u2',
            chat_id=game['chat_id'], message_id=game['message_id'],
        )
        self.assertEqual(final['tax_percent'], 10)
        self.assertEqual(final['tax_amount'], 20)
        self.assertEqual(final['reward_amount'], 180)

    def test_persian_and_arabic_bet_parser(self):
        self.assertEqual(parse_bet('بازی ۱٬۰۰۰'), 1000)
        self.assertEqual(parse_bet('شرط بندی 250'), 250)
        self.assertEqual(parse_bet('شرطبندی ٣٠٠'), 300)
        self.assertIsNone(parse_bet('بازی -100'))


if __name__ == '__main__':
    unittest.main()
