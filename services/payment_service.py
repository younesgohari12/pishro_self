"""Persistent payment requests and exactly-once manual approval."""
from __future__ import annotations
import json
import os
import time

import db
from config import MIN_DIAMOND, MAX_DIAMOND, PRICE_PER_DIAMOND
from database import models
from services.transaction_service import connect, begin_immediate, ledger_entry


class PaymentError(ValueError):
    pass


def _integer(value, label, minimum=1):
    if type(value) is not int or not minimum <= value <= (1 << 63) - 1:
        raise PaymentError(f'{label} معتبر نیست.')
    return value


def _initialize():
    with db._lock, models._lock, connect() as conn:
        begin_immediate(conn)
        if conn.execute("SELECT 1 FROM payment_migrations WHERE name='legacy_json_v1'").fetchone():
            return
        legacy = {}
        if os.path.exists(db.PAYMENTS_FILE):
            with open(db.PAYMENTS_FILE, encoding='utf-8') as handle:
                legacy = json.load(handle)
            if not isinstance(legacy, dict):
                raise PaymentError('فایل پرداخت‌های قبلی قابل خواندن نیست.')
        for key, rec in legacy.items():
            if not isinstance(rec, dict):
                raise PaymentError('رکورد پرداخت قدیمی معتبر نیست.')
            conn.execute('''INSERT OR IGNORE INTO wallet_payments
                (id,uid,diamonds,amount,status,created_at,created_ts,reviewed_at)
                VALUES (?,?,?,?,?,?,?,?)''',
                (int(key),int(rec['uid']),int(rec['diamonds']),int(rec['amount']),rec['status'],
                 rec.get('created_at',''),float(rec.get('created_ts',0)),rec.get('reviewed_at','')))
        # Preserve the old next number even when historical requests were removed.
        next_id = max(1001, int(db.get_global().get('next_pay_id') or 1001))
        conn.execute("INSERT INTO payment_migrations(name,value) VALUES ('legacy_json_v1',?)",(next_id,))
        conn.commit()


def create(uid, diamonds, amount):
    uid=_integer(uid,'شناسه کاربر');diamonds=_integer(diamonds,'الماس');amount=_integer(amount,'مبلغ')
    if not MIN_DIAMOND <= diamonds <= MAX_DIAMOND or amount != diamonds * PRICE_PER_DIAMOND:
        raise PaymentError('مقدار خرید یا مبلغ پرداخت معتبر نیست.')
    _initialize()
    with models._lock, connect() as conn:
        begin_immediate(conn)
        if conn.execute("SELECT 1 FROM wallet_payments WHERE uid=? AND status='pending'",(uid,)).fetchone():
            raise PaymentError('شما یک درخواست پرداخت در انتظار دارید.')
        floor=conn.execute("SELECT value FROM payment_migrations WHERE name='legacy_json_v1'").fetchone()[0]
        maximum=conn.execute('SELECT COALESCE(MAX(id),1000) FROM wallet_payments').fetchone()[0]
        pay_id=max(floor,maximum+1)
        conn.execute('''INSERT INTO wallet_payments
            (id,uid,diamonds,amount,status,created_at,created_ts,reviewed_at)
            VALUES (?,?,?,?,'pending',?,?,'')''',
            (pay_id,uid,diamonds,amount,models._now_iso(),time.time()))
        conn.commit()
        return pay_id


def get(pay_id):
    _initialize()
    with connect() as conn:
        row=conn.execute('SELECT * FROM wallet_payments WHERE id=?',(int(pay_id),)).fetchone()
        return dict(row) if row else None


def list_requests(status=None,limit=None):
    _initialize()
    sql='SELECT * FROM wallet_payments';args=[]
    if status:
        sql+=' WHERE status=?';args.append(status)
    sql+=' ORDER BY created_ts DESC,id DESC'
    if limit:
        sql+=' LIMIT ?';args.append(max(1,int(limit)))
    with connect() as conn:
        return [dict(row) for row in conn.execute(sql,args)]


def review(pay_id, *, approve, reviewer_id):
    from services.admin_manager import is_owner
    if not is_owner(reviewer_id):
        raise PaymentError('دسترسی تأیید پرداخت ندارید.')
    if type(approve) is not bool:
        raise PaymentError('تصمیم پرداخت معتبر نیست.')
    _initialize()
    with models._lock, connect() as conn:
        begin_immediate(conn)
        row=conn.execute('SELECT * FROM wallet_payments WHERE id=?',(int(pay_id),)).fetchone()
        if row is None:
            raise PaymentError('درخواست پرداخت پیدا نشد.')
        rec=dict(row)
        if rec['status']!='pending':
            return dict(rec,duplicate=True)
        now=models._now_iso()
        if approve:
            diamonds=_integer(rec['diamonds'],'الماس');_integer(rec['amount'],'مبلغ')
            user=conn.execute('SELECT diamonds FROM users WHERE telegram_id=?',(rec['uid'],)).fetchone()
            if user is None:
                raise PaymentError('کیف پول کاربر موجود نیست.')
            before=user['diamonds'];after=before+diamonds
            _integer(after,'موجودی',minimum=0)
            conn.execute('UPDATE users SET diamonds=?,updated_at=? WHERE telegram_id=?',(after,now,rec['uid']))
            ledger_entry(conn,user_id=rec['uid'],tx_type='ADMIN_ADD',amount=diamonds,before=before,after=after,
                         game_id=None,description=f"PAYMENT:{rec['id']}",created_at=now)
        status='approved' if approve else 'rejected'
        conn.execute('UPDATE wallet_payments SET status=?,reviewed_at=?,reviewer_id=? WHERE id=?',
                     (status,now,int(reviewer_id),rec['id']))
        conn.commit()
        return dict(rec,status=status,reviewed_at=now,reviewer_id=int(reviewer_id),duplicate=False)
