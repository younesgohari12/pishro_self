"""Smoke Test سه‌چته — مقایسه Runtime کانورتر در Saved / Private / Group.

هدف: اجرای دقیقاً همان سه تست spec مالک و کشف ROOT CAUSE اگر entity ساخته
نشد. سه پیام «سلام 😂🔥❤️» به سه نوع چت ارسال می‌شود و برای هر چت گزارش
می‌شود که آیا MessageEntityCustomEmoji واقعی ساخته شده یا نه؛ در صورت عدم
تبدیل، علت دقیق همان لحظه (کدام شرط رد شده) هم به لاگ محلی و هم به ربات
گزارش تلگرامی (ENV: PREMIUM_LOG_BOT_TOKEN) ارسال می‌شود.

اجرا (روی سرور مالک، جایی که سشن معتبر وجود دارد):

    python -m tools.premium_emoji_debug_smoke --session '<STRING_SESSION>' \
        --private <user_id> --group <group_id>

    # بدون --private/--group فقط Saved Messages تست می‌شود.
    # توکن ربات گزارش فقط از ENV: PREMIUM_LOG_BOT_TOKEN
    # کانورتر باید برای این حساب روشن باشد (دکمه 🎨 Premium Emoji پنل).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from services import telegram_logger as tlog  # noqa: E402
from services.custom_emoji_service import (  # noqa: E402
    extract_custom_emojis,
)
from services.premium_emoji_converter import (  # noqa: E402
    PremiumEmojiConverter,
    install_premium_emoji_converter,
    install_premium_emoji_outgoing_injector,
)

TEST_TEXT = 'سلام 😂🔥❤️'
COOLDOWN_NOTE = ('converter cooled down after a Telegram rejection '
                 f'({config.PREMIUM_EMOJI_CONVERTER_ENABLED=} flag ignored)')


def _engine_state(engine):
    if time.monotonic() < engine.disabled_until:
        return 'COOLDOWN'
    choice = engine.is_enabled() if engine.is_enabled else None
    if choice is not None:
        return 'ON' if choice else 'OFF(panel)'
    return 'ON' if config.PREMIUM_EMOJI_CONVERTER_ENABLED else 'OFF(config)'


async def send_and_verify(client, engine, peer, label):
    """ارسال پیام نمونه و راستی‌آزمایی entity های پیام ارسال‌شده."""
    result = {'label': label, 'chat': peer, 'sent': False, 'entities': 0,
              'doc_ids': [], 'state': _engine_state(engine), 'cause': None}
    try:
        sent = await client.send_message(peer, TEST_TEXT, parse_mode=None)
        result['sent'] = True
        result['message_id'] = getattr(sent, 'id', None)
        items = extract_custom_emojis(sent)
        result['entities'] = len(items)
        result['doc_ids'] = [item['document_id'] for item in items]
        if not items:
            # ROOT CAUSE همان لحظه: چه چیزی جلوی تبدیل را گرفته؟
            if time.monotonic() < engine.disabled_until:
                result['cause'] = COOLDOWN_NOTE
            elif not engine.effective_enabled():
                result['cause'] = ('converter disabled — دکمه 🎨 Premium Emoji '
                                   'پنل سلف خاموش است یا تنظیم حساب None است')
            elif engine.convert(TEST_TEXT, [])[1] == []:
                result['cause'] = 'mapping has no active ids for these emojis'
            else:
                result['cause'] = ('entities dropped after conversion — '
                                   'edit/update interference; see [PREMIUM DEBUG] log')
    except Exception as exc:  # noqa: BLE001
        result['cause'] = f'send failed: {type(exc).__name__}'
        tlog.send_error('❌ Premium Emoji Error',
                        {'File': 'tools/premium_emoji_debug_smoke.py',
                         'Error': f'{type(exc).__name__}: {exc}'})
    return result


def print_report(results):
    print('\n================ PREMIUM EMOJI DEBUG SMOKE ================')
    print(f'Text: {TEST_TEXT!r}')
    header = f"{'Chat':<12}{'State':<12}{'Sent':<6}{'Entities':<9}{'Cause'}"
    print(header)
    print('-' * len(header))
    for row in results:
        print(f"{row['label']:<12}{row['state']:<12}"
              f"{'✅' if row['sent'] else '❌':<6}{row['entities']:<9}"
              f"{row['cause'] or ('OK: MessageEntityCustomEmoji created ' + str(row['doc_ids']))}")
    print('===========================================================')
    ok = all(row['sent'] and row['entities'] == 3 for row in results)
    print('VERDICT:', 'ALL CHATS CONVERTED ✅' if ok else
          'ROOT CAUSE FOUND — see Cause column + telegram log ❌')
    return ok


async def run_with_session(session_string, private_id, group_id):
    from telethon import TelegramClient, events
    from telethon.sessions import StringSession

    client = TelegramClient(StringSession(session_string),
                            int(os.environ.get('PREMIUM_RESOLVE_API_ID',
                                               str(getattr(config, 'API_ID', 0) or 0))),
                            os.environ.get('PREMIUM_RESOLVE_API_HASH',
                                           str(getattr(config, 'API_HASH', '') or '')))
    await client.connect()
    if not await client.is_user_authorized():
        print('SESSION_INVALID — سشن معتبر نیست')
        return 1
    me = await client.get_me()

    # همان نصب runtime سلف: prefix نمی‌خواهیم (مستقل است)، کانورتر + injector
    engine = PremiumEmojiConverter(premium=getattr(me, 'premium', False),
                                   is_enabled=lambda: True)
    engine.owner_id = me.id
    install_premium_emoji_converter(client, account=me, is_enabled=lambda: True)
    install_premium_emoji_outgoing_injector(client, engine)

    # ثبت پیام‌های خروجی برای مقایسه (debug زنده در تلگرام)
    seen = []

    @client.on(events.NewMessage(outgoing=True))
    async def _capture(event):
        items = extract_custom_emojis(event.message)
        seen.append({'chat': event.chat_id, 'text': event.raw_text,
                     'entities': len(items)})

    results = [await send_and_verify(client, engine, 'me', 'Saved')]
    await asyncio.sleep(2.0)
    if private_id:
        results.append(await send_and_verify(client, engine, int(private_id), 'Private'))
        await asyncio.sleep(2.0)
    if group_id:
        results.append(await send_and_verify(client, engine, int(group_id), 'Group'))
        await asyncio.sleep(2.0)

    ok = print_report(results)
    tlog.send_premium_event(
        '🧪 Premium Emoji Debug Smoke — 3-Chat Comparison',
        {'Account': me.id, 'Text': TEST_TEXT,
         'Results': ' | '.join(f"{r['label']}: entities={r['entities']} "
                               f"state={r['state']} cause={r['cause'] or 'OK'}"
                               for r in results),
         'Verdict': 'ALL CONVERTED' if ok else 'ROOT CAUSE FOUND'},
        level='INFO', kind='converted')
    await client.disconnect()
    return 0 if ok else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', default='',
                        help='StringSession پرمیوم مالک (یا ENV: PREMIUM_SMOKE_SESSION)')
    parser.add_argument('--private', default='', help='user_id چت خصوصی')
    parser.add_argument('--group', default='', help='chat_id گروه')
    args = parser.parse_args()

    # گارد آفلاین: موتور بدون نگاشتِ فعال چیزی نمی‌فرستد
    engine = PremiumEmojiConverter()
    _, entities = engine.convert(TEST_TEXT, [])
    print(f'offline mapping check: {len(entities)} entities for {TEST_TEXT!r}')

    session = args.session or os.environ.get('PREMIUM_SMOKE_SESSION', '')
    if not session:
        print('NO_SESSION — فقط بررسی آفلاین انجام شد؛ برای تست سه‌چته واقعی '
              '--session بدهید (روی سرور مالک).')
        return 0
    return asyncio.run(run_with_session(session, args.private, args.group))


if __name__ == '__main__':
    raise SystemExit(main())
