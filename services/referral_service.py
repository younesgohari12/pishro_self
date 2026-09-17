"""One committed wallet reward per invitee, including concurrent callbacks."""
import db
from config import REF_REWARD
from database import models
from services.transaction_service import connect, begin_immediate, ledger_entry
from services.transfer_service import registered_user, MAX_BALANCE


def claim(invitee_id):
    uid = int(invitee_id)
    with db._lock, models._lock, connect() as conn:
        begin_immediate(conn)
        invited = db._get_internal(uid)
        ref = int(invited.get('referred_by') or 0)
        if (not ref or ref == uid or invited.get('ref_credited')
                or not registered_user(uid) or not registered_user(ref)):
            return None
        if conn.execute('SELECT 1 FROM referral_claims WHERE invitee_id=?', (uid,)).fetchone():
            return None
        row = conn.execute('SELECT diamonds FROM users WHERE telegram_id=?', (ref,)).fetchone()
        if row is None or type(REF_REWARD) is not int or not 0 < REF_REWARD <= MAX_BALANCE - row['diamonds']:
            return None
        before = row['diamonds']
        now = models._now_iso()
        conn.execute('INSERT INTO referral_claims VALUES (?,?,?,?)', (uid, ref, REF_REWARD, now))
        conn.execute('UPDATE users SET diamonds=?,updated_at=? WHERE telegram_id=?', (before+REF_REWARD, now, ref))
        ledger_entry(conn, user_id=ref, tx_type='ADMIN_ADD', amount=REF_REWARD, before=before,
                     after=before+REF_REWARD, game_id=None, description=f'REFERRAL:{uid}', created_at=now)
        conn.commit()
        return {'referrer_id': ref, 'reward': REF_REWARD}
