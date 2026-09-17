"""Upgrade/restore acceptance tests with real SQLite, JSON, sessions and media."""
from contextlib import closing
import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import shutil
import subprocess
import sys
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import zipfile

import pytest
import db
from database import models
from services import balance_service, trial_manager
import storage

SETTINGS = {'API_ID': 12345, 'API_HASH': 'test-only-hash', 'BOT_TOKEN': '123:test-only',
            'PRICE_PER_DIAMOND': 40, 'TRANSLATE_ENABLED': False, 'CRYPTO_ENABLED': False}


def table_rows(path):
    with closing(sqlite3.connect(path)) as conn:
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {table: conn.execute('SELECT * FROM "' + table.replace('"', '""') + '"').fetchall()
                for table in tables}


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    source = tmp_path / 'old-release'
    data = source / 'db'
    data.mkdir(parents=True)
    monkeypatch.setattr(db, 'DB_DIR', str(data))
    for key, file in [('INDEX_FILE', 'index'), ('META_FILE', 'meta'), ('GLOBAL_FILE', 'global'), ('PAYMENTS_FILE', 'payments')]:
        monkeypatch.setattr(db, key, str(data / (file + '.json')))
    monkeypatch.setattr(models, 'TABCHI_DB_PATH', str(data / 'tabchi.sqlite3'))
    for init in (models.init_tabchi_db, models.init_message_saver_db, models.init_membership_support_db,
                 models.init_game_db, models.init_custom_emojis_db, models.init_crypto_db, models.init_ai_db):
        init()
    db.touch_user(101, username='legacy_user', first_name='Old')
    db.update_user_settings(101, {'diamonds': 3456, 'muted_chats': [707], 'self_enabled': False,
                                  'bill_seconds': 125, 'ref_count': 4})
    db.set_global({'card_holder': 'Test holder', 'force_channels': ['@test_channel']})
    models.touch_profile(101, username='legacy_user', first_name='Old')
    trial_manager.activate_after_login(101)
    trial_manager.revoke(101)
    models.create_ticket(101)
    models.set_save_status(101, True)
    models.set_crypto_favorites(101, ['BTC', 'TON'])
    models.set_ai_default_language(101, 'Persian')
    models.add_ai_exchange(101, 'Old question', 'Old answer')
    models.upsert_admin(user_id=202, username='helper', role='limited', permissions=['view_users'])
    for name in storage.DATA_DIRS:
        (source / name).mkdir(exist_ok=True)
    (source / 'sessions/user_101.txt').write_bytes(b'original-session-never-contact-telegram\r\n')
    (source / 'voice_settings.json').write_text('{"101":"male"}')
    (source / 'fosh_list.txt').write_text('custom user text\n')
    (source / 'message_cache/photo.jpg').write_bytes(b'preserved-photo-bytes')
    (source / 'banner/video.mp4').write_bytes(b'preserved-banner-bytes')
    (source / 'upload/audio.ogg').write_bytes(b'preserved-upload-bytes')
    (source / 'config.py').write_text("PRICE_PER_DIAMOND = 10 * 3\nBOT_TOKEN = '123:previous-setting'\n")
    return source


def test_upgrade_copies_all_tables_files_and_settings_without_deleting_source(legacy, tmp_path):
    target = tmp_path / 'PishroSelfData'
    before = table_rows(legacy / 'db/tabchi.sqlite3')
    json_before = {p.name: p.read_bytes() for p in (legacy / 'db').glob('*.json')}
    storage.initialize_store(target, legacy, SETTINGS)
    assert table_rows(target / 'db/tabchi.sqlite3') == before
    assert table_rows(legacy / 'db/tabchi.sqlite3') == before
    for name, content in json_before.items():
        assert (target / 'db' / name).read_bytes() == content
        assert (legacy / 'db' / name).read_bytes() == content
    for name in ('sessions/user_101.txt', 'voice_settings.json', 'fosh_list.txt',
                 'message_cache/photo.jpg', 'banner/video.mp4', 'upload/audio.ogg'):
        assert (target / name).read_bytes() == (legacy / name).read_bytes()
    config = storage.load_user_config(target / 'config.py', SETTINGS)
    assert config['PRICE_PER_DIAMOND'] == 30
    assert config['BOT_TOKEN'] == '123:previous-setting'
    backups = list(storage.backup_dir(target).glob('*.zip'))
    assert len(backups) == 1
    storage.restore_backup(tmp_path / 'restored-import', backups[0])
    assert table_rows(tmp_path / 'restored-import/db/tabchi.sqlite3') == before


