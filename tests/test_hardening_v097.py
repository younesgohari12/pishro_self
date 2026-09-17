"""Regression tests for v0.09.7 reliability/security hardening."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import db
from services import deleted_handler
from services.logging_service import redact


ROOT = Path(__file__).resolve().parents[1]


def test_config_is_python_only_and_validates_without_values():
    import ast
    import config
    source = (ROOT / 'config.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    # Local credentials in config.py are intentional for this user-requested release.
    assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id in {'_env', '_env_int', '_env_float', '_load_dotenv'}
                   for n in ast.walk(tree))
    assert '.env' not in source
    assert 'os.getenv' not in source and 'os.environ' not in source
    assert not (ROOT / '.env.example').exists()
    assert not config.validate_runtime_config()


def test_environment_cannot_override_python_configuration(monkeypatch):
    import config
    import runpy
    monkeypatch.setenv('PISHRO_BOT_TOKEN', 'wrong-value')
    monkeypatch.setenv('BOT_TOKEN', 'wrong-value')
    values = runpy.run_path(str(ROOT / 'config.py'))
    # Compare as booleans to prevent credentials appearing in pytest failures.
    assert bool(values['BOT_TOKEN'] == config.BOT_TOKEN)


def test_logger_redacts_tokens_keys_and_phone_numbers():
    token = '123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_123456789'
    key = 'aa-THIS_IS_A_FAKE_TEST_KEY_12345'
    rendered = redact(f'token={token} api_key={key} phone=+491234567890')
    assert token not in rendered
    assert key not in rendered
    assert '+491234567890' not in rendered
    assert '[REDACTED]' in rendered
    assert '[PHONE]' in rendered


def test_user_settings_returns_nested_deep_copy():
    db._cache['101'] = db._normalize_settings({'muted_chats': [], 'enemy_chats': []})
    first = db.get_user_settings(101)
    first['muted_chats'].append(999)
    first['enemy_chats'].append(888)
    second = db.get_user_settings(101)
    assert second['muted_chats'] == []
    assert second['enemy_chats'] == []


def test_atomic_json_write_recovers_last_known_good_backup(tmp_path):
    path = tmp_path / 'settings.json'
    db._write_json(str(path), {'version': 1})
    db._write_json(str(path), {'version': 2})
    path.write_text('{broken json', encoding='utf-8')

    recovered = db._read_json(str(path), {})
    assert recovered == {'version': 1}
    assert json.loads((tmp_path / 'settings.json.bak').read_text(encoding='utf-8')) == {'version': 1}


def test_shard_rollover_write_failure_keeps_old_user_copy(monkeypatch):
    original = db._normalize_settings({'first_name': 'Old'})
    db._save_user('101', original)
    old_path = Path(db._shard_path(0))
    assert '101' in json.loads(old_path.read_text(encoding='utf-8'))
    assert db._index['101'] == 0

    # Force rollover on the next save, then fail exactly when writing the new shard.
    monkeypatch.setattr(db, 'MAX_DB_FILE_SIZE', 1)
    real_write = db._write_json

    def fail_new_shard(path, data):
        if Path(path).name == 'users_1.json':
            raise OSError('simulated disk failure')
        return real_write(path, data)

    monkeypatch.setattr(db, '_write_json', fail_new_shard)
    changed = db._normalize_settings({'first_name': 'New'})
    with pytest.raises(OSError, match='simulated disk failure'):
        db._save_user('101', changed)

    durable = json.loads(old_path.read_text(encoding='utf-8'))
    assert durable['101']['first_name'] == 'Old'
    assert db._index['101'] == 0


def test_ttl_task_registry_drops_empty_owner_bucket():
    marker = object()
    deleted_handler._TTL_TASKS.clear()
    deleted_handler._TTL_TASKS[77] = {marker}
    deleted_handler._ttl_task_done(77, marker)
    assert 77 not in deleted_handler._TTL_TASKS


def test_json_user_settings_survive_fresh_process_state(monkeypatch):
    saved = db._normalize_settings({
        'first_name': 'Persisted',
        'muted_chats': [111, 222],
        'enemy_chats': [333],
        'message_font_enabled': True,
    })
    db._save_user('101', saved)

    # Simulate a process restart while keeping only files on disk.
    monkeypatch.setattr(db, '_cache', {})
    monkeypatch.setattr(db, '_index', {})
    monkeypatch.setattr(db, '_meta', {'latest_shard': 0})
    monkeypatch.setattr(db, '_initialized', False)

    loaded = db.get_user_settings(101)
    assert loaded['first_name'] == 'Persisted'
    assert loaded['muted_chats'] == [111, 222]
    assert loaded['enemy_chats'] == [333]
    assert loaded['message_font_enabled'] is True


def test_invalid_primary_does_not_replace_known_good_backup(tmp_path):
    path = tmp_path / 'durable.json'
    db._write_json(str(path), {'version': 1})
    db._write_json(str(path), {'version': 2})
    # backup now contains version 1. Corrupt primary, then write a new value.
    path.write_text('{corrupt', encoding='utf-8')
    db._write_json(str(path), {'version': 3})

    assert json.loads(path.read_text(encoding='utf-8')) == {'version': 3}
    assert json.loads((tmp_path / 'durable.json.bak').read_text(encoding='utf-8')) == {'version': 1}


def test_missing_index_entry_is_recovered_from_shard(monkeypatch):
    saved = db._normalize_settings({'first_name': 'Recovered'})
    db._save_user('404', saved)

    # Simulate a valid index that lost just this one entry.
    db._write_json(db.INDEX_FILE, {'999': 0})
    monkeypatch.setattr(db, '_cache', {})
    monkeypatch.setattr(db, '_index', {})
    monkeypatch.setattr(db, '_meta', {'latest_shard': 0})
    monkeypatch.setattr(db, '_initialized', False)

    loaded = db.get_user_settings(404)
    assert loaded['first_name'] == 'Recovered'
    assert db._index['404'] == 0
