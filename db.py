"""
دیتابیس JSON با شاردینگ + کیف پول + پرداخت‌ها + تنظیمات سراسری + رفرال + صورتحساب
"""
import os
import json
import time
import threading
import copy
import tempfile
import shutil

from config import DB_DIR, MAX_DB_FILE_SIZE, CLOCK_FONTS, get_tehran_time
from services.logging_service import log_db_error, get_logger

_logger = get_logger('db')

DEFAULT_SETTINGS = {
    'bio_clock': False,
    'lastname_clock': False,
    'bio_font': 1,
    'lastname_font': 1,
    'base_bio': '',
    'base_first_name': '',
    'base_last_name': '',
    # کیف پول
    'diamonds': 0,
    'total_diamonds_bought': 0,
    'total_spent_toman': 0,
    'username': '',
    'first_name': '',
    'registered_at': '',
    # مدیریت سلف
    'self_enabled': True,
    # فونت خودکار پیام‌های خروجی
    'message_font_enabled': False,
    'message_font_style': 'bold',
    # تبدیل ایموجی یونیکد به Custom Emoji در خروجی سلف (None = پیش‌فرض config)
    'premium_emoji_converter': None,
    'muted_chats': [],
    'enemy_chats': [],
    # هشدارها و صورتحساب
    'alerts_enabled': True,
    'bill_seconds': 0.0,
    'last_alert_hours': 9999,
    # رفرال
    'referred_by': 0,
    'ref_credited': False,
    'ref_count': 0,
    'ref_earned': 0,
}

DEFAULT_GLOBAL = {
    'card_number': '',
    'card_holder': '',
    'bot_enabled': True,
    'force_channels': [],
    'next_pay_id': 1001,
    'stats': {
        'approved': 0,
        'rejected': 0,
        'diamonds_sold': 0,
        'revenue_toman': 0,
        'fees_collected': 0,
        'ref_rewards': 0,
        'billing_burned': 0,
    },
}

_lock = threading.RLock()
_cache = {}
_index = {}
_meta = {'latest_shard': 0}
_initialized = False

INDEX_FILE = os.path.join(DB_DIR, 'index.json')
META_FILE = os.path.join(DB_DIR, 'meta.json')
GLOBAL_FILE = os.path.join(DB_DIR, 'global.json')
PAYMENTS_FILE = os.path.join(DB_DIR, 'payments.json')


# Admin panel transient states (persistent with TTL)
ADMIN_STATES_FILE = os.path.join(DB_DIR, 'admin_states.json')
ADMIN_STATE_TTL = 600


def load_admin_states():
    """Load only admin panel states and remove expired entries."""
    now = time.time()
    with _lock:
        data = _read_json(ADMIN_STATES_FILE, {})
        if not isinstance(data, dict):
            data = {}
        cleaned = {}
        for uid, state in data.items():
            if isinstance(state, dict) and float(state.get('_expires_at', 0)) > now:
                cleaned[str(uid)] = state
        if cleaned != data:
            _write_json(ADMIN_STATES_FILE, cleaned)
        return {int(uid): state for uid, state in cleaned.items()}


def save_admin_state(uid, state):
    """Persist an admin panel state with expiration."""
    with _lock:
        data = _read_json(ADMIN_STATES_FILE, {})
        data[str(int(uid))] = {
            **state,
            '_expires_at': time.time() + ADMIN_STATE_TTL,
        }
        _write_json(ADMIN_STATES_FILE, data)


def delete_admin_state(uid):
    with _lock:
        data = _read_json(ADMIN_STATES_FILE, {})
        data.pop(str(int(uid)), None)
        _write_json(ADMIN_STATES_FILE, data)


def _shard_path(num):
    return os.path.join(DB_DIR, f"users_{num}.json")


def _read_json(path, default):
    if not os.path.exists(path):
        return copy.deepcopy(default)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as exc:
        log_db_error('read_json', exc, path=os.path.basename(path))
        backup = path + '.bak'
        if os.path.exists(backup):
            try:
                with open(backup, 'r', encoding='utf-8') as f:
                    recovered = json.load(f)
                _logger.warning('Recovered JSON from backup file=%s', os.path.basename(path))
                return recovered
            except Exception as backup_exc:
                log_db_error('read_json_backup', backup_exc, path=os.path.basename(backup))
        from storage import StorageError
        raise StorageError(f'JSON data and its backup are unreadable: {os.path.basename(path)}') from None


