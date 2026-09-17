"""نقطه ورود اصلی - همه فایل‌ها را اجرا می‌کند."""
import asyncio

from config import BASE_DIR, DATA_DIR, user_config_values, release_config_updates, premium_converter_config_updates, resend_mode_config_updates, TRIAL_DURATION_HOURS
from storage import (StorageError, process_lock, initialize_store, prepare_upgrade, finish_upgrade, apply_config_update)



async def _cancel_tasks(tasks):
    """Cancel and drain tasks so shutdown never leaves pending work behind."""
    current = asyncio.current_task()
    pending = [task for task in tasks if task is not current and not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def main():
    try:
        with process_lock(DATA_DIR):
            initialize_store(DATA_DIR, BASE_DIR, user_config_values())
            digest, backup = prepare_upgrade(DATA_DIR, BASE_DIR)
            print(f"📁 مسیر ثابت داده‌ها: {DATA_DIR}")
            if backup:
                print(f"💾 پشتیبان پیش از ارتقا: {backup.name}")
            apply_config_update(DATA_DIR, 'v0.09.13-coingecko-key', release_config_updates())
            # Production-Safe: نصب‌های موجود که PREMIUM_EMOJI_CONVERTER_ENABLED=False
            # انتشار قبلی را دریافت کرده بودند، یک‌باره (با marker جدا) به پیش‌فرض
            # روشن ارتقا می‌یابند؛ انتخاب پنل هر حساب در دیتابیس دست‌نخورده می‌ماند.
            apply_config_update(DATA_DIR, 'v0.09.13-premium-emoji-converter-default-on',
                                premium_converter_config_updates())
            # Premium Resend + Away: کلید PREMIUM_EMOJI_RESEND_MODE وقتی در
            # config ذخیره‌شده نبود اضافه می‌شود؛ تغییر دستی مالک حفظ می‌ماند.
            apply_config_update(DATA_DIR, 'v0.09.13-premium-resend-away',
                                resend_mode_config_updates())
            await _main_runtime(digest)
    except StorageError as exc:
        print(f"❌ اجرای برنامه برای حفظ داده‌ها متوقف شد: {exc}")


async def _main_runtime(storage_digest):
    import importlib
    import config
    importlib.reload(config)
    from bot import run_bot
    from inline import run_inline
    from self_manager import start_all_sessions
    
    import db
    from config import validate_runtime_config
    from database.models import (
        init_tabchi_db,
        init_message_saver_db,
        init_membership_support_db,
        init_game_db,
        init_custom_emojis_db,
        init_crypto_db,
        init_ai_db,
    )
    from services.balance_service import migrate_legacy_wallets
    from services.logging_service import get_logger, setup_logging
    from services.scheduler import run_scheduler
    
    logger = get_logger('main')
    from services.data_backup import run_backup_loop
    setup_logging()
    print("=" * 60)
    print("🚀 سیستم سلف چندکاربره")
    print("=" * 60)

    config_problems = validate_runtime_config()
    if config_problems:
        missing = ', '.join(config_problems)
        logger.error('Startup blocked: missing/invalid configuration: %s', missing)
        print(f"❌ تنظیمات ضروری ناقص است: {missing}")
        print("ℹ️ مقادیر ضروری را در فایل config.py بررسی کنید.")
        return

    # دیتابیس‌ها باید به صورت یک startup gate بالا بیایند. ادامه دادن با DB
    # نیمه‌آماده می‌تواند state کاربران را ناسازگار کند.
    try:
        user_count = db.init_db()
        init_tabchi_db()
        init_message_saver_db()
        init_membership_support_db()
        init_custom_emojis_db()
        init_game_db()
        init_crypto_db()
        init_ai_db()
        migrated_wallets = migrate_legacy_wallets()
        from services.usage_service import reconcile_all
        reconcile_all()  # Catch up persisted dues before any Telegram connection.
        finish_upgrade(DATA_DIR, storage_digest)
    except Exception:
        logger.exception('Database initialization failed; startup aborted')
        print("❌ دیتابیس کامل initialize نشد؛ برای جلوگیری از خرابی اطلاعات اجرا متوقف شد.")
        return

    print(f"📦 دیتابیس فعال شد - {user_count} کاربر")
    print(f"💎 کیف پول اتمیک فعال شد - {migrated_wallets} کاربر مهاجرت شد")

    try:
        session_tasks = await start_all_sessions()
    except Exception:
        logger.exception('Failed while starting saved self sessions')
        print("❌ خطا در راه‌اندازی سشن‌های ذخیره‌شده")
        return

    from services.usage_service import run_billing_loop
    billing_task = asyncio.create_task(run_billing_loop(), name='persistent-billing')
    backup_task = asyncio.create_task(run_backup_loop(), name='data-backup')
    scheduler_task = asyncio.create_task(run_scheduler(), name='tabchi-scheduler')
    bot_task = asyncio.create_task(run_bot(), name='main-bot')
    inline_task = asyncio.create_task(run_inline(), name='inline-bot')
    critical_tasks = [bot_task, inline_task, scheduler_task, billing_task]
    all_tasks = critical_tasks + list(session_tasks) + [backup_task]

    print("\n✅ همه سیستم‌ها فعال شدند:")
    print("   - 🤖 ربات اصلی")
    print("   - 🎛 ربات اینلاین")
    print(f"   - 🎁 Trial {TRIAL_DURATION_HOURS}h / 🎧 Support / 👑 Admin RBAC")
    print(f"   - 👥 {len(session_tasks)} سشن سلف")
    print("\n🎯 شروع حلقه اصلی...\n")
    logger.info('Runtime started: %s self session(s)', len(session_tasks))

    try:
        # اگر یکی از سه سرویس حیاتی بی‌دلیل تمام شود، اجرای نیمه‌جان نداشته باشیم.
        done, _ = await asyncio.wait(critical_tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task.cancelled():
                logger.warning('Critical service cancelled: %s', task.get_name())
                continue
            exc = task.exception()
            if exc is None:
                logger.error('Critical service stopped unexpectedly: %s', task.get_name())
            else:
                logger.error(
                    'Critical service crashed: %s: %s: %s',
                    task.get_name(), type(exc).__name__, exc,
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
    except asyncio.CancelledError:
        raise
    finally:
        import self_manager
        await _cancel_tasks(all_tasks + list(self_manager.running_sessions.values()))
        logger.info('Runtime stopped and outstanding tasks were drained')


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 ربات متوقف شد")
