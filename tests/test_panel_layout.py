from pathlib import Path


def test_dot_panel_has_new_top_level_layout():
    source = (Path(__file__).parents[1] / 'inline.py').read_text(encoding='utf-8')
    labels = [
        '🎙 تبدیل متن به صوت',
        '🤖 تبچی',
        '💾 سیو پیام',
        '🔤 فونت',
        '📊 آمار',
        '⚙️ سایر قابلیت‌ها',
    ]
    for label in labels:
        assert label in source
    assert 'settings_root' not in source
    assert 'ui.inline_button("⚙️ تنظیمات"' not in source