def _write_json(path, data):
    """Durable atomic JSON write with one known-good backup."""
    directory = os.path.dirname(path) or '.'
    os.makedirs(directory, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', dir=directory, prefix='.__pishro_', delete=False
        ) as f:
            temporary = f.name
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())

        if os.path.exists(path):
            # Never overwrite a known-good backup with a corrupted primary.
            primary_is_valid = False
            try:
                with open(path, 'r', encoding='utf-8') as current_file:
                    json.load(current_file)
                primary_is_valid = True
            except Exception:
                _logger.warning(
                    'Primary JSON is invalid; preserving existing backup file=%s',
                    os.path.basename(path),
                )

            if primary_is_valid:
                backup_tmp = path + '.bak.tmp'
                try:
                    shutil.copy2(path, backup_tmp)
                    os.replace(backup_tmp, path + '.bak')
                finally:
                    if os.path.exists(backup_tmp):
                        try:
                            os.unlink(backup_tmp)
                        except OSError:
                            pass

        os.replace(temporary, path)
        temporary = None
    except Exception as exc:
        log_db_error('write_json', exc, path=os.path.basename(path))
        raise
    finally:
        if temporary and os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _default_settings():
    return copy.deepcopy(DEFAULT_SETTINGS)


def _normalize_settings(data):
    d = _default_settings()

    if isinstance(data, dict):
        d.update(data)

    for key in ('bio_clock', 'lastname_clock'):
        d[key] = bool(d.get(key, False))

    d['self_enabled'] = bool(d.get('self_enabled', True))
    d['message_font_enabled'] = bool(d.get('message_font_enabled', False))
    # None means "panel toggle never touched" -> release config default applies.
    converter_flag = d.get('premium_emoji_converter', None)
    d['premium_emoji_converter'] = None if converter_flag is None else bool(converter_flag)
    font_style = str(d.get('message_font_style', 'bold') or 'bold').strip().lower()
    if font_style not in {'bold', 'italic', 'bold_italic', 'strike', 'underline', 'monospace'}:
        font_style = 'bold'
    d['message_font_style'] = font_style
    d['alerts_enabled'] = bool(d.get('alerts_enabled', True))
    d['ref_credited'] = bool(d.get('ref_credited', False))

    for key in ('bio_font', 'lastname_font'):
        try:
            val = int(d.get(key, 1))
        except Exception:
            val = 1
        if val not in CLOCK_FONTS:
            val = 1
        d[key] = val

    for key in ('base_bio', 'base_first_name', 'base_last_name',
                'username', 'first_name', 'registered_at'):
        val = d.get(key, '')
        d[key] = '' if val is None else str(val)

    for key in ('diamonds', 'total_diamonds_bought', 'total_spent_toman',
                'referred_by', 'ref_count', 'ref_earned'):
        try:
            d[key] = max(0, int(d.get(key, 0)))
        except Exception:
            d[key] = 0

    try:
        d['last_alert_hours'] = int(d.get('last_alert_hours', 9999))
    except Exception:
        d['last_alert_hours'] = 9999

    try:
        d['bill_seconds'] = float(d.get('bill_seconds', 0))
    except Exception:
        d['bill_seconds'] = 0.0

    for key in ('muted_chats', 'enemy_chats'):
        val = d.get(key, [])
        if not isinstance(val, list):
            val = []
        out = []
        for x in val:
            try:
                out.append(int(x))
            except Exception:
                pass
        d[key] = out

    return d


def _has_shard_files():
    try:
        for f in os.listdir(DB_DIR):
            if f.startswith('users_') and f.endswith('.json'):
                return True
    except Exception:
        pass
    return False


