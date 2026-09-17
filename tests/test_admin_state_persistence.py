
import time

import db
from handlers.admin import AdminController


def test_admin_state_storage(tmp_path, monkeypatch):
    path = tmp_path / "admin_states.json"
    monkeypatch.setattr(db, "ADMIN_STATES_FILE", str(path))
    db.save_admin_state(123, {"step": "BROADCAST"})
    assert db.load_admin_states()[123]["step"] == "BROADCAST"


def test_admin_state_restore_after_restart_simulation(tmp_path, monkeypatch):
    path = tmp_path / "admin_states.json"
    monkeypatch.setattr(db, "ADMIN_STATES_FILE", str(path))
    db.save_admin_state(123, {"step": "USER_SEARCH"})

    controller = AdminController.__new__(AdminController)
    controller.states = db.load_admin_states()

    assert controller.states[123]["step"] == "USER_SEARCH"


def test_admin_state_expire(tmp_path, monkeypatch):
    path = tmp_path / "admin_states.json"
    monkeypatch.setattr(db, "ADMIN_STATES_FILE", str(path))
    monkeypatch.setattr(db, "ADMIN_STATE_TTL", 1)

    db.save_admin_state(123, {"step": "TICKET_REPLY"})

    import json
    data = json.loads(path.read_text(encoding="utf8"))
    data["123"]["_expires_at"] = time.time() - 1
    path.write_text(json.dumps(data), encoding="utf8")

    assert 123 not in db.load_admin_states()


def test_admin_state_get_state_expire_clears_ram_and_db(tmp_path, monkeypatch):
    path = tmp_path / "admin_states.json"
    monkeypatch.setattr(db, "ADMIN_STATES_FILE", str(path))
    monkeypatch.setattr(db, "ADMIN_STATE_TTL", 10)

    controller = AdminController.__new__(AdminController)
    controller.states = {}
    controller._set_state(123, {"step": "BROADCAST"})

    import json
    data = json.loads(path.read_text(encoding="utf8"))
    data["123"]["_expires_at"] = time.time() - 1
    path.write_text(json.dumps(data), encoding="utf8")
    controller.states = db.load_admin_states()
    controller.states[123] = data["123"]

    assert controller._get_state(123) is None
    assert 123 not in controller.states
    assert 123 not in db.load_admin_states()
