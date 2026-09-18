"""بخش کاربران پنل مدیریت: دکمه‌های افزایش/کاهش موجودی باید همیشه در دسترس باشند.

ریشه‌ای v0.09.20:
- نمای کاربر در بخش کاربران قبلاً به رکورد کیف پول SQLite وابسته بود و
  دکمه‌های ➕/➖ موجودی در نمای قابل دسترس وجود نداشتند (تابع دارای دکمه یتیم بود).
- این تست‌ها قرارداد جدید را قفل می‌کنند: نمای واحد کاربر همیشه رندر می‌شود،
  دکمه‌های افزایش/کاهش/سریع کار می‌کنند و جستجو برای کاربر بدون رکورد کیف پول
  هم جواب می‌دهد.
"""
from __future__ import annotations

import asyncio
import time

import pytest

import handlers.admin as admin_module
from handlers.admin import AdminController


# ------------------------------------------------------------------
# فیک‌ها
# ------------------------------------------------------------------
class FakeSender:
    def __init__(self, username='owner'):
        self.username = username
        self.first_name = 'Owner'
        self.id = 999


class FakeEvent:
    """رویداد عمومی (پیام/کال‌بک) با ثبت پاس‌ها."""

    def __init__(self, sender_id=999, data=None, raw_text=''):
        self.sender_id = sender_id
        self._data = data
        self.raw_text = raw_text
        self.chat_id = 555
        self.edits = []
        self.replies = []
        self.answers = []
        self._sender = FakeSender()

    async def get_sender(self):
        return self._sender

    async def answer(self, text=None, alert=False):
        self.answers.append((text, alert))

    async def edit(self, text, buttons=None, parse_mode=None):
        self.edits.append({'text': text, 'buttons': buttons})

    async def reply(self, text, buttons=None, parse_mode=None):
        self.replies.append({'text': text, 'buttons': buttons})

    def button_data(self):
        """همهٔ callback_dataهای دکمه‌های آخرین ویرایش/پاسخ."""
        rows = None
        if self.edits:
            rows = self.edits[-1]['buttons']
        elif self.replies:
            rows = self.replies[-1]['buttons']
        out = []
        for row in rows or []:
            for btn in row:
                data = getattr(btn, 'data', None)
                if isinstance(data, bytes):
                    out.append(data.decode('utf-8'))
        return out

    def last_text(self):
        if self.edits:
            return self.edits[-1]['text']
        if self.replies:
            return self.replies[-1]['text']
        return ''


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, target, text, buttons=None, parse_mode=None):
        self.sent.append({'target': target, 'text': text, 'buttons': buttons})


def make_controller(monkeypatch, *, balance=100, wallet_row=None, profile=None):
    """کنترلر ایزوله از دیسک/دیتابیس با تزریق وابستگی‌های کنترل‌شده."""
    controller = AdminController.__new__(AdminController)
    controller.bot = FakeBot()
    controller.resolve_user = lambda value: _async(None)
    controller.states = {}

    monkeypatch.setattr(admin_module.admin_manager, 'is_admin', lambda uid, username='': True)
    monkeypatch.setattr(admin_module.admin_manager, 'note_identity', lambda uid, username: None)
    monkeypatch.setattr(admin_module.admin_manager, 'has_permission',
                        lambda uid, perm, username='': True)
    monkeypatch.setattr(admin_module.admin_manager, 'can_any',
                        lambda uid, perms, username='': True)

    profile = profile if profile is not None else {
        'username': 'ali', 'first_name': 'Ali', 'last_name': '',
        'registered_at': '2026-01-01', 'last_activity': '2026-09-18',
    }
    wallet_row = wallet_row if wallet_row is not None else {
        'telegram_id': 55, 'username': 'ali', 'first_name': 'Ali',
        'diamonds': balance, 'referral_count': 2, 'banned': 0,
    }

    monkeypatch.setattr(admin_module.models, 'get_user_details', lambda uid: wallet_row)
    monkeypatch.setattr(admin_module.models, 'get_profile', lambda uid: profile)
    monkeypatch.setattr(admin_module.models, 'ticket_count_for_user', lambda uid: 0)
    monkeypatch.setattr(admin_module.trial_manager, 'refresh', lambda uid: None)
    monkeypatch.setattr(admin_module.db, 'get_user_settings', lambda uid: {'diamonds': balance, 'username': 'ali'})
    monkeypatch.setattr(admin_module.db, 'update_user_settings', lambda uid, patch: None)
    monkeypatch.setattr(admin_module.db, 'save_admin_state', lambda uid, state: None)
    monkeypatch.setattr(admin_module.db, 'delete_admin_state', lambda uid: None)
    return controller


