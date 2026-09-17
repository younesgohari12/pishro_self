from pathlib import Path
import db


def test_font_settings_are_per_user(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_DIR', str(tmp_path), raising=False)
    monkeypatch.setattr(db, 'INDEX_FILE', str(tmp_path / 'index.json'), raising=False)
    monkeypatch.setattr(db, 'META_FILE', str(tmp_path / 'meta.json'), raising=False)
    monkeypatch.setattr(db, 'GLOBAL_FILE', str(tmp_path / 'global.json'), raising=False)
    monkeypatch.setattr(db, 'PAYMENTS_FILE', str(tmp_path / 'payments.json'), raising=False)
    db._cache.clear()
    db._index.clear()
    db._meta.clear()
    db._meta.update({'latest_shard': 0})
    db._initialized = False

    a = db.update_user_settings(101, {'message_font_enabled': True, 'message_font_style': 'underline'})
    b = db.get_user_settings(202)

    assert a['message_font_enabled'] is True
    assert a['message_font_style'] == 'underline'
    assert b['message_font_enabled'] is False
    assert b['message_font_style'] == 'bold'


def test_invalid_font_style_normalizes_to_bold():
    normalized = db._normalize_settings({'message_font_enabled': True, 'message_font_style': 'unknown'})
    assert normalized['message_font_enabled'] is True
    assert normalized['message_font_style'] == 'bold'


def test_panel_contains_top_level_font_button():
    source = (Path(__file__).parents[1] / 'inline.py').read_text(encoding='utf-8')
    assert 'ui.inline_button("🔤 فونت", b"msgfont_menu"' in source
    assert 'msgfont_select' in source
    assert 'msgfont_preview' in source
    assert 'msgfont_enable' in source
    assert 'msgfont_disable' in source


def test_font_service_contains_all_six_telegram_entity_modes():
    source = (Path(__file__).parents[1] / 'services' / 'font_formatter.py').read_text(encoding='utf-8')
    expected = [
        'MessageEntityBold',
        'MessageEntityItalic',
        'MessageEntityStrike',
        'MessageEntityUnderline',
        'MessageEntityCode',
        'bold_italic',
    ]
    for item in expected:
        assert item in source
