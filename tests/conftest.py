"""Tests always use throwaway storage and never contact Telegram."""
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
import tempfile
import atexit

# Tests must never read or initialize the real server's persistent config/data.
_test_home = tempfile.TemporaryDirectory(prefix='pishro-tests-')
atexit.register(_test_home.cleanup)
# Path.home is a normal inherited class method on supported Python versions.
# Do not index Path.__dict__ directly: it raises KeyError on versions where
# pathlib implements it through an inherited descriptor.
_home_method = Path.home
try:
    Path.home = classmethod(lambda cls: Path(_test_home.name))
    import config
finally:
    Path.home = _home_method

import db
from database import models


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(models, 'TABCHI_DB_PATH', str(tmp_path / 'test.sqlite3'))
    monkeypatch.setattr(db, 'DB_DIR', str(tmp_path))
    for attr, name in [('INDEX_FILE','index'), ('META_FILE','meta'), ('GLOBAL_FILE','global'), ('PAYMENTS_FILE','payments')]:
        monkeypatch.setattr(db, attr, str(tmp_path / (name + '.json')))
    for attr, value in [('_cache', {}), ('_index', {}), ('_meta', {'latest_shard':0}), ('_initialized',False)]:
        monkeypatch.setattr(db, attr, value)
    def no_network(*args, **kwargs):
        raise AssertionError('Live network is forbidden in tests')
    monkeypatch.setattr(socket.socket, 'connect', no_network)