def test_repeated_upgrade_never_reimports_old_wallets_or_config(legacy, tmp_path):
    target = tmp_path / 'PishroSelfData'
    storage.initialize_store(target, legacy, SETTINGS)
    with closing(sqlite3.connect(target / 'db/tabchi.sqlite3')) as conn:
        conn.execute('UPDATE users SET diamonds=19 WHERE telegram_id=101')
        conn.commit()
    storage.write_user_config(target / 'config.py', {**SETTINGS, 'PRICE_PER_DIAMOND': 55})
    assert storage.initialize_store(target, legacy, SETTINGS) is None
    with closing(sqlite3.connect(target / 'db/tabchi.sqlite3')) as conn:
        assert conn.execute('SELECT diamonds FROM users WHERE telegram_id=101').fetchone()[0] == 19
    assert storage.load_user_config(target / 'config.py', SETTINGS)['PRICE_PER_DIAMOND'] == 55


def test_changing_os_account_cannot_reimport_stale_legacy_balances(legacy, tmp_path):
    first = tmp_path / 'first-account/PishroSelfData'
    storage.initialize_store(first, legacy, SETTINGS)
    other = tmp_path / 'second-account/PishroSelfData'
    with pytest.raises(storage.StorageError, match='already migrated'):
        storage.initialize_store(other, legacy, SETTINGS)
    assert not other.exists()


def test_missing_active_store_does_not_silently_reimport_old_balances(legacy, tmp_path):
    target = tmp_path / 'PishroSelfData'
    storage.initialize_store(target, legacy, SETTINGS)
    kept = tmp_path / 'data-moved-by-operator'
    target.rename(kept)
    with pytest.raises(storage.StorageError, match='already migrated'):
        storage.initialize_store(target, legacy, SETTINGS)
    assert not target.exists()
    assert (kept / 'db/tabchi.sqlite3').is_file()


def test_release_changes_backup_before_marking_success_and_restarts_do_not_repeat(legacy, tmp_path):
    target = tmp_path / 'PishroSelfData'
    app = tmp_path / 'release-1'
    app.mkdir()
    (app / 'main.py').write_text('VERSION = 1\n')
    storage.initialize_store(target, legacy, SETTINGS)
    digest, saved = storage.prepare_upgrade(target, app)
    assert saved.is_file()
    assert storage.validate_data(target, True)['last_app_digest'] is None
    storage.finish_upgrade(target, digest)
    assert storage.prepare_upgrade(target, app)[1] is None
    before = table_rows(target / 'db/tabchi.sqlite3')
    app2 = tmp_path / 'completely-different-release-folder'
    app2.mkdir()
    (app2 / 'main.py').write_text('VERSION = 2\n')
    digest2, saved2 = storage.prepare_upgrade(target, app2)
    assert saved2 != saved and saved2.is_file()
    assert table_rows(target / 'db/tabchi.sqlite3') == before
    storage.finish_upgrade(target, digest2)


def test_backup_includes_committed_sqlite_wal_changes(legacy, tmp_path):
    target = tmp_path / 'PishroSelfData'
    storage.initialize_store(target, legacy, SETTINGS)
    path = target / 'db/tabchi.sqlite3'
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA wal_autocheckpoint=0')
        conn.execute('UPDATE users SET diamonds=777 WHERE telegram_id=101')
        conn.commit()
        assert Path(str(path) + '-wal').stat().st_size > 0
        saved = storage.create_backup(target)
        restored = tmp_path / 'restored'
        storage.restore_backup(restored, saved)
        with closing(sqlite3.connect(restored / 'db/tabchi.sqlite3')) as restored_conn:
            assert restored_conn.execute('SELECT diamonds FROM users WHERE telegram_id=101').fetchone()[0] == 777


