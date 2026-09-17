"""CLI گزارش نهایی Premium Emoji Converter به ادمین.

Usage (فقط با ENV؛ هیچ توکنی داخل کد نیست):

    PREMIUM_REPORT_BOT_TOKEN=123:abc python -m tools.send_premium_report \
        --report build_report.json [--zip dist/PishroSelf_v0.09.13_PREMIUM_EMOJI_FINAL.zip]

ساختار report JSON (همه فیلدها اختیاری):
    {"build": "OK", "changed_files": 9, "tests": "173 passed",
     "errors": "-", "zip_path": "dist/...zip", "notes": "..."}
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.premium_report import (  # noqa: E402
    ADMIN_REPORT_ID,
    send_report_document,
    send_report_text,
)


def build_report_text(data: dict) -> str:
    lines = [
        '🎨 گزارش نهایی Premium Emoji Converter — PishroSelf v0.09.13',
        '',
        f"🔨 وضعیت Build: {data.get('build', 'نامشخص')}",
        f"📝 فایل‌های تغییرکرده: {data.get('changed_files', 'نامشخص')}",
        f"🧪 تست‌ها: {data.get('tests', 'نامشخص')}",
        f"⚠️ خطاها: {data.get('errors', 'بدون خطا')}",
        f"📦 مسیر ZIP: {data.get('zip_path', 'نامشخص')}",
    ]
    if data.get('notes'):
        lines += ['', f'🗒 یادداشت: {data["notes"]}']
    return '\n'.join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser(description='ارسال گزارش Premium Emoji Converter')
    parser.add_argument('--report', required=True, help='فایل JSON گزارش')
    parser.add_argument('--zip', dest='zip_path', default=None, help='فایل ZIP نهایی')
    args = parser.parse_args()

    data = json.loads(Path(args.report).read_text(encoding='utf-8'))
    text = build_report_text(data)
    ok_text = await send_report_text(text)
    ok_doc = True
    if args.zip_path:
        ok_doc = await send_report_document(
            args.zip_path, caption='📦 PishroSelf_v0.09.13_PREMIUM_EMOJI_FINAL')
    print(f'report_text={"sent" if ok_text else "SKIPPED/FAILED"} '
          f'document={"sent" if ok_doc else "SKIPPED/FAILED"} '
          f'target={ADMIN_REPORT_ID}')
    return 0 if ok_text else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
