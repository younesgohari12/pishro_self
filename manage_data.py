"""Offline data maintenance. Stop the bot before running write commands."""
import argparse
from pathlib import Path

from storage import (StorageError, default_data_dir, process_lock, initialize_store,
                     create_backup, restore_backup, validate_data, backup_dir)


def main():
    parser = argparse.ArgumentParser(description='PishroSelf persistent data maintenance')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    sub.add_parser('init', help='Explicitly initialize a brand-new empty installation')
    migrate = sub.add_parser('migrate', help='Copy an old installation without deleting it')
    migrate.add_argument('--from', dest='source', required=True)
    sub.add_parser('backup')
    restore = sub.add_parser('restore', help='Restore backup; keep current data in a separate folder')
    restore.add_argument('--backup', required=True)
    args = parser.parse_args()
    root = default_data_dir()
    try:
        if args.command == 'status':
            print('Data:', root)
            print('Python config:', root / 'config.py')
            print('Backups:', backup_dir(root))
            meta = validate_data(root, require_meta=True)
            print('Data format:', meta['format_version'])
            print('Last successful version:', meta.get('last_app_version'))
            return
        with process_lock(root):
            if args.command in {'init', 'migrate'}:
                import config
                source = Path(args.source).resolve() if args.command == 'migrate' else Path(__file__).parent
                result = initialize_store(root, source, config.user_config_values(), allow_empty=args.command == 'init')
                print('Data already exists; nothing overwritten.' if result is None else f'Data prepared: {result}')
            elif args.command == 'backup':
                validate_data(root, require_meta=True)
                print('Backup:', create_backup(root))
            elif args.command == 'restore':
                # Does not import config: restoration must also work if config is damaged.
                kept = restore_backup(root, args.backup)
                print('Restored:', root)
                if kept:
                    print('Previous data preserved:', kept)
    except (StorageError, OSError, ValueError) as exc:
        print(f'Data operation stopped: {exc}')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
