#!/usr/bin/env python3
"""تولید فایل مرکزی emoji_map.json از نگاشت Strict داخلی پروژه.

نگاشت داخلی (premium_emoji_mapping.PREMIUM_EMOJI_MAP) فقط شناسه‌های alt
تأییدشده دارد؛ این ابزار همان داده را به فرمت سادهٔ قراردادی مالک
({ایموجی: [شناسه, ...]}) در ریشهٔ پروژه می‌نویسد تا در صورت نیاز بدون
تغییر کد قابل ویرایش/به‌روزرسانی باشد.

استفاده:
    python tools/export_emoji_map.py [--check]

    --check  فقط مقایسه می‌کند (بدون نوشتن)؛ برای CI/تست.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from premium_emoji_mapping import EMOJI_SOURCE, PREMIUM_EMOJI_MAP  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT = os.path.join(ROOT, 'emoji_map.json')


def build() -> dict:
    """فرمت قراردادی: کلید=ایموجی یونیکد، مقدار=لیست شناسه (رشته‌ای)."""
    return {emoji: [str(value) for value in ids]
            for emoji, ids in PREMIUM_EMOJI_MAP.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true',
                        help='فقط بررسی تطابق؛ فایل بازنویسی نمی‌شود')
    args = parser.parse_args()
    data = build()
    if args.check:
        try:
            with open(OUTPUT, 'r', encoding='utf-8') as handle:
                current = json.load(handle)
        except (OSError, ValueError):
            print(f'❌ {OUTPUT} خوانده نشد؛ با python tools/export_emoji_map.py بسازید')
            return 1
        if current == data:
            print(f'✅ emoji_map.json همگام با نگاشت داخلی است ({len(data)} کلید)')
            return 0
        print('❌ emoji_map.json با نگاشت داخلی تفاوت دارد')
        return 1
    payload = {
        '_comment': 'PishroSelf central emoji map (emoji -> Telegram custom emoji document ids). '
                    'Regenerate with tools/export_emoji_map.py. Missing emoji = left untouched.',
        '_source': EMOJI_SOURCE,
        'map': data,
    }
    with open(OUTPUT, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1, sort_keys=True)
        handle.write('\n')
    print(f'✅ نوشته شد: {OUTPUT} ({len(data)} ایموجی)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
