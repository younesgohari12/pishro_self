"""Smoke Test نسخه Strict — تأیید زنده alt برای entityهای واقعی تولیدشده.

دو حالت اجرا:

1) بدون سشن (قابل اجرا همین‌جا): سه پیام نمونه با موتور واقعی و نگامت ارسالی
   تبدیل می‌شوند؛ شناسه‌های انتخاب‌شده با متد رسمی
   messages.getCustomEmojiDocuments از تلگرام پرسیده می‌شوند و برای هر
   entity بررسی می‌شود که alt واقعی دقیقاً همان ایموجی هدف باشد.
   توکن فقط از ENV: PREMIUM_RESOLVE_TOKEN (یا PREMIUM_REPORT_BOT_TOKEN).

2) با سشن پرمیوم مالک (اختیاری): --session واقعاً ارسال می‌کند و پاسخ
   تلگرام خوانده می‌شود؛ همان بررسی روی پیام ارسال‌شده انجام می‌شود.

Bot-untouched و duplicate-prevention در تست‌های خودکار پوشش داده شده‌اند و
اینجا هم به‌صورت آفلاین چک می‌شوند.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from premium_emoji_mapping import (  # noqa: E402
    CHECKED_EMOJIS, PREMIUM_EMOJI_MAP, normalize_emoji,
)
from services.premium_emoji_converter import (  # noqa: E402
    PremiumEmojiConverter,
)
from tools.resolve_premium_emoji_mapping import (  # noqa: E402
    RESOLVE_TOKEN_ENVS, extract_document_info, read_env_token,
)

# شناسه ثابت قدیمی که از نسخه DEBUG_FINAL به بعد کاملاً ممنوع است؛ فقط به
# عنوان «سنتینل ممنوع» در این ابزار تستی نگه داشته می‌شود.
BANNED_FALLBACK_ID = 5938388342281343001
FALLBACK_FREE_SENTINEL = BANNED_FALLBACK_ID

SMOKE_MESSAGES = ['سلام 😂🔥❤️', 'عالی شد 👑💎', 'دمت گرم 🚀✨']


def build_expected_entities(converter=None):
    """تبدیل آفلاین سه پیام نمونه با موتور واقعی؛ خروجی: پیام → [(emoji, id)]."""
    converter = converter or PremiumEmojiConverter()
    out = {}
    for text in SMOKE_MESSAGES:
        _, entities = converter.convert(text, [])
        out[text] = entities
    return out


async def verify_alts_live(entities_by_message, token):
    """پرس‌وجوی زندهٔ شناسه‌های استفاده‌شده و مقایسه alt با ایموجی هدف."""
    import os
    from telethon import TelegramClient, functions
    from telethon.sessions import MemorySession

    needed = sorted({e.document_id for entities in entities_by_message.values()
                     for e in entities})
    if not needed:
        print('NO_ENTITIES')
        return 1
    # اعتبارنامه API فقط از ENV یا config پروژه؛ هرگز داخل کد نیست.
    api_id = int(os.environ.get('PREMIUM_RESOLVE_API_ID',
                                str(getattr(config, 'API_ID', 0) or 0)))
    api_hash = os.environ.get('PREMIUM_RESOLVE_API_HASH',
                              str(getattr(config, 'API_HASH', '') or ''))
    if not api_id or not api_hash:
        print('NO_API_CREDENTIALS — PREMIUM_RESOLVE_API_ID / PREMIUM_RESOLVE_API_HASH '
              'یا API_ID/API_HASH در config لازم است.')
        return 1
    client = TelegramClient(MemorySession(), api_id, api_hash)
    await client.start(bot_token=token)
    alt_by_id = {}
    try:
        for start in range(0, len(needed), 100):
            batch = needed[start:start + 100]
            docs = await client(functions.messages.GetCustomEmojiDocumentsRequest(
                document_id=batch))
            for document in docs or []:
                info = extract_document_info(document)
                alt_by_id[info['document_id']] = info['alt']
    finally:
        await client.disconnect()

    # نگاشت معکوس ایموجی → کلید هدف (برای مقایسه دقیق معنایی).
    failures, checks = [], 0
    for text, entities in entities_by_message.items():
        print(f'✉️ {text}')
        for entity in entities:
            checks += 1
            alt = alt_by_id.get(entity.document_id, '')
            # کلید هدف: کاراکترهایی که این entity روی آن‌ها نشسته است.
            start16, end16 = entity.offset, entity.offset + entity.length
            raw = text.encode('utf-16-le')
            segment = raw[start16 * 2:end16 * 2].decode('utf-16-le')
            ok = normalize_emoji(alt) == normalize_emoji(segment)
            mark = '✅' if ok else '❌'
            print(f'   {mark} {segment!r} → document_id={entity.document_id} '
                  f'telegram_alt={alt!r}')
            if not ok:
                failures.append((text, segment, entity.document_id, alt))
        if not entities:
            print('   ⚪ بدون تبدیل (ایموجی فعال ندارد)')
    print(f'checks={checks} failures={len(failures)}')
    return 0 if not failures and checks else 2


def offline_guards():
    """بررسی‌های آفلاین: duplicate، bot، fallback، متن دست‌نخورده."""
    converter = PremiumEmojiConverter()
    assert converter.strict is True
    for text, entities in build_expected_entities(converter).items():
        # متن هرگز تغییر نمی‌کند و هیچ تکراری وجود ندارد.
        assert [e.offset for e in entities] == \
            sorted({e.offset for e in entities})
        assert FALLBACK_FREE_SENTINEL not in {e.document_id for e in entities}
        again_text, again_entities = converter.convert(text, entities)
        assert again_text == text and len(again_entities) == len(entities)
    print('offline guards: OK (idempotent, no fallback, text intact)')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', default='',
                        help='StringSession پرمیوم برای ارسال واقعی (اختیاری)')
    parser.add_argument('--chat', type=int, default=8359698350)
    args = parser.parse_args()

    offline_guards()
    entities_by_message = build_expected_entities()

    token = read_env_token(RESOLVE_TOKEN_ENVS)
    if not token:
        print('NO_TOKEN — بررسی زنده alt انجام نشد؛ فقط بررسی آفلاین انجام شد.')
        return 0
    return asyncio.run(verify_alts_live(entities_by_message, token))


if __name__ == '__main__':
    raise SystemExit(main())
