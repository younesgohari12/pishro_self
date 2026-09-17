"""REAL Telegram live test for the Premium Emoji Converter — 6 scenarios.

این ابزار تست «واقعی» است: پیام با سشن پرمیوم واقعی ارسال می‌کند و entity
را از همان پیامِ برگشتیِ تلگرام راستی‌آزمایی می‌کند. هیچ نتیجه‌ای ساختگی
نمی‌سازد؛ بدون سشن معتبر با خطای واضح خارج می‌شود (exit code 2).

سناریوها: Saved / Private / Group / Channel / Caption / Album

اجرا (روی سروری که سشن پرمیوم دارد):
    export PREMIUM_LIVE_SESSION='<StringSession اکانت پرمیوم>'
    python -m tools.premium_emoji_live_test \
        --private <user_id یا @username> --group <chat_id> --channel <channel_id>

قوانین:
- هر پیام با پیشوند یکتا (timestamp) ارسال می‌شود و بلافاصله حذف می‌شود.
- نتیجه فقط از entities پیام برگشتی تلگرام خوانده می‌شود.
- کانال: فقط اگر اکانت اجازه پست داشته باشد پاس می‌شود؛ در غیر این صورت FAIL
  با دلیل دقیق ثبت می‌شود (شبیه‌سازی ممنوع).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telethon import TelegramClient, functions, types  # noqa: E402
from telethon.sessions import StringSession  # noqa: E402

import config  # noqa: E402
from services.premium_emoji_converter import (  # noqa: E402
    install_premium_emoji_converter,
)

TEST_TEXT = 'سلام 🔥 تست 😂 پرمیوم ❤️'
CAPTION_TEXT = 'کپشن آزمایشی 🚀 اختصاصی 👑'
ALBUM_TEXT = 'آلبوم آزمایشی 💎 یک 🤍'


def _unique(text: str) -> str:
    return f'{text} [{int(time.time())}]'


def _customs(message):
    return [e for e in getattr(message, 'entities', None) or ()
            if isinstance(e, types.MessageEntityCustomEmoji)]


class Result:
    def __init__(self, name):
        self.name = name
        self.status = 'FAIL'
        self.detail = ''

    def ok(self, detail):
        self.status, self.detail = 'PASS', detail

    def fail(self, detail):
        self.status, self.detail = 'FAIL', detail


async def _send_and_verify(client, peer, text):
    """ارسال واقعی + راستی‌آزمایی از پیام برگشتی تلگرام."""
    sent = await client.send_message(peer, text, parse_mode=None)
    customs = _customs(sent)
    raw = sent.raw_text or sent.message or ''
    # متن باید دقیقاً همان ورودی باشد (هیچ ایموجی ثابتی اضافه نشده باشد)
    if raw != text:
        raise AssertionError(f'text changed on wire: {raw!r}')
    if len(customs) < 1:
        raise AssertionError(f'expected >= 1 custom entity, got {len(customs)}')
    return sent, customs


async def run_live(args):
    session_value = os.environ.get('PREMIUM_LIVE_SESSION', '').strip()
    if not session_value:
        print('FAIL: متغیر محیطی PREMIUM_LIVE_SESSION تنظیم نشده است؛ تست لایو اجرا نشد.')
        print('این ابزار هرگز نتیجه ساختگی تولید نمی‌کند.')
        return 2
    client = TelegramClient(StringSession(session_value),
                            config.API_ID, config.API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        print('FAIL: سشن معتبر نیست (unauthorized)؛ تست لایو اجرا نشد.')
        return 2
    me = await client.get_me()
    premium = bool(getattr(me, 'premium', False))
    print(f'حساب: {me.id} premium={premium}')
    engine = install_premium_emoji_converter(client, account=me)
    if not engine.effective_enabled():
        print('FAIL: کانورتر روی این حساب فعال نیست '
              f'(effective_enabled=False, master={config.PREMIUM_EMOJI_ENABLED})')
        return 2

    results = []

    # 1) Saved Messages
    r = Result('Saved')
    try:
        sent, customs = await _send_and_verify(client, 'me', _unique(TEST_TEXT))
        r.ok(f'{len(customs)} entity, ids={[e.document_id for e in customs][:3]}')
        await sent.delete()
    except Exception as exc:
        r.fail(f'{type(exc).__name__}: {exc}')
    results.append(r)

    async def chat_scenario(name, target, builder):
        r = Result(name)
        if not target:
            r.fail('هدف داده نشد (--' + name.lower() + ')؛ اجرا نشد، حدس زده نشد')
            results.append(r)
            return
        try:
            peer = await client.get_input_entity(target)
            await builder(peer, r)
        except Exception as exc:
            r.fail(f'{type(exc).__name__}: {exc}')
        results.append(r)

    # 2) Private
    async def private_case(peer, r):
        sent, customs = await _send_and_verify(client, peer, _unique(TEST_TEXT))
        r.ok(f'{len(customs)} entity')
        await sent.delete()
    await chat_scenario('Private', args.private, private_case)

    # 3) Group
    async def group_case(peer, r):
        sent, customs = await _send_and_verify(client, peer, _unique(TEST_TEXT))
        r.ok(f'{len(customs)} entity')
        await sent.delete()
    await chat_scenario('Group', args.group, group_case)

    # 4) Channel (نیاز به اجازه پست؛ در صورت نداشتن دسترسی FAIL با دلیل ثبت می‌شود)
    async def channel_case(peer, r):
        sent, customs = await _send_and_verify(client, peer, _unique(TEST_TEXT))
        r.ok(f'{len(customs)} entity')
        await sent.delete()
    await chat_scenario('Channel', args.channel, channel_case)

    # 5) Caption (فایل واقعی + کپشن، روی Saved تا وابسته به هدف نباشد)
    r = Result('Caption')
    if args.file and Path(args.file).is_file():
        try:
            sent = await client.send_file('me', args.file,
                                          caption=_unique(CAPTION_TEXT), parse_mode=None)
            customs = _customs(sent)
            raw = sent.raw_text or ''
            if len(customs) < 1:
                raise AssertionError(f'expected >=1 custom entity in caption, got {len(customs)}')
            r.ok(f'{len(customs)} entity در کپشن')
            await sent.delete()
        except Exception as exc:
            r.fail(f'{type(exc).__name__}: {exc}')
    elif not args.file:
        r.fail('فایل با --file داده نشد؛ کپشن واقعی اجرا نشد')
    results.append(r)

    # 6) Album (دو فایل واقعی + کپشن، روی Saved)
    r = Result('Album')
    if args.file and args.file2 and Path(args.file).is_file() and Path(args.file2).is_file():
        try:
            sent_list = await client.send_file('me', [args.file, args.file2],
                                               caption=[_unique(ALBUM_TEXT), ''],
                                               parse_mode=None)
            sent = sent_list[0] if isinstance(sent_list, list) else sent_list
            customs = _customs(sent)
            if len(customs) < 1:
                raise AssertionError(f'expected >=1 custom entity in album caption, got {len(customs)}')
            r.ok(f'{len(customs)} entity در کپشن آلبوم')
            if isinstance(sent_list, list):
                await client.delete_messages('me', [m.id for m in sent_list])
            else:
                await sent.delete()
        except Exception as exc:
            r.fail(f'{type(exc).__name__}: {exc}')
    else:
        r.fail('دو فایل با --file و --file2 داده نشد؛ آلبوم واقعی اجرا نشد')
    results.append(r)

    await client.disconnect()

    print('\n' + '=' * 62)
    print('نتیجه تست لایو واقعی (بدون شبیه‌سازی):')
    passed = 0
    for r in results:
        print(f'  {r.status:4} | {r.name:8} | {r.detail}')
        passed += (r.status == 'PASS')
    print('=' * 62)
    print(f'{passed}/{len(results)} PASS')
    return 0 if passed == len(results) else 1


def main():
    parser = argparse.ArgumentParser(description='Premium Emoji live test (real sends)')
    parser.add_argument('--private', default=None, help='user id یا @username')
    parser.add_argument('--group', default=None, help='group chat_id یا @username')
    parser.add_argument('--channel', default=None, help='channel id یا @username')
    parser.add_argument('--file', default=None, help='فایل واقعی برای تست کپشن')
    parser.add_argument('--file2', default=None, help='فایل دوم برای تست آلبوم')
    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(run_live(args)))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == '__main__':
    main()
