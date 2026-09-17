"""Runtime access requires an enabled account and paid or trial entitlement."""
import db
from database import models
from services import balance_service, trial_manager


def can_run(user_id):
    try:
        if models.is_user_banned(user_id):
            return False
        if not db.get_user_settings(user_id).get('self_enabled', False):
            return False
        return trial_manager.is_active(user_id) or balance_service.get_balance(user_id) > 0
    except Exception:
        return False
