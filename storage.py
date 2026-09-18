"""Persistent data lifecycle. This module has no app imports or import-time writes.

Never move the default root into a release directory. Future releases must
retain this contract and bump STORAGE_FORMAT for incompatible storage changes.
"""
from __future__ import annotations

import ast
from contextlib import contextmanager, closing
from datetime import datetime, timezone
import hashlib
import json
import os
import operator
from pathlib import Path
import shutil
import sqlite3
import tempfile
import uuid
import zipfile

APP_VERSION = '0.09.20'
STORAGE_FORMAT = 2
READABLE_FORMATS = (1, 2)
DATA_DIRS = ('db', 'sessions', 'banner', 'upload', 'message_cache')
DATA_FILES = ('voice_settings.json', 'fosh_list.txt')
META_FILE = 'storage_meta.json'
LOCATION_FILE = 'data_location.json'


class StorageError(RuntimeError):
    pass


def default_data_dir():
    return Path.home() / 'PishroSelfData'


def backup_dir(root):
    root = Path(root).resolve()
    return root.parent / (root.name + 'Backups')


def _now():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _private_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True, mode=0o700)


def atomic_write(path, data):
    path = Path(path)
    _private_dir(path.parent)
    fd, tmp = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        if os.name != 'nt':
            dirfd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _write_json(path, value):
    atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode())


def _read_json(path):
    try:
        with Path(path).open(encoding='utf-8-sig') as handle:
            result = json.load(handle)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, OSError):
        raise StorageError(f'Invalid or missing JSON: {Path(path).name}') from None


@contextmanager
def process_lock(root):
    """Held throughout runtime; backups/restore/migration use the same OS lock."""
    root = Path(root).resolve()
    _private_dir(root.parent)
    handle = open(root.parent / (root.name + '.lock'), 'a+b')
    locked = False
    try:
        try:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0)
                if not handle.read(1):
                    handle.write(b'0')
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except (OSError, BlockingIOError):
            raise StorageError('Another instance or data operation is already running.') from None
        yield
    finally:
        if locked:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def load_user_config(path, defaults):
    """Python assignment files only, never execute code from stored settings."""
    path = Path(path)
    if not path.exists():
        if (path.parent / META_FILE).exists():
            raise StorageError('Persistent config.py is missing; restore it before starting.')
        return {}
    try:
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        values = {}
        for node in tree.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                continue
            if not isinstance(node, ast.Assign) or len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                raise ValueError()
            name = node.targets[0].id
            value = ast.literal_eval(node.value)
            if name in defaults:
                if type(value) is not type(defaults[name]):
                    raise ValueError()
                values[name] = value
        return values
    except (OSError, SyntaxError, ValueError, TypeError):
        raise StorageError('Persistent config.py is invalid; no defaults were written over it.') from None


def write_user_config(path, settings):
    lines = ['# Persistent settings. Updates must never overwrite this file.']
    for key, value in settings.items():
        try:
            ast.literal_eval(repr(value))
        except (ValueError, SyntaxError):
            raise StorageError(f'Non-literal setting: {key}') from None
        lines.append(f'{key} = {value!r}')
    atomic_write(path, ('\n'.join(lines) + '\n').encode('utf-8'))


def legacy_config_values(path, defaults):
    """Import literal/numeric old settings without executing the old project."""
    path = Path(path)
    result = dict(defaults)
    if not path.is_file():
        return result
    operations = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                  ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv}
    def value(node):
        if isinstance(node, ast.Name) and node.id in result:
            return result[node.id]
        if isinstance(node, ast.IfExp):
            return value(node.body if value(node.test) else node.orelse)
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, right = value(node.left), value(node.comparators[0])
            if isinstance(node.ops[0], ast.Eq):
                return left == right
            if isinstance(node.ops[0], ast.NotEq):
                return left != right
            raise ValueError()
        if isinstance(node, ast.Call) and not node.keywords:
            if isinstance(node.func, ast.Name) and node.func.id in ('min', 'max'):
                args = [value(arg) for arg in node.args]
                if args and all(type(arg) in (int, float) for arg in args):
                    return (min if node.func.id == 'min' else max)(args)
            if isinstance(node.func, ast.Attribute) and node.func.attr in ('lower', 'upper', 'strip') and not node.args:
                text = value(node.func.value)
                if isinstance(text, str):
                    return getattr(text, node.func.attr)()
            raise ValueError()
        if isinstance(node, ast.BinOp) and type(node.op) in operations:
            left, right = value(node.left), value(node.right)
            if type(left) not in (int, float) or type(right) not in (int, float):
                raise ValueError()
            return operations[type(node.op)](left, right)
        return ast.literal_eval(node)
    try:
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    except (OSError, SyntaxError):
        raise StorageError('Cannot parse the previous config.py; original file is unchanged.') from None
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            key = node.targets[0].id
            if key not in result:
                continue
            try:
                previous = value(node.value)
            except (ValueError, TypeError, ArithmeticError):
                raise StorageError(f'Previous setting {key} needs a literal value in config.py before migration.') from None
            if type(previous) is type(result[key]):
                result[key] = previous
            else:
                raise StorageError(f'Previous setting {key} has an incompatible type; migration was stopped.')
    return result


