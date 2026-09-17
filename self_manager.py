# self_manager.py
"""مدیریت اجرای سشن‌های متعدد"""
import os
import asyncio
from config import SESSIONS_DIR, API_ID, API_HASH
from services.logging_service import get_logger
from services.session_restore import session_user_id

logger = get_logger('self_manager')

running_sessions = {}
active_clients = {}


def register_client(user_id, client):
    active_clients[int(user_id)] = client


def unregister_client(user_id, client=None):
    uid = int(user_id)
    if client is None or active_clients.get(uid) is client:
        active_clients.pop(uid, None)


def get_client_for_user(user_id):
    return active_clients.get(int(user_id))


def _session_finished(session_name, task):
    """Remove completed/crashed tasks so they can be started again."""
    if running_sessions.get(session_name) is task:
        running_sessions.pop(session_name, None)

    if task.cancelled():
        return

    try:
        exc = task.exception()
    except Exception:
        exc = None

    if exc is not None:
        logger.error("Session %s crashed: %s: %s", session_name, type(exc).__name__, exc)
        print(f"❌ سشن {session_name} با خطا متوقف شد: {exc}")


def list_sessions():
    """لیست همه سشن‌های ذخیره شده"""
    sessions = []
    if os.path.exists(SESSIONS_DIR):
        for f in os.listdir(SESSIONS_DIR):
            if (f.endswith('.txt') and session_user_id(f[:-4]) is not None
                    and os.path.isfile(os.path.join(SESSIONS_DIR, f))):
                sessions.append(f[:-4])  # بدون .txt
    return sorted(sessions)

def get_session_string(session_name):
    """خواندن سشن از فایل"""
    if session_user_id(session_name) is None:
        return None
    path = os.path.join(SESSIONS_DIR, f"{session_name}.txt")
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8-sig') as f:
                return f.read().strip()
        except (OSError, UnicodeError) as exc:
            logger.warning("Could not read session file %s: %s: %s", session_name, type(exc).__name__, exc)
    return None

async def start_session(session_name, *, restore=False):
    """شروع یک سشن"""
    from services.access_service import can_run
    uid = session_user_id(session_name)
    if uid is None:
        return False
    # Restore saved connections independently of application account records.
    # Command/clock/scheduler permissions still go through can_run().
    if not restore and not can_run(uid):
        return False
    from self import run_self
    
    existing = running_sessions.get(session_name)
    if existing is not None:
        if existing.done():
            running_sessions.pop(session_name, None)
        else:
            print(f"⚠️ سشن {session_name} قبلاً در حال اجراست")
            return False
    
    session_string = get_session_string(session_name)
    if not session_string:
        print(f"❌ سشن {session_name} یافت نشد")
        return False
    
    session_path = os.path.join(SESSIONS_DIR, session_name)
    task = asyncio.create_task(run_self(session_path, session_string))
    running_sessions[session_name] = task
    task.add_done_callback(lambda done, name=session_name: _session_finished(name, done))
    print(f"🔄 اتصال سشن {session_name} در حال بررسی است")
    return True

async def delete_session(session_name):
    """Delete one self session completely."""
    uid = session_user_id(session_name)
    client = active_clients.get(int(uid)) if uid is not None else None

    if client is not None:
        try:
            if getattr(client, "is_connected", lambda: False)():
                await client.log_out()
        except Exception as exc:
            logger.warning("session logout failed name=%s error=%s", session_name, type(exc).__name__)
        try:
            await client.disconnect()
        except Exception:
            pass
        if uid is not None:
            unregister_client(uid, client)

    task = running_sessions.get(session_name)
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        running_sessions.pop(session_name, None)

    if uid is not None:
        active_clients.pop(int(uid), None)

    path = os.path.join(SESSIONS_DIR, f"{session_name}.txt")
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as exc:
        logger.warning("session remove failed name=%s error=%s", session_name, type(exc).__name__)
        return False

    return True


async def stop_session(session_name):
    """توقف یک سشن"""
    if session_name in running_sessions:
        task = running_sessions[session_name]
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        running_sessions.pop(session_name, None)
        print(f"🛑 سشن {session_name} متوقف شد")
        return True
    return False

async def start_all_sessions():
    """شروع همه سشن‌ها"""
    sessions = list_sessions()
    print(f"\n{'='*50}")
    print(f"📂 یافت شد: {len(sessions)} سشن")
    print(f"{'='*50}")
    
    tasks = []
    for session_name in sessions:
        try:
            if await start_session(session_name, restore=True):
                tasks.append(running_sessions[session_name])
        except Exception as exc:
            logger.warning('Session restore failed name=%s error=%s',
                           session_name, type(exc).__name__)
    
    print(f"🔄 بازیابی {len(tasks)} سشن زمان‌بندی شد\n")
    return tasks
