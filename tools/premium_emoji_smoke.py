"""Smoke Test واقعی روی Self Account — فقط روی حساب پرمیوم مالک اجرا شود.

این اسکریپت بخشی از تست‌های خودکار نیست؛ نیاز به سشن واقعی تلگرام دارد:

    python -m tools.premium_emoji_smoke --session <SESSION_STRING> --chat 8359698350

جریان: سه پیام نمونه با اکانت کاربری ارسال می‌شود، سپس entityهای پیام ارسال‌شده
بررسی می‌شود که MessageEntityCustomEmoji واقعی داشته باشند؛ متن حفظ شده باشد؛
Bot هیچ نقشی در ارسال نداشته باشد.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telethon import TelegramClient, types  # noqa: E402
from telethon.sessions import StringSession  # noqa: E402

import config  # noqa: E402
from services.premium_emoji_converter import (  # noqa: E402
    install_premium_emoji_converter,
)

SMOKE_MESSAGES = ['سلام 😂🔥❤️', 'عالی شد 👑💎', 'دمت گرم 🚀✨']
SMOKE_PEER = 8359698350  # حساب مالک برای بررسی خروجی


def custom_entities(message):
    return [e for e in getattr(message, 'entities', None) or ()
            if isinstance(e, types.MessageEntityCustomEmoji)]


async def run(session_string: str, chat_id: int, keep: bool) -> int:
    client = TelegramClient(StringSession(session_string), config.API_ID, config.API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        print('❌ سشن معتبر نیست')
        return 2
    me = await client.get_me()
    engine = install_premium_emoji_converter(client, account=me, is_enabled=lambda: True)
    if engine is None:
        print('❌ این حساب ربات است؛ Smoke Test فقط روی اکانت کاربری معنا دارد.')
        return 2
    sent_ids = []
    failures = []
    for text in SMOKE_MESSAGES:
        sent = await client.send_message(chat_id, text, parse_mode=None)
        sent_ids.append(sent.id)
        customs = custom_entities(sent)
        if not customs:
            failures.append(f'پیام «{text}» بدون Custom Emoji ارسال شد.')
            continue
        if sent.message != text:
            failures.append(f'متن پیام تغییر کرد: {sent.message!r}')
        ids = {e.document_id for e in customs}
        print(f'✅ «{text}» → {len(customs)} Custom Emoji | document_ids={sorted(ids)}')
    if not keep:
        try:
            await client.delete_messages(chat_id, sent_ids)
            print('🧹 پیام‌های تست حذف شدند.')
        except Exception as exc:
            print(f'⚠️ حذف پیام‌های تست ناموفق بود: {type(exc).__name__}')
    if failures:
        print('— خطاها —')
        for item in failures:
            print('•', item)
        return 1
    print('🎉 Smoke Test کامل شد: همه پیام‌ها Custom Emoji واقعی داشتند.')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='Smoke Test Premium Emoji Converter')
    parser.add_argument('--session', required=True, help='SESSION_STRING اکانت سلف')
    parser.add_argument('--chat', type=int, default=SMOKE_PEER)
    parser.add_argument('--keep', action='store_true', help='پیام‌های تست حذف نشوند')
    args = parser.parse_args()
    return asyncio.run(run(args.session, args.chat, args.keep))


if __name__ == '__main__':
    raise SystemExit(main())