def has_legacy_data(source):
    source = Path(source)
    return any(p.is_file() for name in ('db', 'sessions') for p in (source / name).rglob('*'))


def _all_data_files(root, include_meta=True):
    root = Path(root)
    files = []
    for name in DATA_DIRS:
        directory = root / name
        if directory.is_symlink():
            raise StorageError(f'Symlinked data directory must be imported explicitly: {name}')
        for p in sorted(directory.rglob('*')):
            if p.is_symlink():
                raise StorageError(f'Symlink in data: {p.name}')
            if p.is_file():
                files.append(p)
    for name in (*DATA_FILES, 'config.py', *([META_FILE] if include_meta else [])):
        path = root / name
        if path.is_symlink():
            raise StorageError(f'Symlink in data: {name}')
        if path.is_file():
            files.append(path)
    return files


def _sqlite_file(path):
    with Path(path).open('rb') as handle:
        return handle.read(16) == b'SQLite format 3\x00'


def _ro_sqlite(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=15)


def _check_sqlite(path):
    try:
        with closing(_ro_sqlite(path)) as conn:
            if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise StorageError(f'SQLite integrity check failed: {Path(path).name}')
    except sqlite3.Error:
        raise StorageError(f'Cannot read SQLite database: {Path(path).name}') from None


def validate_data(root, require_meta=False):
    root = Path(root)
    meta = _read_json(root / META_FILE) if require_meta else {}
    if meta and (type(meta.get('format_version')) is not int or meta['format_version'] not in READABLE_FORMATS):
        raise StorageError('Unsupported data format; refusing to downgrade or reset data.')
    for name in meta.get('required_files', []):
        if name not in ('db/tabchi.sqlite3', 'config.py'):
            raise StorageError('Invalid required-file entry in data metadata.')
        if not (root / name).is_file():
            raise StorageError(f'Required data file is missing: {name}')
    for path in (root / 'db').glob('*.json'):
        try:
            _read_json(path)
        except StorageError:
            # Runtime's existing last-known-good JSON recovery remains allowed.
            _read_json(str(path) + '.bak')
    index_path = root / 'db' / 'index.json'
    if index_path.exists():
        try:
            index = _read_json(index_path)
        except StorageError:
            index = _read_json(str(index_path) + '.bak')
        for shard in index.values():
            if not str(shard).isdigit() or not (root / 'db' / f'users_{int(shard)}.json').is_file():
                raise StorageError('User index references a missing shard; restore data before starting.')
    database = root / 'db' / 'tabchi.sqlite3'
    if database.exists():
        _check_sqlite(database)
        required_tables = meta.get('required_tables', [])
        if required_tables:
            with closing(_ro_sqlite(database)) as conn:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not set(required_tables).issubset(tables):
                raise StorageError('Required wallet/billing tables are missing; restore data before starting.')
    voice = root / 'voice_settings.json'
    if voice.exists():
        _read_json(voice)
    return meta


def _copy_data(source, target, include_meta=True):
    """Snapshot SQLite using its backup API, including committed WAL contents."""
    source, target = Path(source), Path(target)
    files = _all_data_files(source, include_meta)
    for name in DATA_DIRS:
        _private_dir(target / name)
    for path in files:
        if path.name.endswith(('-wal', '-shm', '-journal')):
            parent_db = path.with_name(path.name.rsplit('-', 1)[0])
            if parent_db.exists() and _sqlite_file(parent_db):
                continue
        rel = path.relative_to(source)
        dest = target / rel
        _private_dir(dest.parent)
        if _sqlite_file(path):
            with closing(_ro_sqlite(path)) as src, closing(sqlite3.connect(dest)) as dst:
                src.backup(dst)
            dest.chmod(0o600)
            _check_sqlite(dest)
        else:
            before = path.stat()
            shutil.copyfile(path, dest)
            dest.chmod(0o600)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise StorageError('Source data changed while copying; stop the old version first.')