def test_media_references_follow_new_data_root(legacy, tmp_path):
    target = tmp_path / 'PishroSelfData'
    with closing(sqlite3.connect(legacy / 'db/tabchi.sqlite3')) as conn:
        conn.execute('''INSERT INTO message_cache(owner_id,chat_id,message_id,message_type,media_path,cached_at)
                        VALUES (101,500,600,'photo',?,1)''', (str(legacy / 'message_cache/photo.jpg'),))
        conn.commit()
    storage.initialize_store(target, legacy, SETTINGS)
    with closing(sqlite3.connect(target / 'db/tabchi.sqlite3')) as conn:
        path = conn.execute('SELECT media_path FROM message_cache').fetchone()[0]
    assert Path(path) == target / 'message_cache/photo.jpg'
    assert Path(path).read_bytes() == b'preserved-photo-bytes'
    saved = storage.create_backup(target)
    restored = tmp_path / 'new-server-data'
    storage.restore_backup(restored, saved)
    with closing(sqlite3.connect(restored / 'db/tabchi.sqlite3')) as conn:
        assert conn.execute('SELECT media_path FROM message_cache').fetchone()[0] == str(restored / 'message_cache/photo.jpg')


@pytest.mark.parametrize('damage', ['database_missing', 'database_corrupt', 'json_corrupt', 'shard_missing', 'future_format', 'config_missing'])
def test_damaged_existing_data_blocks_startup_without_creating_empty_files(legacy, tmp_path, damage):
    target = tmp_path / 'PishroSelfData'
    storage.initialize_store(target, legacy, SETTINGS)
    storage.finish_upgrade(target, 'old-digest')
    if damage == 'database_missing':
        (target / 'db/tabchi.sqlite3').unlink()
    elif damage == 'database_corrupt':
        (target / 'db/tabchi.sqlite3').write_bytes(b'bad sqlite data')
    elif damage == 'json_corrupt':
        (target / 'db/users_0.json').write_bytes(b'bad JSON')
        (target / 'db/users_0.json.bak').unlink(missing_ok=True)
    elif damage == 'shard_missing':
        (target / 'db/users_0.json').unlink()
    elif damage == 'config_missing':
        (target / 'config.py').unlink()
    else:
        meta = json.loads((target / storage.META_FILE).read_text())
        meta['format_version'] = 99
        (target / storage.META_FILE).write_text(json.dumps(meta))
    before = {str(p.relative_to(target)): p.read_bytes() for p in target.rglob('*')
              if p.is_file() and not p.name.endswith(('-wal', '-shm'))}
    with pytest.raises(storage.StorageError):
        storage.initialize_store(target, legacy, SETTINGS)
    after = {str(p.relative_to(target)): p.read_bytes() for p in target.rglob('*')
             if p.is_file() and not p.name.endswith(('-wal', '-shm'))}
    assert before == after


def test_empty_install_requires_explicit_init_and_destination_is_not_merged(tmp_path):
    source = tmp_path / 'empty-release'
    source.mkdir()
    target = tmp_path / 'PishroSelfData'
    with pytest.raises(storage.StorageError, match='Previous data'):
        storage.initialize_store(target, source, SETTINGS)
    assert not target.exists()
    storage.initialize_store(target, source, SETTINGS, allow_empty=True)
    assert (target / storage.META_FILE).is_file()
    conflict = tmp_path / 'unknown-data'
    conflict.mkdir()
    (conflict / 'important.txt').write_text('keep')
    with pytest.raises(storage.StorageError, match='unregistered'):
        storage.initialize_store(conflict, source, SETTINGS, allow_empty=True)
    assert (conflict / 'important.txt').read_text() == 'keep'


def test_failed_import_keeps_source_and_does_not_publish_partial_target(legacy, tmp_path, monkeypatch):
    original_copy = storage.shutil.copyfile
    def fail_session(src, dst, *args, **kwargs):
        if Path(src).name == 'user_101.txt':
            raise OSError('disk full')
        return original_copy(src, dst, *args, **kwargs)
    monkeypatch.setattr(storage.shutil, 'copyfile', fail_session)
    target = tmp_path / 'PishroSelfData'
    before = table_rows(legacy / 'db/tabchi.sqlite3')
    with pytest.raises(OSError):
        storage.initialize_store(target, legacy, SETTINGS)
    assert not target.exists()
    assert table_rows(legacy / 'db/tabchi.sqlite3') == before
    assert (legacy / 'sessions/user_101.txt').is_file()


