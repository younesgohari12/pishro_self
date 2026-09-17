"""Transactional two-player virtual-diamond game engine."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from database import models
from services import balance_service
from services.transaction_service import begin_immediate, connect, ledger_entry


class GameError(Exception):
    code = 'GAME_ERROR'


class GameDisabled(GameError):
    code = 'GAME_DISABLED'


class InvalidBet(GameError):
    code = 'INVALID_BET'


class InsufficientBalance(GameError):
    code = 'INSUFFICIENT_BALANCE'

    def __init__(self, required: int, balance: int):
        super().__init__('insufficient balance')
        self.required = int(required)
        self.balance = int(balance)


class ActiveGameExists(GameError):
    code = 'ACTIVE_GAME_EXISTS'


class GameUnavailable(GameError):
    code = 'GAME_UNAVAILABLE'


class OwnGame(GameError):
    code = 'OWN_GAME'


class NotCreator(GameError):
    code = 'NOT_CREATOR'


class CallbackMismatch(GameError):
    code = 'CALLBACK_MISMATCH'


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec='seconds')


def _fetch_game(conn, game_id: int) -> dict[str, Any] | None:
    row = conn.execute('SELECT * FROM games WHERE id=?', (int(game_id),)).fetchone()
    return dict(row) if row else None


def create_game(
    *,
    chat_id: int,
    message_id: int,
    creator_id: int,
    creator_username: str | None,
    bet_amount: int,
) -> dict[str, Any]:
    bet = int(bet_amount)
    uid = int(creator_id)
    balance_service.ensure_user(uid, username=creator_username)
    now = _now_dt()

    with models._lock, connect() as conn:
        begin_immediate(conn)
        settings_row = conn.execute('SELECT * FROM game_settings WHERE id=1').fetchone()
        settings = dict(settings_row)
        if not int(settings.get('game_enabled') or 0):
            conn.rollback()
            raise GameDisabled()
        minimum = int(settings['min_bet'])
        maximum = int(settings['max_bet'])
        if bet < minimum or bet > maximum:
            conn.rollback()
            raise InvalidBet()
        expires = now + timedelta(seconds=int(settings['expiration_seconds']))
        if int(settings.get('prevent_multiple_open') or 0):
            active = conn.execute(
                '''SELECT 1 FROM games
                   WHERE creator_id=? AND status IN ('OPEN','LOCKED') LIMIT 1''',
                (uid,)
            ).fetchone()
            if active:
                conn.rollback()
                raise ActiveGameExists()
        wallet = conn.execute('SELECT diamonds FROM users WHERE telegram_id=?', (uid,)).fetchone()
        current = int(wallet['diamonds']) if wallet else 0
        if current < bet:
            conn.rollback()
            raise InsufficientBalance(bet, current)
        cur = conn.execute(
            '''INSERT INTO games
               (chat_id,message_id,creator_id,creator_username,bet_amount,tax_percent,
                tax_amount,reward_amount,status,created_at,expires_at)
               VALUES (?,?,?,?,?,?,0,0,'OPEN',?,?)''',
            (
                int(chat_id), int(message_id), uid, (creator_username or '').lstrip('@'),
                bet, int(settings['game_tax']), _iso(now), _iso(expires),
            )
        )
        game_id = int(cur.lastrowid)
        after = current - bet
        changed = conn.execute(
            'UPDATE users SET diamonds=?,updated_at=? WHERE telegram_id=? AND diamonds>=?',
            (after, _iso(now), uid, bet)
        )
        if changed.rowcount != 1:
            conn.rollback()
            raise InsufficientBalance(bet, current)
        ledger_entry(
            conn, user_id=uid, tx_type='GAME_STAKE', amount=-bet,
            before=current, after=after, game_id=game_id,
            description='Creator stake reserved', created_at=_iso(now),
        )
        conn.commit()
        return _fetch_game(conn, game_id)


def join_game(
    *,
    game_id: int,
    opponent_id: int,
    opponent_username: str | None,
    chat_id: int,
    message_id: int,
) -> dict[str, Any]:
    opponent = int(opponent_id)
    balance_service.ensure_user(opponent, username=opponent_username)
    now = _now_dt()
    now_iso = _iso(now)
    with models._lock, connect() as conn:
        begin_immediate(conn)
        game = _fetch_game(conn, game_id)
        if not game:
            conn.rollback(); raise GameUnavailable()
        if int(game['chat_id']) != int(chat_id) or int(game['message_id']) != int(message_id):
            conn.rollback(); raise CallbackMismatch()
        if int(game['creator_id']) == opponent:
            conn.rollback(); raise OwnGame()
        if game['status'] != models.GAME_OPEN:
            conn.rollback(); raise GameUnavailable()
        if datetime.fromisoformat(game['expires_at']) <= now:
            _refund_open_game(conn, game, models.GAME_EXPIRED, now_iso)
            conn.commit(); raise GameUnavailable()

        # Creator reservation is re-verified inside the same write transaction.
        reservation = conn.execute(
            '''SELECT 1 FROM diamond_transactions
               WHERE game_id=? AND user_id=? AND type='GAME_STAKE' AND amount=? LIMIT 1''',
            (int(game_id), int(game['creator_id']), -int(game['bet_amount']))
        ).fetchone()
        if reservation is None:
            conn.rollback(); raise GameUnavailable()

        bet = int(game['bet_amount'])
        wallet = conn.execute('SELECT diamonds FROM users WHERE telegram_id=?', (opponent,)).fetchone()
        before = int(wallet['diamonds']) if wallet else 0
        if before < bet:
            conn.rollback(); raise InsufficientBalance(bet, before)

        locked = conn.execute(
            "UPDATE games SET status='LOCKED',opponent_id=?,opponent_username=?,joined_at=? WHERE id=? AND status='OPEN'",
            (opponent, (opponent_username or '').lstrip('@'), now_iso, int(game_id))
        )
        if locked.rowcount != 1:
            conn.rollback(); raise GameUnavailable()

        after = before - bet
        debit = conn.execute(
            'UPDATE users SET diamonds=?,updated_at=? WHERE telegram_id=? AND diamonds>=?',
            (after, now_iso, opponent, bet)
        )
        if debit.rowcount != 1:
            conn.rollback(); raise InsufficientBalance(bet, before)
        ledger_entry(
            conn, user_id=opponent, tx_type='GAME_STAKE', amount=-bet,
            before=before, after=after, game_id=int(game_id),
            description='Opponent game stake', created_at=now_iso,
        )

        creator = int(game['creator_id'])
        winner = int(secrets.choice((creator, opponent)))
        loser = opponent if winner == creator else creator
        gross = bet * 2
        tax = gross * int(game['tax_percent']) // 100
        reward = gross - tax
        winner_row = conn.execute('SELECT diamonds FROM users WHERE telegram_id=?', (winner,)).fetchone()
        winner_before = int(winner_row['diamonds'])
        winner_after = winner_before + reward
        conn.execute(
            'UPDATE users SET diamonds=?,updated_at=? WHERE telegram_id=?',
            (winner_after, now_iso, winner)
        )
        ledger_entry(
            conn, user_id=winner, tx_type='GAME_WIN', amount=reward,
            before=winner_before, after=winner_after, game_id=int(game_id),
            description='Game winner reward after tax', created_at=now_iso,
        )
        conn.execute(
            '''UPDATE games SET status='FINISHED',tax_amount=?,reward_amount=?,
               winner_id=?,loser_id=?,finished_at=? WHERE id=? AND status='LOCKED' ''',
            (tax, reward, winner, loser, now_iso, int(game_id))
        )
        conn.commit()
        return _fetch_game(conn, game_id)


def _refund_open_game(conn, game: dict[str, Any], status: str, now_iso: str) -> None:
    if game['status'] != models.GAME_OPEN:
        raise GameUnavailable()
    uid = int(game['creator_id'])
    bet = int(game['bet_amount'])
    row = conn.execute('SELECT diamonds FROM users WHERE telegram_id=?', (uid,)).fetchone()
    before = int(row['diamonds'])
    after = before + bet
    changed = conn.execute(
        'UPDATE games SET status=?,finished_at=? WHERE id=? AND status=?',
        (status, now_iso, int(game['id']), models.GAME_OPEN)
    )
    if changed.rowcount != 1:
        raise GameUnavailable()
    conn.execute(
        'UPDATE users SET diamonds=?,updated_at=? WHERE telegram_id=?',
        (after, now_iso, uid)
    )
    ledger_entry(
        conn, user_id=uid, tx_type='GAME_REFUND', amount=bet,
        before=before, after=after, game_id=int(game['id']),
        description=f"Game stake refunded ({status})", created_at=now_iso,
    )


def cancel_game(*, game_id: int, creator_id: int, chat_id: int, message_id: int) -> dict[str, Any]:
    now_iso = _iso(_now_dt())
    with models._lock, connect() as conn:
        begin_immediate(conn)
        game = _fetch_game(conn, game_id)
        if not game:
            conn.rollback(); raise GameUnavailable()
        if int(game['chat_id']) != int(chat_id) or int(game['message_id']) != int(message_id):
            conn.rollback(); raise CallbackMismatch()
        if int(game['creator_id']) != int(creator_id):
            conn.rollback(); raise NotCreator()
        if game['status'] != models.GAME_OPEN:
            conn.rollback(); raise GameUnavailable()
        _refund_open_game(conn, game, models.GAME_CANCELLED, now_iso)
        conn.commit()
        return _fetch_game(conn, game_id)


def expire_due_games(limit: int = 100) -> list[dict[str, Any]]:
    now_iso = _iso(_now_dt())
    expired: list[dict[str, Any]] = []
    with models._lock, connect() as conn:
        begin_immediate(conn)
        rows = conn.execute(
            "SELECT * FROM games WHERE status='OPEN' AND expires_at<=? ORDER BY id LIMIT ?",
            (now_iso, max(1, min(500, int(limit))))
        ).fetchall()
        for row in rows:
            game = dict(row)
            _refund_open_game(conn, game, models.GAME_EXPIRED, now_iso)
            expired.append(game)
        conn.commit()
    for game in expired:
        game['status'] = models.GAME_EXPIRED
        game['finished_at'] = now_iso
    return expired


def get_game(game_id: int) -> dict[str, Any] | None:
    return models.get_game(game_id)


def chat_matches_setting(chat_id: int, username: str | None = None) -> bool:
    configured = str(models.get_game_settings().get('main_game_group') or '').strip()
    if not configured:
        return False
    if configured.startswith('@'):
        return bool(username) and configured[1:].lower() == username.lstrip('@').lower()
    if not configured.lstrip('-').isdigit():
        return False
    wanted = int(configured)
    actual = int(chat_id)
    if wanted == actual or abs(wanted) == abs(actual):
        return True
    # Accept a raw positive channel id as well as Telegram's -100-prefixed id.
    return wanted > 0 and str(abs(actual)).endswith(str(wanted))
