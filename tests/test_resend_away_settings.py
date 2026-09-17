"""Acceptance tests — تنظیمات و پنل برای Resend هوشمند + Away v0.09.13.

- کلیدهای دیتابیس و نرمال‌سازی (premium_emoji_resend / away_*)
- زنجیره premium_resend_effective (هم‌راستا با موتور)
- دکمه‌های پنل اصلی و صفحه Away
- کلید config و marker ارتقا
- الگوی دستورهای .بستن و .away
"""
import pytest

import config
import db
import inline
import ui
from services import away as away_service


UID = 8359698350


# ================================================== db keys
def test_default_settings_contain_new_keys():
    settings = db.get_user_settings(UID)
    assert 'premium_emoji_resend' in settings
    assert settings['premium_emoji_resend'] is None  # لمس‌نشده
    assert settings['away_enabled'] is False
    assert settings['away_text'] == db.DEFAULT_AWAY_TEXT
    assert settings['away_sent_users'] == {}


def test_normalize_away_sent_users():
    normalized = db._normalize_settings({
        'away_sent_users': {'100': 111.5, 'bad': 'x', 200: '222'},
        'away_text': '  متن  ',
        'premium_emoji_resend': 0,
    })
    assert normalized['away_sent_users'] == {'100': 111.5, '200': 222.0}
    assert normalized['away_text'] == 'متن'
    assert normalized['premium_emoji_resend'] is False


def test_normalize_empty_away_text_falls_back_to_default():
    normalized = db._normalize_settings({'away_text': '   '})
    assert normalized['away_text'] == db.DEFAULT_AWAY_TEXT


def test_roundtrip_persist_new_keys():
    db.update_user_settings(UID, {
        'premium_emoji_resend': True,
        'away_enabled': True,
        'away_text': 'متن من',
        'away_sent_users': {'555': 123.0},
    })
    settings = db.get_user_settings(UID)
    assert settings['premium_emoji_resend'] is True
    assert settings['away_enabled'] is True
    assert settings['away_text'] == 'متن من'
    assert settings['away_sent_users'] == {'555': 123.0}
    # پاک‌سازی برای تست‌های دیگر
    db.update_user_settings(UID, {
        'premium_emoji_resend': None, 'away_enabled': False,
        'away_text': db.DEFAULT_AWAY_TEXT, 'away_sent_users': {},
    })


# ================================================== effective chain
def test_premium_resend_effective_chain(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_ENABLED', True)
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_CONVERTER_ENABLED', True)
    db.update_user_settings(UID, {'premium_emoji_converter': None,
                                  'premium_emoji_resend': None})
    # لمس‌نشده + همه کلیدها روشن → روشن (Production-Safe)
    assert inline.premium_resend_effective(UID) is True
    # پنل صریح خاموش → خاموش
    db.update_user_settings(UID, {'premium_emoji_resend': False})
    assert inline.premium_resend_effective(UID) is False
    # کانورتر خاموش (پنل) → resend هم خاموش
    db.update_user_settings(UID, {'premium_emoji_resend': None,
                                  'premium_emoji_converter': False})
    assert inline.premium_resend_effective(UID) is False
    db.update_user_settings(UID, {'premium_emoji_converter': None})


def test_premium_resend_effective_hard_off(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', False)
    assert inline.premium_resend_effective(UID) is False


# ================================================== panel
def test_main_menu_has_resend_and_away_buttons():
    text, buttons = inline.build_main_menu(UID, 'helperbot')
    flat = [str(b.text) for row in buttons for b in row]
    assert any('Resend' in t for t in flat)
    assert any('Away Message' in t for t in flat)


def test_away_menu_builder():
    text, buttons = inline.build_away_menu(UID)
    flat = [str(b.text) for row in buttons for b in row]
    assert any('روشن کردن' in t or 'خاموش کردن' in t for t in flat)
    assert any('تغییر متن' in t for t in flat)
    assert any('ریست' in t for t in flat)
    assert 'Away Message' in text
    assert db.DEFAULT_AWAY_TEXT[:30] in text or 'متن فعلی' in text


def test_away_menu_reflects_state():
    away_service.set_enabled(UID, True)
    text, buttons = inline.build_away_menu(UID)
    assert '🟢 روشن' in text
    away_service.set_enabled(UID, False)
    text, buttons = inline.build_away_menu(UID)
    assert '🔴 خاموش' in text


# ================================================== config marker
def test_resend_mode_config_updates():
    updates = config.resend_mode_config_updates()
    assert updates == {'PREMIUM_EMOJI_RESEND_MODE':
                       config.PREMIUM_EMOJI_RESEND_MODE}
    assert config.PREMIUM_EMOJI_RESEND_MODE is True  # پیش‌فرض انتشار روشن


def test_config_flags_exist():
    for key in ('PREMIUM_EMOJI_RESEND_MODE', 'PREMIUM_EMOJI_RESEND_DEBUG',
                'AWAY_DEBUG', 'AWAY_LOG_LEVEL', 'STATE_DEBUG',
                'AWAY_RESET_HOURS'):
        assert hasattr(config, key), key


# ================================================== command patterns
def test_close_and_away_patterns():
    from self import PATTERN_CLOSE, PATTERN_AWAY
    assert PATTERN_CLOSE.match('.بستن')
    assert not PATTERN_CLOSE.match('.بستن 2')
    assert PATTERN_AWAY.match('.away')
    assert PATTERN_AWAY.match('.away on')
    assert PATTERN_AWAY.match('.away text سلام بعداً می‌آیم')
    assert PATTERN_AWAY.match('.AWAY OFF')
    assert not PATTERN_AWAY.match('away on')