def test_restore_preserves_current_state_as_separate_directory(legacy, tmp_path):
    target = tmp_path / 'PishroSelfData'
    storage.initialize_store(target, legacy, SETTINGS)
    saved = storage.create_backup(target)
    with closing(sqlite3.connect(target / 'db/tabchi.sqlite3')) as conn:
        conn.execute('UPDATE users SET diamonds=88 WHERE telegram_id=101')
        conn.commit()
    kept = storage.restore_backup(target, saved)
    with closing(sqlite3.connect(target / 'db/tabchi.sqlite3')) as conn:
        assert conn.execute('SELECT diamonds FROM users WHERE telegram_id=101').fetchone()[0] == 3456
    with closing(sqlite3.connect(kept / 'db/tabchi.sqlite3')) as conn:
        assert conn.execute('SELECT diamonds FROM users WHERE telegram_id=101').fetchone()[0] == 88


def test_restore_checksum_failure_leaves_current_data_untouched(legacy, tmp_path):
    target = tmp_path / 'PishroSelfData'
    storage.initialize_store(target, legacy, SETTINGS)
    saved = storage.create_backup(target)
    bad = tmp_path / 'bad.zip'
    with zipfile.ZipFile(saved) as src, zipfile.ZipFile(bad, 'w') as dst:
        for name in src.namelist():
            dst.writestr(name, b'changed-session' if name == 'sessions/user_101.txt' else src.read(name))
    before = table_rows(target / 'db/tabchi.sqlite3')
    with pytest.raises(storage.StorageError, match='checksum'):
        storage.restore_backup(target, bad)
    assert table_rows(target / 'db/tabchi.sqlite3') == before
    assert (target / 'sessions/user_101.txt').read_bytes() == (legacy / 'sessions/user_101.txt').read_bytes()


def test_failed_restore_promotion_rolls_back_to_current_data(legacy, tmp_path, monkeypatch):
    target = tmp_path / 'PishroSelfData'
    storage.initialize_store(target, legacy, SETTINGS)
    saved = storage.create_backup(target)
    (target / 'sessions/user_101.txt').write_bytes(b'newer-session-must-survive')
    original_replace = storage.os.replace
    def fail_promotion(src, dst):
        if Path(dst) == target and Path(src).name.startswith(target.name + '.restore-'):
            raise OSError('simulated rename failure')
        return original_replace(src, dst)
    monkeypatch.setattr(storage.os, 'replace', fail_promotion)
    with pytest.raises(OSError):
        storage.restore_backup(target, saved)
    assert (target / 'sessions/user_101.txt').read_bytes() == b'newer-session-must-survive'
    assert storage.validate_data(target, True)


def test_process_lock_blocks_second_instance_and_releases_on_failure(tmp_path):
    root = tmp_path / 'data'
    with storage.process_lock(root):
        with pytest.raises(storage.StorageError, match='Another instance'):
            with storage.process_lock(root):
                pytest.fail('Second instance was admitted')
    with storage.process_lock(root):
        pass


def test_corrupt_json_does_not_become_default_settings(tmp_path):
    path = tmp_path / 'users.json'
    path.write_bytes(b'broken')
    with pytest.raises(storage.StorageError):
        db._read_json(str(path), {})
    assert path.read_bytes() == b'broken'


def test_persistent_config_survives_new_defaults_and_refuses_executable_code(tmp_path):
    path = tmp_path / 'config.py'
    storage.write_user_config(path, {**SETTINGS, 'PRICE_PER_DIAMOND': 27})
    new_defaults = {**SETTINGS, 'PRICE_PER_DIAMOND': 999, 'NEW_OPTION': True}
    resolved = {**new_defaults, **storage.load_user_config(path, new_defaults)}
    assert resolved['PRICE_PER_DIAMOND'] == 27 and resolved['NEW_OPTION'] is True
    path.write_text("__import__('os').remove('database')")
    with pytest.raises(storage.StorageError):
        storage.load_user_config(path, new_defaults)