def _rebuild_index(save=True):
    """Rebuild the derived user->shard index from durable shard files."""
    global _index

    previous_index = dict(_index) if isinstance(_index, dict) else {}
    new_index = {}
    shard_files = []

    try:
        for name in os.listdir(DB_DIR):
            if not (name.startswith('users_') and name.endswith('.json')):
                continue
            try:
                shard_files.append((int(name[6:-5]), name))
            except (TypeError, ValueError):
                continue
    except OSError:
        shard_files = []

    # Oldest -> newest means a duplicate left by safe cleanup resolves to the
    # newest durable copy.
    for num, _name in sorted(shard_files):
        data = _read_json(_shard_path(num), {})
        if not isinstance(data, dict):
            continue
        for uid_s in data.keys():
            new_index[str(uid_s)] = num

    _index = new_index

    if save and new_index != previous_index:
        try:
            _write_json(INDEX_FILE, _index)
        except Exception as exc:
            log_db_error('rebuild_user_index', exc)


def _initialize(force=False):
    global _initialized, _index, _meta

    with _lock:
        if _initialized and not force:
            return

        os.makedirs(DB_DIR, exist_ok=True)

        _meta = _read_json(META_FILE, {}) or {}
        if not isinstance(_meta, dict):
            _meta = {}

        if os.path.exists(INDEX_FILE):
            _index = _read_json(INDEX_FILE, {}) or {}
            if not isinstance(_index, dict):
                _index = {}
        else:
            _index = {}

        # The index is derived data. Reconcile it once at startup so a prior
        # interrupted index write cannot make durable user settings disappear.
        if _has_shard_files():
            _rebuild_index(save=True)

        max_shard = 0
        try:
            for f in os.listdir(DB_DIR):
                if f.startswith('users_') and f.endswith('.json'):
                    try:
                        num = int(f[6:-5])
                        if num > max_shard:
                            max_shard = num
                    except Exception:
                        pass
        except Exception:
            pass

        latest = _meta.get('latest_shard')
        if not isinstance(latest, int) or latest < 0:
            latest = max_shard
        if max_shard > latest:
            latest = max_shard

        _meta['latest_shard'] = latest
        _initialized = True


def _load_shard(num):
    data = _read_json(_shard_path(num), {}) or {}
    if isinstance(data, dict):
        for uid_s, settings in data.items():
            _cache[str(uid_s)] = _normalize_settings(settings)
    return data



def _get_internal(uid):
    _initialize()
    uid_s = str(uid)

    if uid_s in _cache:
        return _cache[uid_s]

    shard = _index.get(uid_s)
    if shard is not None:
        try:
            shard = int(shard)
        except Exception:
            shard = None

    if shard is not None:
        _load_shard(shard)
        if uid_s in _cache:
            return _cache[uid_s]

    return _default_settings()


def _choose_new_shard():
    limit = max(1, int(MAX_DB_FILE_SIZE))
    latest = int(_meta.get('latest_shard', 0) or 0)

    path = _shard_path(latest)
    while os.path.exists(path) and os.path.getsize(path) >= limit:
        latest += 1
        path = _shard_path(latest)

    _meta['latest_shard'] = latest

    try:
        _write_json(META_FILE, _meta)
    except Exception:
        pass

    return latest