def _remap_media_paths(stage, old_root, new_root):
    database = Path(stage) / 'db' / 'tabchi.sqlite3'
    if not database.exists():
        return
    with closing(sqlite3.connect(database)) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, column in [('message_cache', 'media_path'), ('banners', 'file_id')]:
            if table not in tables:
                continue
            cols = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
            if column not in cols:
                continue
            for rowid, value in conn.execute(f'SELECT rowid, {column} FROM {table}').fetchall():
                if not isinstance(value, str):
                    continue
                for directory in DATA_DIRS:
                    prefix = str(Path(old_root).resolve() / directory) + os.sep
                    if value.startswith(prefix):
                        relative = Path(directory) / value[len(prefix):]
                        if (Path(stage) / relative).is_file():
                            conn.execute(f'UPDATE {table} SET {column}=? WHERE rowid=?',
                                         (str(Path(new_root) / relative), rowid))
                        break
        conn.commit()


def create_backup(root, reason='manual', *, destination=None, original_root=None):
    root = Path(root).resolve()
    validate_data(root, require_meta=(root / META_FILE).exists())
    destination = Path(destination) if destination else backup_dir(root)
    _private_dir(destination)
    label = ''.join(c for c in reason if c.isalnum() or c in '-_')[:50] or 'backup'
    output = destination / f'{_now()}_{label}_{uuid.uuid4().hex[:8]}.zip'
    with tempfile.TemporaryDirectory(prefix='.snapshot-', dir=destination) as scratch:
        stage = Path(scratch)
        _copy_data(root, stage)
        validate_data(stage, require_meta=(root / META_FILE).exists())
        files = [p for p in stage.rglob('*') if p.is_file()]
        manifest = {str(p.relative_to(stage)).replace(os.sep, '/'):
                    file_hash(p) for p in files}
        temporary = output.with_suffix('.pending')
        try:
            with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
                for path in files:
                    archive.write(path, str(path.relative_to(stage)))
                archive.writestr('BACKUP_MANIFEST.json', json.dumps({
                    'format_version': _read_json(stage / META_FILE).get('format_version', STORAGE_FORMAT) if (stage / META_FILE).exists() else STORAGE_FORMAT, 'original_root': str(original_root or root), 'sha256': manifest,
                }))
            with zipfile.ZipFile(temporary) as archive:
                if archive.testzip() is not None:
                    raise StorageError('Backup ZIP verification failed.')
            temporary.chmod(0o600)
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
    return output


def initialize_store(root, source, settings, *, allow_empty=False):
    """Copy once into a staging directory. The original data is never removed."""
    root, source = Path(root).resolve(), Path(source).resolve()
    if (root / META_FILE).exists():
        validate_data(root, require_meta=True)
        return None
    if (source / LOCATION_FILE).exists():
        _read_json(source / LOCATION_FILE)
        raise StorageError('This old installation was already migrated. The active store is missing '
                           'or belongs to another server account; restore its backup, not the stale copy.')
    if root.exists() and any(root.iterdir()):
        raise StorageError('Destination contains unregistered data; refusing to merge or overwrite it.')
    if root == source or root in source.parents or source in root.parents:
        raise StorageError('Persistent data must be outside the program/source directory.')
    legacy = has_legacy_data(source)
    if not legacy and not allow_empty:
        raise StorageError('Previous data was not found. Use manage_data.py migrate --from OLD_DIRECTORY; '
                           'use init only for a new installation.')
    _private_dir(root.parent)
    stage = Path(tempfile.mkdtemp(prefix=root.name + '.import-', dir=root.parent))
    try:
        if legacy:
            validate_data(source)
            _copy_data(source, stage, include_meta=False)
            settings = legacy_config_values(source / 'config.py', settings)
        else:
            for name in DATA_DIRS:
                _private_dir(stage / name)
        for name in DATA_FILES:
            if not (stage / name).exists():
                original = source / name
                if not original.is_file():
                    original = Path(__file__).parent / 'defaults' / name
                if original.is_file():
                    shutil.copyfile(original, stage / name)
        write_user_config(stage / 'config.py', settings)
        _write_json(stage / META_FILE, {
            'format_version': STORAGE_FORMAT, 'created_at': _now(),
            'imported_from': str(source) if legacy else None,
            'last_app_version': None, 'last_app_digest': None,
            'required_files': ['config.py'],
        })
        validate_data(stage, require_meta=True)
        if legacy:
            create_backup(stage, 'before_import', destination=backup_dir(root), original_root=source)
            _remap_media_paths(stage, source, root)
            # Prevent a different OS account from reimporting this stale source
            # after the active store has accumulated newer balances/data.
            _write_json(source / LOCATION_FILE, {'data_dir': str(root), 'created_at': _now()})
        if root.exists():
            root.rmdir()  # only the previously verified empty destination
        os.replace(stage, root)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return root