def test_failed_pre_upgrade_backup_never_starts_runtime(legacy, tmp_path, monkeypatch):
    import main
    target = tmp_path / 'PishroSelfData'
    storage.initialize_store(target, legacy, SETTINGS)
    runtime = AsyncMock()
    monkeypatch.setattr(main, 'DATA_DIR', str(target))
    monkeypatch.setattr(main, 'BASE_DIR', str(legacy))
    monkeypatch.setattr(main, '_main_runtime', runtime)
    def fail(*args, **kwargs):
        raise storage.StorageError('backup failed')
    monkeypatch.setattr(storage, 'create_backup', fail)
    before = table_rows(target / 'db/tabchi.sqlite3')
    asyncio.run(main.main())
    runtime.assert_not_awaited()
    assert table_rows(target / 'db/tabchi.sqlite3') == before


def test_two_fresh_processes_and_release_folders_reuse_same_wallet_and_config(legacy, tmp_path):
    project = Path(__file__).resolve().parents[1]
    home = tmp_path / 'service-account'
    home.mkdir()
    script = '''
import sys, socket, importlib
from pathlib import Path
Path.home = classmethod(lambda cls: Path(sys.argv[1]))
def no_network(*args, **kwargs):
    raise RuntimeError('Live network forbidden')
socket.socket.connect = no_network
sys.path.insert(0, sys.argv[2])
import config
from storage import initialize_store, prepare_upgrade, finish_upgrade
initialize_store(config.DATA_DIR, sys.argv[3], config.user_config_values())
importlib.reload(config)
assert config.PRICE_PER_DIAMOND == 30
assert bool(config.BOT_TOKEN == '123:previous-setting')
import db
from database import models
from services.balance_service import migrate_legacy_wallets, get_balance
digest, backup = prepare_upgrade(config.DATA_DIR, sys.argv[2])
db.init_db()
models.init_game_db()
migrate_legacy_wallets()
finish_upgrade(config.DATA_DIR, digest)
assert get_balance(101) == int(sys.argv[4])
assert db.get_user_settings(101)['muted_chats'] == [707]
from tts.voices import VOICE_SETTINGS_FILE
assert VOICE_SETTINGS_FILE == Path(config.DATA_DIR) / 'voice_settings.json'
import bot.core
assert bool(bot.core.BOT_TOKEN == '123:previous-setting')
print('fresh-process persistence PASS')
'''
    for version, balance in [('release-A', 3456), ('new-location-release-B', 21)]:
        app = tmp_path / version
        shutil.copytree(project, app, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache',
                                                                  'tests', 'logs', 'db', 'sessions'))
        if version.endswith('B'):
            path = home / 'PishroSelfData/db/tabchi.sqlite3'
            with closing(sqlite3.connect(path)) as conn:
                conn.execute('UPDATE users SET diamonds=21 WHERE telegram_id=101')
                conn.commit()
            # The new release ships different defaults: stored config must win.
            cfg = app / 'config.py'
            cfg.write_text(cfg.read_text().replace('PRICE_PER_DIAMOND = 40', 'PRICE_PER_DIAMOND = 999'))
        result = subprocess.run([sys.executable, '-c', script, str(home), str(app), str(legacy), str(balance)],
                                cwd=tmp_path, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert 'fresh-process persistence PASS' in result.stdout


def test_v011_format_upgrade_keeps_every_existing_row_and_can_restore_old_backup(legacy, tmp_path, monkeypatch):
    target = tmp_path / 'PishroSelfData'
    # Match the previous release's schema: no durable billing clocks yet.
    with closing(sqlite3.connect(legacy / 'db/tabchi.sqlite3')) as conn:
        conn.execute('DROP TABLE billing_clocks')
        conn.commit()
    storage.initialize_store(target, legacy, SETTINGS)
    meta_path = target / storage.META_FILE
    meta = json.loads(meta_path.read_text())
    meta.update(format_version=1, last_app_version='0.09.11')
    meta_path.write_text(json.dumps(meta))
    old_rows = table_rows(target / 'db/tabchi.sqlite3')
    config_before = (target / 'config.py').read_bytes()
    sessions_before = (target / 'sessions/user_101.txt').read_bytes()
    app = Path(__file__).resolve().parents[1]
    digest, backup = storage.prepare_upgrade(target, app)
    with zipfile.ZipFile(backup) as archive:
        assert json.loads(archive.read('BACKUP_MANIFEST.json'))['format_version'] == 1
        assert json.loads(archive.read(storage.META_FILE))['format_version'] == 1
    monkeypatch.setattr(models, 'TABCHI_DB_PATH', str(target / 'db/tabchi.sqlite3'))
    models.init_game_db()
    storage.finish_upgrade(target, digest)
    assert storage.validate_data(target, True)['format_version'] == 2
    for table, rows in old_rows.items():
        assert table_rows(target / 'db/tabchi.sqlite3')[table] == rows
    assert table_rows(target / 'db/tabchi.sqlite3')['billing_clocks'] == []
    assert (target / 'config.py').read_bytes() == config_before
    assert (target / 'sessions/user_101.txt').read_bytes() == sessions_before
    storage.finish_upgrade(target, digest)
    assert len(storage.validate_data(target, True)['format_migrations']) == 1
    preserved = storage.restore_backup(target, backup)
    assert preserved.is_dir()
    assert storage.validate_data(target, True)['format_version'] == 1
    assert table_rows(target / 'db/tabchi.sqlite3') == old_rows


def test_v012_new_defaults_do_not_reset_saved_tokens_prices_or_old_flags(tmp_path):
    saved = tmp_path / 'config.py'
    saved.write_text("BOT_TOKEN = '123:stored'\nPRICE_PER_DIAMOND = 30\nTRANSLATE_ENABLED = False\n")
    defaults = dict(SETTINGS, SELF_TRANSLATE_ENABLED=True, SELF_TRANSLATE_MODEL='gpt-4o')
    values = dict(defaults, **storage.load_user_config(saved, defaults))
    assert values['BOT_TOKEN'] == '123:stored'
    assert values['PRICE_PER_DIAMOND'] == 30
    assert values['TRANSLATE_ENABLED'] is False
    assert values['SELF_TRANSLATE_ENABLED'] is True
    assert values['SELF_TRANSLATE_MODEL'] == 'gpt-4o'


def test_billing_survives_two_processes_release_paths_and_snapshot_restore(legacy, tmp_path):
    project = Path(__file__).resolve().parents[1]
    home = tmp_path / 'service-account'
    home.mkdir()
    script = '''
import sys, socket, importlib, time
from pathlib import Path
Path.home = classmethod(lambda cls: Path(sys.argv[1]))
def no_network(*args, **kwargs):
    raise RuntimeError('Live network forbidden')
socket.socket.connect = no_network
sys.path.insert(0, sys.argv[2])
import config
from storage import initialize_store, prepare_upgrade, finish_upgrade, create_backup, restore_backup
initialize_store(config.DATA_DIR, sys.argv[3], config.user_config_values())
importlib.reload(config)
import db
from database import models
from services import balance_service as wallet, usage_service as usage
phase = int(sys.argv[4])
time.time = lambda: 1800000000.0 + (5410 if phase else 0)
digest, backup = prepare_upgrade(config.DATA_DIR, sys.argv[2])
db.init_db()
models.init_game_db()
wallet.migrate_legacy_wallets()
finish_upgrade(config.DATA_DIR, digest)
if phase == 0:
    db.touch_user(606)
    wallet.set_balance_from_core(606, 8)
    usage.enroll_verified(606)
else:
    usage.reconcile_all()  # wallet migration may already have settled the same dues
    assert wallet.get_balance(606) == 5
    with models._conn() as conn:
        assert conn.execute("SELECT -SUM(amount) FROM diamond_transactions WHERE user_id=606 AND description LIKE 'HALF_HOUR_USAGE:%'").fetchone()[0] == 3
    with models._conn() as conn:
        due = conn.execute('SELECT next_due FROM billing_clocks WHERE user_id=606').fetchone()[0]
    assert due == 1800007200.0
    snapshot = create_backup(config.DATA_DIR, 'billing-test')
    restore_backup(config.DATA_DIR, snapshot)
    assert usage.reconcile_all() == []
    assert wallet.get_balance(606) == 5
assert wallet.get_balance(101) == 3456
assert db.get_user_settings(101)['muted_chats'] == [707]
assert config.PRICE_PER_DIAMOND == 30
print('billing restart PASS')
'''
    for phase, name in enumerate(('release-A', 'new-folder-release-B')):
        app = tmp_path / name
        shutil.copytree(project, app, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache', 'tests', 'logs'))
        result = subprocess.run([sys.executable, '-c', script, str(home), str(app), str(legacy), str(phase)],
                                cwd=tmp_path, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert 'billing restart PASS' in result.stdout


def test_completed_billing_upgrade_refuses_missing_clock_table(legacy, tmp_path):
    target = tmp_path / 'PishroSelfData'
    storage.initialize_store(target, legacy, SETTINGS)
    storage.finish_upgrade(target, 'test-completed-release')
    with closing(sqlite3.connect(target / 'db/tabchi.sqlite3')) as conn:
        conn.execute('DROP TABLE billing_clocks')
        conn.commit()
    with pytest.raises(storage.StorageError, match='tables are missing'):
        storage.prepare_upgrade(target, Path(__file__).resolve().parents[1])


def test_v013_key_update_is_atomic_idempotent_and_preserves_other_data(legacy, tmp_path):
    root = tmp_path / 'PishroSelfData'
    storage.initialize_store(root, legacy, SETTINGS)
    config_path = root / 'config.py'
    with config_path.open('a') as handle:
        handle.write("\nCOINGECKO_API_KEY = 'old-test-key'\nEXTRA_USER_SETTING = [1, 2]\n")
    before = table_rows(root / 'db/tabchi.sqlite3')
    session = (root / 'sessions/user_101.txt').read_bytes()
    patch = {'COINGECKO_API_KEY': 'new-test-key', 'COINGECKO_API_KEY_TYPE': 'demo'}
    _, backup = storage.prepare_upgrade(root, Path(__file__).resolve().parents[1])
    assert storage.apply_config_update(root, 'v013-test', patch)
    with zipfile.ZipFile(backup) as archive:
        assert b'old-test-key' in archive.read('config.py')
    config_path.write_text(config_path.read_text().replace('new-test-key', 'later-user-edit'))
    assert not storage.apply_config_update(root, 'v013-test', patch)
    result = config_path.read_text()
    assert 'later-user-edit' in result and 'EXTRA_USER_SETTING = [1, 2]' in result
    assert '123:previous-setting' in result
    assert table_rows(root / 'db/tabchi.sqlite3') == before
    assert (root / 'sessions/user_101.txt').read_bytes() == session


def test_v013_failed_config_patch_never_marks_update_complete(legacy, tmp_path, monkeypatch):
    root = tmp_path / 'PishroSelfData'
    storage.initialize_store(root, legacy, SETTINGS)
    before = (root / 'config.py').read_bytes()
    def fail(*a, **kw): raise OSError('write failed')
    monkeypatch.setattr(storage, 'atomic_write', fail)
    with pytest.raises(OSError):
        storage.apply_config_update(root, 'v013-test', {'COINGECKO_API_KEY': 'new-test-key'})
    assert (root / 'config.py').read_bytes() == before



def test_premium_emoji_defaults_preserve_previous_release_state(legacy, tmp_path):
    """Emoji settings preserve old preferences, wallets, history and media."""
    defaults = {**SETTINGS, 'PREMIUM_EMOJI_ENABLED': True, 'PREMIUM_EMOJI_STYLE': 'smart'}
    target = tmp_path / 'premium-store'
    before = table_rows(legacy / 'db/tabchi.sqlite3')
    storage.initialize_store(target, legacy, defaults)
    saved = target / 'config.py'
    values = storage.load_user_config(saved, defaults)
    assert values['PREMIUM_EMOJI_ENABLED'] is True
    assert values['PREMIUM_EMOJI_STYLE'] == 'smart'
    assert values['PRICE_PER_DIAMOND'] == 30
    storage.write_user_config(saved, {**values, 'PREMIUM_EMOJI_ENABLED': False,
                                      'PREMIUM_EMOJI_STYLE': 'exact'})
    storage.initialize_store(target, tmp_path / 'another-release', defaults)
    again = storage.load_user_config(saved, defaults)
    assert again['PREMIUM_EMOJI_ENABLED'] is False
    assert again['PREMIUM_EMOJI_STYLE'] == 'exact'
    assert table_rows(target / 'db/tabchi.sqlite3') == before
    for name in ('sessions/user_101.txt', 'voice_settings.json', 'fosh_list.txt',
                 'message_cache/photo.jpg', 'banner/video.mp4', 'upload/audio.ogg'):
        assert (target / name).read_bytes() == (legacy / name).read_bytes()



def test_exact_smart_upgrade_preserves_old_emoji_preference_and_account_data(legacy, tmp_path):
    defaults = {**SETTINGS, 'PREMIUM_EMOJI_ENABLED': True, 'PREMIUM_EMOJI_STYLE': 'exact-smart'}
    root = tmp_path / 'emoji-exact-upgrade'
    storage.initialize_store(root, legacy, defaults)
    before = table_rows(root / 'db/tabchi.sqlite3')
    saved = root / 'config.py'
    values = storage.load_user_config(saved, defaults)
    assert values['PREMIUM_EMOJI_STYLE'] == 'exact-smart'
    storage.write_user_config(saved, {**values, 'PREMIUM_EMOJI_ENABLED': False, 'PREMIUM_EMOJI_STYLE': 'smart'})
    storage.initialize_store(root, tmp_path / 'new-release-location', defaults)
    restarted = storage.load_user_config(saved, defaults)
    assert restarted['PREMIUM_EMOJI_ENABLED'] is False and restarted['PREMIUM_EMOJI_STYLE'] == 'smart'
    from services.premium_emoji_injector import _style
    assert _style(restarted['PREMIUM_EMOJI_STYLE']) == 'exact-smart'
    assert table_rows(root / 'db/tabchi.sqlite3') == before
    for name in ('sessions/user_101.txt', 'voice_settings.json', 'fosh_list.txt',
                 'message_cache/photo.jpg', 'banner/video.mp4', 'upload/audio.ogg'):
        assert (root / name).read_bytes() == (legacy / name).read_bytes()


def test_prefix_keys_removed_and_legacy_values_ignored(legacy, tmp_path):
    """از DEBUG_FINAL به بعد سیستم Prefix حذف شده است.

    کلیدهای PREMIUM_EMOJI_PREFIX_* دیگر جزو پیش‌فرض‌ها نیستند؛ اگر کانفیگ
    قدیمی کاربر همچنان آن‌ها را داشته باشد، نادیده گرفته می‌شوند (بدون خطا)
    و بقیه تنظیمات و داده‌ها سالم می‌مانند.
    """
    assert 'PREMIUM_EMOJI_PREFIX_ENABLED' not in SETTINGS
    assert 'PREMIUM_EMOJI_PREFIX_MODE' not in SETTINGS
    assert 'PREMIUM_EMOJI_PREFIX_IDS' not in SETTINGS
    defaults = {**SETTINGS, 'PREMIUM_EMOJI_ENABLED': True}
    root = tmp_path / 'prefix-store'
    before = table_rows(legacy / 'db/tabchi.sqlite3')
    storage.initialize_store(root, legacy, defaults)
    saved = root / 'config.py'
    # کانفیگ ذخیره‌شده فقط کلیدهای شناخته‌شده را می‌نویسد/می‌خواند.
    values = storage.load_user_config(saved, defaults)
    assert 'PREMIUM_EMOJI_PREFIX_IDS' not in values
    assert 'PREMIUM_EMOJI_PREFIX_ENABLED' not in values
    assert values['PRICE_PER_DIAMOND'] == 30
    storage.write_user_config(saved, {**values, 'PREMIUM_EMOJI_ENABLED': False})
    storage.initialize_store(root, tmp_path / 'different-release', defaults)
    restarted = storage.load_user_config(saved, defaults)
    assert restarted['PREMIUM_EMOJI_ENABLED'] is False
    assert table_rows(root / 'db/tabchi.sqlite3') == before
    for name in ('sessions/user_101.txt', 'voice_settings.json', 'fosh_list.txt',
                 'message_cache/photo.jpg', 'banner/video.mp4', 'upload/audio.ogg'):
        assert (root / name).read_bytes() == (legacy / name).read_bytes()