def _async(value):
    async def _inner(*args, **kwargs):
        return value
    return _inner()


def run(coro):
    return asyncio.run(coro)


def patch_adjust(monkeypatch, calls, *, after=160):
    import services.balance_service as bs

    def fake_adjust(user_id, amount, *, floor_zero=False, description=''):
        calls.append({'user_id': user_id, 'amount': amount,
                      'floor_zero': floor_zero, 'description': description})
        return after

    monkeypatch.setattr(bs, 'admin_adjust', fake_adjust)


# ------------------------------------------------------------------
# نمای واحد کاربر
# ------------------------------------------------------------------
def test_user_detail_has_balance_buttons_even_without_wallet_row(monkeypatch):
    """کاربر بدون رکورد کیف پول هم باید نمای کامل + دکمه‌های موجودی بگیرد."""
    controller = make_controller(monkeypatch, wallet_row=None, balance=7)
    event = FakeEvent()

    text, buttons = controller.render_user_detail(55)

    assert '💎 موجودی: **7 الماس**' in text
    datas = [getattr(b, 'data', b'').decode() if isinstance(getattr(b, 'data', None), bytes) else ''
             for row in buttons for b in row]
    assert 'adm2_uplus_55' in datas
    assert 'adm2_uminus_55' in datas
    assert any('adm2_user_ban_55' in d for d in datas)


def test_adm2_user_callback_opens_unified_detail(monkeypatch):
    """کلیک روی کاربر در لیست → نمای واحد با دکمه‌های افزایش/کاهش موجودی."""
    controller = make_controller(monkeypatch)
    event = FakeEvent(data='adm2_user_55')

    handled = run(controller.handle_callback(event, 'adm2_user_55'))

    assert handled is True
    assert event.edits, 'نمای کاربر باید ویرایش/نمایش شود'
    datas = event.button_data()
    assert 'adm2_uplus_55' in datas
    assert 'adm2_uminus_55' in datas
    assert 'adm2_user_ban_55' in datas
    assert 'وضعیت حساب' in event.last_text()


def test_render_user_management_detail_delegates_to_unified_view(monkeypatch):
    controller = make_controller(monkeypatch)
    detail = controller.render_user_detail(55)
    mgmt = controller.render_user_management_detail(55)
    assert detail == mgmt


# ------------------------------------------------------------------
# شروع جریان افزایش/کاهش
# ------------------------------------------------------------------
def test_uplus_opens_prompt_with_presets(monkeypatch):
    controller = make_controller(monkeypatch, balance=100)
    event = FakeEvent()

    run(controller.handle_callback(event, 'adm2_uplus_55'))

    assert controller.states[999]['step'] == 'USER_BALANCE_AMOUNT'
    assert controller.states[999]['delta'] == 1
    assert controller.states[999]['target'] == 55
    assert 'افزایش' in event.last_text()
    assert '100 الماس' in event.last_text()
    datas = event.button_data()
    assert 'adm2_uquick_1_10_55' in datas
    assert 'adm2_uquick_1_1000_55' in datas


def test_uminus_sets_negative_delta(monkeypatch):
    controller = make_controller(monkeypatch, balance=100)
    event = FakeEvent()

    run(controller.handle_callback(event, 'adm2_uminus_55'))

    st = controller.states[999]
    assert st['delta'] == -1
    assert 'کاهش' in event.last_text()
    assert 'adm2_uquick_-1_50_55' in event.button_data()


# ------------------------------------------------------------------
# دکمه‌های سریع
# ------------------------------------------------------------------
def test_uquick_adds_balance_and_notifies_user(monkeypatch):
    controller = make_controller(monkeypatch, balance=100)
    calls = []
    patch_adjust(monkeypatch, calls, after=150)
    event = FakeEvent()

    run(controller.handle_callback(event, 'adm2_uquick_1_50_55'))

    assert calls == [{'user_id': 55, 'amount': 50, 'floor_zero': True,
                      'description': 'Admin user adjustment'}]
    assert any('قبل: **100** | بعد: **150**' in r['text'] for r in event.replies)
    user_msgs = [s for s in controller.bot.sent if s['target'] == 55]
    assert user_msgs and '50 الماس به حساب شما اضافه شد' in user_msgs[0]['text']


