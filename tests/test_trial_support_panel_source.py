from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_main_bot_menu_contains_trial_and_support():
    source = (ROOT / 'bot' / 'core.py').read_text(encoding='utf-8')
    assert 'TRIAL_DURATION_HOURS' in source
    assert '🎧 پشتیبانی' in source
    assert 'trial_menu' in source
    assert 'support_menu' in source


def test_requested_modules_exist():
    expected = [
        ROOT / 'handlers' / 'trial.py',
        ROOT / 'handlers' / 'support.py',
        ROOT / 'handlers' / 'admin.py',
        ROOT / 'handlers' / 'panel.py',
        ROOT / 'services' / 'ticket_manager.py',
        ROOT / 'services' / 'admin_manager.py',
        ROOT / 'services' / 'trial_manager.py',
        ROOT / 'database' / 'models.py',
    ]
    assert all(path.exists() for path in expected)


def test_trial_login_is_bound_to_same_telegram_account():
    source = (ROOT / 'login_manager.py').read_text(encoding='utf-8')
    assert "purpose == 'free_trial' and int(me.id) != int(uid)" in source
    assert 'activate_after_login' in source