def release_digest(app_root):
    digest = hashlib.sha256()
    app_root = Path(app_root)
    for path in sorted([*app_root.rglob('*.py'), *app_root.glob('requirements*.txt')]):
        rel = path.relative_to(app_root)
        if any(part in {'tests', '__pycache__', *DATA_DIRS} for part in rel.parts):
            continue
        digest.update(str(rel).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def prepare_upgrade(root, app_root):
    meta = validate_data(root, require_meta=True)
    digest = release_digest(app_root)
    if meta.get('last_app_digest') != digest:
        backup = create_backup(root, 'before_' + APP_VERSION)
        return digest, backup
    return digest, None


def finish_upgrade(root, digest):
    meta = validate_data(root, require_meta=True)
    # Version 1 -> 2 retains every legacy table/row; init_game_db adds billing_clocks.
    # Persist the format gate before any session is enrolled, blocking v0.09.11
    # from running its incompatible runtime-only billing against these clocks.
    if meta.get('format_version') == 1:
        meta.setdefault('format_migrations', []).append({'from': 1, 'to': 2, 'at': _now()})
    meta.update(format_version=STORAGE_FORMAT, last_app_version=APP_VERSION, last_app_digest=digest, last_success_at=_now())
    if (Path(root) / 'db' / 'tabchi.sqlite3').is_file():
        meta['required_files'] = ['config.py', 'db/tabchi.sqlite3']
        meta['required_tables'] = ['users', 'diamond_transactions', 'wallet_usage', 'billing_clocks']
    _write_json(Path(root) / META_FILE, meta)


def restore_backup(root, backup):
    """Verify first, preserve current data, then atomically swap directory trees."""
    root, backup = Path(root).resolve(), Path(backup).resolve()
    _private_dir(root.parent)
    stage = Path(tempfile.mkdtemp(prefix=root.name + '.restore-', dir=root.parent))
    preserved = root.with_name(root.name + '.before-restore-' + _now() + '-' + uuid.uuid4().hex[:8])
    moved = False
    try:
        with zipfile.ZipFile(backup) as archive:
            manifest = json.loads(archive.read('BACKUP_MANIFEST.json'))
            if manifest.get('format_version') not in READABLE_FORMATS:
                raise StorageError('Unsupported backup format.')
            expected = manifest['sha256']
            names = archive.namelist()
            if len(names) != len(set(names)) or set(names) != set(expected) | {'BACKUP_MANIFEST.json'}:
                raise StorageError('Backup file list does not match its manifest.')
            for name, digest in expected.items():
                path = Path(name)
                if (path.is_absolute() or '..' in path.parts or '\\' in name
                        or ':' in name or not path.parts):
                    raise StorageError('Unsafe path in backup.')
                dest = stage / path
                _private_dir(dest.parent)
                with archive.open(name) as src, dest.open('wb') as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
                dest.chmod(0o600)
                if file_hash(dest) != digest:
                    raise StorageError('Backup checksum mismatch.')
        if not (stage / META_FILE).exists():
            raise StorageError('This is an original import backup; import its data with migrate, not restore.')
        validate_data(stage, require_meta=True)
        load_user_config(stage / 'config.py', {})
        _remap_media_paths(stage, manifest.get('original_root', str(root)), root)
        meta = _read_json(stage / META_FILE)
        meta.update(last_app_digest=None, restored_at=_now())
        _write_json(stage / META_FILE, meta)
        if root.exists():
            os.replace(root, preserved)
            moved = True
        try:
            os.replace(stage, root)
        except BaseException:
            if moved:
                os.replace(preserved, root)
            raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return preserved if moved else None


def apply_config_update(root, update_id, patch):
    """Apply an explicitly authorized release patch once, after pre-upgrade backup.

    Patch and marker are a single atomic file write; later user edits win.
    All unrelated literal settings, including unknown keys, are retained.
    Caller holds process_lock for the entire startup.
    """
    path = Path(root) / 'config.py'
    load_user_config(path, {})  # validate without executing stored Python
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    settings = {node.targets[0].id: ast.literal_eval(node.value)
                for node in tree.body if isinstance(node, ast.Assign)}
    markers = settings.get('_APPLIED_RELEASE_UPDATES', [])
    if not isinstance(markers, list) or not all(isinstance(item, str) for item in markers):
        raise StorageError('Invalid config update history; config was not changed.')
    if update_id in markers:
        return False
    settings.update(patch)
    settings['_APPLIED_RELEASE_UPDATES'] = [*markers, update_id]
    write_user_config(path, settings)
    return True