def _save_user(uid_s, settings):
    """Persist one user without risking data loss during shard rollover.

    The destination shard is written first.  The index is then durably
    updated, and only after both steps succeed is the old shard cleaned up.
    A cleanup failure can therefore leave a harmless duplicate, but never
    removes the last durable copy of the user's settings.
    """
    _initialize()
    uid_s = str(uid_s)

    old_shard = _index.get(uid_s)
    if old_shard is not None:
        try:
            old_shard = int(old_shard)
        except (TypeError, ValueError):
            old_shard = None

    if old_shard is not None:
        old_path = _shard_path(old_shard)
        if os.path.exists(old_path) and os.path.getsize(old_path) <= MAX_DB_FILE_SIZE:
            shard = old_shard
        else:
            shard = _choose_new_shard()
            if shard == old_shard:
                shard += 1
                _meta['latest_shard'] = shard
                try:
                    _write_json(META_FILE, _meta)
                except Exception as exc:
                    log_db_error('persist_shard_metadata', exc, shard=shard)
    else:
        shard = _choose_new_shard()

    # 1) First create/update the durable destination copy.
    path = _shard_path(shard)
    data = _read_json(path, {}) or {}
    if not isinstance(data, dict):
        data = {}
    data[uid_s] = copy.deepcopy(settings)
    _write_json(path, data)

    # 2) Then make the index point at the successfully-written destination.
    previous_index_value = _index.get(uid_s)
    try:
        previous_index_int = int(previous_index_value) if previous_index_value is not None else None
    except (TypeError, ValueError):
        previous_index_int = None

    if previous_index_int != shard:
        _index[uid_s] = shard
        try:
            _write_json(INDEX_FILE, _index)
        except Exception as exc:
            if previous_index_value is None:
                _index.pop(uid_s, None)
            else:
                _index[uid_s] = previous_index_value
            log_db_error('persist_user_shard_index', exc, user_id=uid_s, shard=shard)
            raise

    if shard > int(_meta.get('latest_shard', 0) or 0):
        _meta['latest_shard'] = shard
        try:
            _write_json(META_FILE, _meta)
        except Exception as exc:
            # Metadata only optimizes future shard selection.  The durable
            # user copy + index above are authoritative, so this is non-fatal.
            log_db_error('persist_shard_metadata', exc, shard=shard)

    # 3) Remove the stale copy only after the new copy and index are safe.
    if old_shard is not None and old_shard != shard:
        old_path = _shard_path(old_shard)
        if os.path.exists(old_path):
            old_data = _read_json(old_path, {}) or {}
            if isinstance(old_data, dict) and uid_s in old_data:
                del old_data[uid_s]
                try:
                    _write_json(old_path, old_data)
                except Exception as exc:
                    _logger.warning(
                        'Could not clean stale user copy from shard %s; '
                        'keeping duplicate for safety: %s', old_shard, exc
                    )


def get_user_settings(uid):
    with _lock:
        current = copy.deepcopy(_get_internal(uid))
        # SQLite is authoritative for diamonds once the game migration has run.
        try:
            from services.balance_service import get_balance_if_exists
            wallet_balance = get_balance_if_exists(int(uid))
            if wallet_balance is not None:
                current['diamonds'] = int(wallet_balance)
        except Exception:
            # Keep startup/backward compatibility before SQLite is initialized.
            pass
        return copy.deepcopy(current)


def _validate_wallet_patch(patch):
    if isinstance(patch, dict) and 'diamonds' in patch:
        value = patch['diamonds']
        if type(value) is not int or not 0 <= value <= (1 << 63) - 1:
            raise ValueError('diamond balance must be a nonnegative SQLite integer')


def save_user_settings(uid, settings):
    _validate_wallet_patch(settings)
    with _lock:
        uid_s = str(uid)
        normalized = _normalize_settings(settings)
        try:
            from services.balance_service import set_balance_from_core
            set_balance_from_core(
                int(uid), int(normalized.get('diamonds') or 0),
                username=normalized.get('username'), first_name=normalized.get('first_name'),
                description='Legacy wallet save synchronized to SQLite',
            )
        except Exception:
            raise
        from services.usage_service import configure
        configure(int(uid), enabled=normalized.get('self_enabled', False))
        _save_user(uid_s, normalized)
        _cache[uid_s] = copy.deepcopy(normalized)
        return copy.deepcopy(normalized)


def update_user_settings(uid, patch):
    _validate_wallet_patch(patch)
    with _lock:
        uid_s = str(uid)
        current = copy.deepcopy(_get_internal(uid_s))
        try:
            from services.balance_service import get_balance_if_exists
            wallet_balance = get_balance_if_exists(int(uid))
            if wallet_balance is not None:
                current['diamonds'] = int(wallet_balance)
        except Exception:
            pass
        if isinstance(patch, dict):
            current.update(patch)
        current = _normalize_settings(current)
        if isinstance(patch, dict) and 'diamonds' in patch:
            try:
                from services.balance_service import set_balance_from_core
                current['diamonds'] = set_balance_from_core(
                    int(uid), int(current['diamonds']),
                    username=current.get('username'), first_name=current.get('first_name'),
                    description='Core wallet update',
                )
            except ValueError:
                raise
            except Exception:
                # Never report a JSON-only balance change as a successful wallet write.
                raise
        if isinstance(patch, dict) and 'self_enabled' in patch:
            from services.usage_service import configure
            configure(int(uid), enabled=current['self_enabled'])
        _save_user(uid_s, current)
        _cache[uid_s] = copy.deepcopy(current)
        return copy.deepcopy(current)


