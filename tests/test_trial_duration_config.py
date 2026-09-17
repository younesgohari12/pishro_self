def test_trial_default_config():
    import config
    assert config.TRIAL_DURATION_HOURS == 24


def test_trial_config_override(monkeypatch):
    import config
    monkeypatch.setattr(config, "TRIAL_DURATION_HOURS", 48)
    assert config.TRIAL_DURATION_HOURS == 48


def test_grant_revoke_behavior_unchanged():
    from services import trial_manager
    assert trial_manager.grant_admin_trial(909090, 1) is True
    assert trial_manager.is_active(909090) is True
    assert trial_manager.revoke(909090) is True
    assert trial_manager.is_active(909090) is False
