"""Periodic snapshots share the application's write locks."""
import asyncio
import time

from config import DATA_DIR, BACKUP_INTERVAL_HOURS
from storage import create_backup, backup_dir
from services.logging_service import get_logger


def backup_live_data():
    import db
    from database import models
    from tts import voices
    with db._lock, models._lock, voices._settings_lock:
        return create_backup(DATA_DIR, 'automatic')


async def run_backup_loop():
    logger = get_logger('backup')
    interval = max(1, BACKUP_INTERVAL_HOURS) * 3600
    while True:
        await asyncio.sleep(60)
        try:
            latest = max((p.stat().st_mtime for p in backup_dir(DATA_DIR).glob('*.zip')), default=0)
            if time.time() - latest >= interval:
                task = asyncio.create_task(asyncio.to_thread(backup_live_data))
                try:
                    path = await asyncio.shield(task)
                except asyncio.CancelledError:
                    # Keep the process lock held until the backup thread finishes.
                    await asyncio.gather(task, return_exceptions=True)
                    raise
                logger.info('Automatic data backup completed: %s', path.name)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error('Automatic backup failed; existing data unchanged: %s', type(exc).__name__)