def touch_user(uid, username=None, first_name=None):
    with _lock:
        uid_s = str(uid)
        cur = get_user_settings(uid_s)
        patch = {}

        if username is not None and (username or '') != cur.get('username'):
            patch['username'] = username or ''

        if first_name is not None and (first_name or '') != cur.get('first_name'):
            patch['first_name'] = first_name or ''

        if not cur.get('registered_at'):
            patch['registered_at'] = get_tehran_time()

        if patch:
            result = update_user_settings(uid, patch)
            try:
                from services.balance_service import ensure_user
                ensure_user(
                    int(uid), username=result.get('username'), first_name=result.get('first_name'),
                    initial_balance=int(result.get('diamonds') or 0),
                )
            except Exception:
                pass
            return result

        return copy.deepcopy(cur)


def user_exists(uid):
    with _lock:
        _initialize()
        return str(int(uid)) in _index


def count_users():
    with _lock:
        _initialize()
        return len(_index)


def iter_user_ids():
    """Yield user ids lazily without holding the database lock.

    A short snapshot of the index keys is taken while locked. The returned
    generator only iterates over that snapshot, so slow consumers (broadcasts,
    exports, etc.) cannot block database operations.
    """
    with _lock:
        _initialize()
        snapshot = tuple(_index.keys())

    for uid in snapshot:
        yield int(uid)


def iter_user_id_batches(batch_size=500):
    """Yield user id batches for streaming consumers such as broadcasts."""
    batch = []
    size = max(1, int(batch_size))
    for uid in iter_user_ids():
        batch.append(uid)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def get_user_ids_page(page=1, page_size=40):
    """Return a reverse-sorted user page with bounded memory usage.

    Only one page-sized heap is kept. Large page numbers increase scan time,
    but do not increase RAM usage by ``page * page_size`` anymore.
    """
    import heapq

    page = max(1, int(page))
    page_size = max(1, int(page_size))

    current = []
    upper_bound = None

    for _ in range(page):
        heap = []
        for uid in iter_user_ids():
            if upper_bound is not None and uid >= upper_bound:
                continue
            if len(heap) < page_size:
                heapq.heappush(heap, uid)
            elif uid > heap[0]:
                heapq.heapreplace(heap, uid)

        current = sorted(heap, reverse=True)
        if current:
            upper_bound = current[-1]
        else:
            break

    return current, count_users()


def get_all_user_ids():
    with _lock:
        _initialize()
        return [int(x) for x in _index.keys()]


def init_db():
    with _lock:
        _initialize()
        return len(_index)


# ========================================
# 🌍 تنظیمات سراسری
# ========================================
def get_global():
    with _lock:
        g = _read_json(GLOBAL_FILE, None)
        base = json.loads(json.dumps(DEFAULT_GLOBAL))

        if isinstance(g, dict):
            stats = base['stats']
            base.update(g)
            st2 = g.get('stats') or {}
            if isinstance(st2, dict):
                stats.update(st2)
            base['stats'] = stats

        fc = base.get('force_channels')
        if not isinstance(fc, list):
            base['force_channels'] = []

        return base


def set_global(patch):
    with _lock:
        g = get_global()
        if isinstance(patch, dict):
            g.update(patch)
        _write_json(GLOBAL_FILE, g)
        return g


def add_stats(delta):
    with _lock:
        g = get_global()
        st = g.setdefault('stats', {})
        for k, v in (delta or {}).items():
            try:
                st[k] = int(st.get(k, 0)) + int(v)
            except Exception:
                pass
        _write_json(GLOBAL_FILE, g)
        return g


# ========================================
# 🧾 پرداخت‌ها
# ========================================
def _load_payments():
    p = _read_json(PAYMENTS_FILE, {})
    return p if isinstance(p, dict) else {}


def create_payment(uid, diamonds, amount):
    from services.payment_service import create
    return create(uid, diamonds, amount)


def get_payment(pay_id):
    from services.payment_service import get
    return get(pay_id)


def update_payment(pay_id, patch):
    # Approval must go through the atomic, authorized review operation.
    raise ValueError('use payment_service.review for payment decisions')


def list_payments(status=None, limit=None):
    from services.payment_service import list_requests
    return list_requests(status, limit)