def test_uquick_subtracts_balance(monkeypatch):
    controller = make_controller(monkeypatch, balance=100)
    calls = []
    patch_adjust(monkeypatch, calls, after=0)
    event = FakeEvent()

    run(controller.handle_callback(event, 'adm2_uquick_-1_100_55'))

    assert calls and calls[0]['amount'] == -100
    user_msgs = [s for s in controller.bot.sent if s['target'] == 55]
    assert user_msgs and 'کسر شد' in user_msgs[0]['text']


def test_uquick_rejects_invalid_payload(monkeypatch):
    controller = make_controller(monkeypatch)
    calls = []
    patch_adjust(monkeypatch, calls)
    event = FakeEvent()

    run(controller.handle_callback(event, 'adm2_uquick_x_50_55'))

    assert calls == []
    assert any('نامعتبر' in (a[0] or '') for a in event.answers)


# ------------------------------------------------------------------
# ورود دستی مقدار
# ------------------------------------------------------------------
def _balance_state(controller, target=55, delta=1):
    controller._set_state(999, {'step': 'USER_BALANCE_AMOUNT',
                                'target': target, 'delta': delta,
                                '_expires_at': time.time() + 600})


def test_manual_amount_flow_adjusts(monkeypatch):
    controller = make_controller(monkeypatch, balance=100)
    _balance_state(controller)
    calls = []
    patch_adjust(monkeypatch, calls, after=250)
    event = FakeEvent(raw_text='150')

    handled = run(controller.handle_message(event))

    assert handled is True
    assert calls == [{'user_id': 55, 'amount': 150, 'floor_zero': True,
                      'description': 'Admin user adjustment'}]
    assert 999 not in controller.states, 'state باید بعد از موفقیت پاک شود'


def test_manual_amount_rejects_non_numeric(monkeypatch):
    controller = make_controller(monkeypatch)
    _balance_state(controller)
    calls = []
    patch_adjust(monkeypatch, calls)
    event = FakeEvent(raw_text='abc')

    run(controller.handle_message(event))

    assert calls == []
    assert any('عدد مثبت' in r['text'] for r in event.replies)


# ------------------------------------------------------------------
# جستجوی کاربر
# ------------------------------------------------------------------
def test_user_search_falls_back_to_resolver_without_wallet_row(monkeypatch):
    """کاربر بدون رکورد کیف پول از طریق resolve_user پیدا می‌شود."""
    controller = make_controller(monkeypatch)
    controller.resolve_user = lambda value: _async(4242)
    monkeypatch.setattr(admin_module.models, 'search_users', lambda text: [])
    controller._set_state(999, {'step': 'USER_SEARCH', '_expires_at': time.time() + 600})
    event = FakeEvent(raw_text='@newuser')

    handled = run(controller.handle_message(event))

    assert handled is True
    datas = [getattr(b, 'data', b'').decode() if isinstance(getattr(b, 'data', None), bytes) else ''
             for row in event.replies[-1]['buttons'] for b in row]
    assert 'adm2_uplus_4242' in datas
    assert 'adm2_uminus_4242' in datas


def test_user_search_still_hits_wallet_index_first(monkeypatch):
    controller = make_controller(monkeypatch)
    monkeypatch.setattr(admin_module.models, 'search_users',
                        lambda text: [{'telegram_id': 55}])
    controller._set_state(999, {'step': 'USER_SEARCH', '_expires_at': time.time() + 600})
    event = FakeEvent(raw_text='ali')

    run(controller.handle_message(event))

    datas = [getattr(b, 'data', b'').decode() if isinstance(getattr(b, 'data', None), bytes) else ''
             for row in event.replies[-1]['buttons'] for b in row]
    assert 'adm2_uplus_55' in datas


# ------------------------------------------------------------------
# دسترسی
# ------------------------------------------------------------------
def test_balance_buttons_denied_without_manage_users(monkeypatch):
    controller = make_controller(monkeypatch)
    monkeypatch.setattr(
        AdminController, '_allowed',
        lambda self, uid, perm, username='': perm != 'manage_users'
    )
    event = FakeEvent()

    handled = run(controller.handle_callback(event, 'adm2_uplus_55'))

    assert handled is True
    assert any('فعال نیست' in (a[0] or '') for a in event.answers)
    assert event.edits == []


def test_admin_menu_has_users_section_button():
    import bot.core as core

    text, buttons = core.render_admin_menu()
    datas = [getattr(b, 'data', b'').decode() if isinstance(getattr(b, 'data', None), bytes) else ''
             for row in buttons for b in row]
    assert 'adm2_users' in datas
