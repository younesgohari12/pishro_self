"""کپی محتوای کانال‌ها/گروه‌های قفل (Forward Restricted).

این سرویس با دستور `.کپی محتوا <لینک>` فعال می‌شود. لینک پیام از تلگرام
پارس شده، پیام با get_messages گرفته می‌شود (که محدودیت فوروارد را
دور می‌زند) و سپس محتوای آن به مقصدی که کاربر انتخاب می‌کند ارسال
می‌شود. مقصد می‌تواند Saved Messages، کاربر، گروه یا کانال باشد.
"""
from __future__ import annotations

from services.access_service import can_run

import os
import re
import threading
import time
from typing import Any

from telethon import events, types, utils
from telethon.tl.types import (
    InputMediaGeoPoint,
    InputGeoPoint,
    InputMediaContact,
)

# ============================================================
# State برای capture مقصد (جدا از save_message تا تداخل نکند)
# ============================================================
_PENDING_LOCK = threading.RLock()
_PENDING_DESTINATION: dict[int, dict[str, Any]] = {}
_PENDING_TTL_SECONDS = 300


def begin_destination_capture(owner_id: int, *, payload: dict[str, Any]) -> None:
    """شروع capture برای کپی محتوا. payload شامل link_info و metadata است."""
    with _PENDING_LOCK:
        _PENDING_DESTINATION[int(owner_id)] = {
            'expires_at': time.time() + _PENDING_TTL_SECONDS,
            'payload': dict(payload or {}),
        }


def cancel_destination_capture(owner_id: int) -> None:
    with _PENDING_LOCK:
        _PENDING_DESTINATION.pop(int(owner_id), None)


def _get_state(owner_id: int) -> dict[str, Any] | None:
    uid = int(owner_id)
    now = time.time()
    with _PENDING_LOCK:
        state = _PENDING_DESTINATION.get(uid)
        if not state:
            return None
        if state['expires_at'] < now:
            _PENDING_DESTINATION.pop(uid, None)
            return None
        return state


def has_destination_capture(owner_id: int) -> bool:
    return _get_state(owner_id) is not None


def consume_destination_capture(owner_id: int) -> dict[str, Any] | None:
    state = _get_state(owner_id)
    if state is None:
        return None
    with _PENDING_LOCK:
        _PENDING_DESTINATION.pop(int(owner_id), None)
    return state


# ============================================================
# پارس لینک t.me
# ============================================================
_LINK_RE = re.compile(
    r'(?:https?://)?(?:t\.me|telegram\.me|telegram\.dog)'
    r'/(?:c/(\d+)|([A-Za-z0-9_]+))'
    r'/(\d+)',
    re.IGNORECASE,
)


def parse_message_link(raw: str) -> dict[str, Any] | None:
    """پارس لینک پیام تلگرام.

    خروجی:
      {'kind': 'username', 'username': 'durov', 'message_id': 123}
      {'kind': 'private',  'channel_raw': 4421725618, 'message_id': 3}
    """
    text = (raw or '').strip()
    if not text:
        return None
    m = _LINK_RE.search(text)
    if not m:
        return None
    c_group, username_group, msg_id_group = m.group(1), m.group(2), m.group(3)
    try:
        message_id = int(msg_id_group)
    except Exception:
        return None
    if c_group:
        try:
            return {
                'kind': 'private',
                'channel_raw': int(c_group),
                'message_id': message_id,
            }
        except Exception:
            return None
    if username_group:
        return {
            'kind': 'username',
            'username': username_group,
            'message_id': message_id,
        }
    return None


async def resolve_peer(client, link_info: dict[str, Any]):
    """تبدیل اطلاعات پارس‌شده به entity قابل استفاده برای get_messages."""
    kind = link_info.get('kind')
    if kind == 'username':
        username = link_info.get('username')
        if not username:
            raise ValueError('نام کاربری در لینک موجود نیست.')
        try:
            return await client.get_entity(username)
        except Exception as exc:
            raise ValueError(f'چت/کانال «{username}» پیدا نشد.') from exc
    if kind == 'private':
        raw_id = int(link_info['channel_raw'])
        # t.me/c/XXXXX در واقع ID بدون پیشوند -100 است.
        candidates = (raw_id, int(f'-100{raw_id}'))
        # ابتدا در گفتگوهای خود کاربر جستجو می‌کنیم تا entity کش شود.
        try:
            async for dialog in client.iter_dialogs(limit=500):
                entity = dialog.entity
                if entity is None:
                    continue
                try:
                    peer_id = int(utils.get_peer_id(entity))
                except Exception:
                    continue
                entity_id = int(getattr(entity, 'id', 0) or 0)
                if peer_id in candidates or entity_id in candidates:
                    return entity
        except Exception:
            pass
        # اگر نبود، با -100 امتحان می‌کنیم.
        for cand in candidates:
            try:
                return await client.get_entity(cand)
            except Exception:
                continue
        raise ValueError(
            'این چت در گفتگوهای همین حساب پیدا نشد. اول باید عضو کانال/گروه باشی.'
        )
    raise ValueError('لینک نامعتبر است.')


# ============================================================
# تشخیص نوع و ارسال محتوا
# ============================================================
def _message_type(message) -> str:
    if getattr(message, 'photo', None):
        return 'photo'
    if getattr(message, 'gif', None):
        return 'gif'
    if getattr(message, 'sticker', None):
        return 'sticker'
    if getattr(message, 'voice', None):
        return 'voice'
    if getattr(message, 'audio', None):
        return 'audio'
    if getattr(message, 'video', None):
        return 'video'
    if getattr(message, 'poll', None):
        return 'poll'
    if getattr(message, 'contact', None):
        return 'contact'
    if getattr(message, 'geo', None) or getattr(message, 'venue', None):
        return 'location'
    if getattr(message, 'document', None):
        return 'file'
    if getattr(message, 'media', None):
        return 'other'
    return 'text'


async def _send_location(client, destination, message) -> bool:
    geo = getattr(message, 'geo', None)
    venue = getattr(message, 'venue', None)
    if venue is not None:
        geo = getattr(venue, 'geo', None) or geo
    if geo is None:
        return False
    media = InputMediaGeoPoint(
        geo_point=InputGeoPoint(
            lat=float(geo.lat), long=float(geo.long), accuracy_radius=None
        )
    )
    await client.send_message(
        destination,
        message=getattr(message, 'raw_text', '') or '',
        file=media,
    )
    return True


async def _send_contact(client, destination, message) -> bool:
    contact = getattr(message, 'contact', None)
    if contact is None:
        return False
    media = InputMediaContact(
        phone_number=str(getattr(contact, 'phone_number', '') or ''),
        first_name=str(getattr(contact, 'first_name', '') or ''),
        last_name=str(getattr(contact, 'last_name', '') or ''),
        vcard=str(getattr(contact, 'vcard', '') or ''),
    )
    await client.send_message(
        destination,
        message=getattr(message, 'raw_text', '') or '',
        file=media,
    )
    return True


async def copy_message_to(client, destination, message) -> str:
    """کپی محتوای پیام به مقصد و برگرداندن نوع محتوا."""
    kind = _message_type(message)

    if kind == 'text':
        text = getattr(message, 'raw_text', '') or getattr(message, 'message', '') or '[پیام متنی خالی]'
        await client.send_message(destination, text, parse_mode=None)
        return 'text'

    if kind == 'location':
        ok = await _send_location(client, destination, message)
        if ok:
            return 'location'
        await client.send_message(destination, '[لوکیشن قابل کپی نبود]', parse_mode=None)
        return 'location'

    if kind == 'contact':
        ok = await _send_contact(client, destination, message)
        if ok:
            return 'contact'
        await client.send_message(destination, '[مخاطب قابل کپی نبود]', parse_mode=None)
        return 'contact'

    if kind == 'poll':
        poll = getattr(message, 'poll', None)
        if poll is not None:
            try:
                question = getattr(poll, 'question', '') or ''
                answers = [getattr(a, 'text', '') for a in getattr(poll, 'answers', []) or []]
                if question and answers:
                    await client.send_poll(
                        destination,
                        question=question,
                        options=answers,
                        type=getattr(poll, 'type', None),
                        is_anonymous=bool(getattr(poll, 'public_voters', False) is False),
                    )
                    return 'poll'
            except Exception:
                pass
        await client.send_message(destination, '[نظرسنجی قابل کپی نبود]', parse_mode=None)
        return 'poll'

    # همه انواع دارای مدیا (photo/video/voice/audio/gif/sticker/file)
    caption = getattr(message, 'raw_text', '') or getattr(message, 'message', '') or ''

    kwargs: dict[str, Any] = {'parse_mode': None}
    if kind == 'voice':
        kwargs['voice_note'] = True
    elif kind in {'video', 'gif'}:
        kwargs['supports_streaming'] = True

    try:
        if kind == 'sticker':
            await client.send_file(destination, message.media, **kwargs)
        else:
            await client.send_file(
                destination,
                message.media,
                caption=caption or None,
                **kwargs,
            )
        return kind
    except Exception as exc:
        try:
            path = await message.download_media(file=os.path.abspath('.') + os.sep)
            if path and os.path.exists(path):
                try:
                    if kind == 'sticker':
                        await client.send_file(destination, path, **kwargs)
                    else:
                        await client.send_file(
                            destination, path, caption=caption or None, **kwargs
                        )
                    return kind
                finally:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
        except Exception:
            pass
        raise RuntimeError(f'ارسال مدیا ناموفق بود: {exc}') from exc


# ============================================================
# Resolve مقصد کاربر
# ============================================================
async def _resolve_destination(client, raw: str):
    """مشابه resolve_destination در media_sender اما ساده‌تر برای این سرویس."""
    raw = (raw or '').strip()
    if not raw:
        raise ValueError('مقصد خالی است.')
    if raw.lower() in ('me', 'self', 'سیو', 'saved', 'saved messages'):
        me = await client.get_me()
        return me, str(me.id), 'Saved Messages'
    if raw.startswith('@'):
        entity = await client.get_entity(raw)
        canonical = f"@{getattr(entity, 'username', raw.lstrip('@'))}"
        title = (
            getattr(entity, 'title', None)
            or f"{getattr(entity, 'first_name', '')} {getattr(entity, 'last_name', '') or ''}".strip()
            or canonical
        )
        return entity, canonical, str(title)
    normalized = raw.replace(' ', '')
    if normalized.lstrip('-').isdigit():
        wanted = int(normalized)
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            try:
                peer_id = int(utils.get_peer_id(entity))
            except Exception:
                continue
            raw_id = int(getattr(entity, 'id', 0) or 0)
            channel_marked = int(f'-100{raw_id}') if raw_id and peer_id < -10**9 else None
            if wanted in {peer_id, raw_id, channel_marked}:
                title = getattr(entity, 'title', None) or (
                    f"{getattr(entity, 'first_name', '')} {getattr(entity, 'last_name', '') or ''}".strip()
                ) or str(peer_id)
                return entity, str(peer_id), str(title)
        try:
            entity = await client.get_entity(wanted)
            peer_id = int(utils.get_peer_id(entity))
            title = getattr(entity, 'title', None) or (
                f"{getattr(entity, 'first_name', '')} {getattr(entity, 'last_name', '') or ''}".strip()
            ) or str(peer_id)
            return entity, str(peer_id), str(title)
        except Exception as exc:
            raise ValueError('این آیدی در گفتگوهای همین حساب پیدا نشد.') from exc
    raise ValueError('مقصد باید @username، آیدی عددی، یا «me» برای Saved Messages باشد.')


# ============================================================
# هندلر capture مقصد
# ============================================================
async def _handle_destination_input(client, owner_id: int, event) -> bool:
    """اگر کاربر در حال انتخاب مقصد کپی محتوا باشد، پیامش را بگیر."""
    state = _get_state(owner_id)
    if state is None:
        return False
    raw = (event.raw_text or '').strip()
    if not raw:
        return False

    if raw in ('لغو', 'cancel', 'انصراف'):
        consume_destination_capture(owner_id)
        try:
            await event.delete()
        except Exception:
            pass
        await client.send_message(
            event.chat_id, '❌ کپی محتوا لغو شد.', parse_mode=None
        )
        return True

    consumed = consume_destination_capture(owner_id)
    if consumed is None:
        return False

    payload = consumed.get('payload', {})
    link_info = payload.get('link_info') or {}
    source_title = payload.get('source_title', 'نامشخص')

    ignore = getattr(client, '_message_saver_ignore', set())
    ignore.add((event.chat_id, event.id))
    client._message_saver_ignore = ignore
    try:
        await event.delete()
    except Exception:
        pass

    try:
        destination, canonical, dest_title = await _resolve_destination(client, raw)
    except Exception as exc:
        await client.send_message(
            event.chat_id,
            f'❌ مقصد نامعتبر است:\n{exc}\n\nدوباره `.کپی محتوا <لینک>` را بزن.',
            parse_mode=None,
        )
        return True

    try:
        source_entity = await resolve_peer(client, link_info)
        message_id = int(link_info.get('message_id') or 0)
        messages = await client.get_messages(source_entity, ids=message_id)
        target_message = messages[0] if isinstance(messages, list) else messages
        if target_message is None:
            raise ValueError('پیام در کانال/گروه پیدا نشد.')

        progress = await client.send_message(
            event.chat_id,
            '⏳ در حال کپی محتوا...',
            parse_mode=None,
        )
        kind = await copy_message_to(client, destination, target_message)

        kind_labels = {
            'text': 'متن', 'photo': 'عکس', 'video': 'ویدیو',
            'voice': 'ویس', 'audio': 'صوت', 'gif': 'گیف',
            'sticker': 'استیکر', 'file': 'فایل', 'location': 'لوکیشن',
            'contact': 'مخاطب', 'poll': 'نظرسنجی', 'other': 'مدیا',
        }
        await progress.edit(
            '✅ **کپی محتوا انجام شد**\n\n'
            f'📦 نوع: `{kind_labels.get(kind, kind)}`\n'
            f'📤 مبدأ: `{source_title}`\n'
            f'📥 مقصد: `{dest_title}`\n'
            f'🆔 `{canonical}`',
            parse_mode='md',
        )
    except Exception as exc:
        await client.send_message(
            event.chat_id,
            f'❌ کپی محتوا ناموفق بود:\n{exc}',
            parse_mode=None,
        )
    return True


def register_copy_protected_handlers(client, owner_id: int) -> None:
    """ثبت هندلر capture مقصد روی کلاینت سلف."""
    owner_id = int(owner_id)

    @client.on(events.NewMessage(outgoing=True))
    async def copy_protected_capture_handler(event):
        if not can_run(owner_id):
            return
        try:
            await _handle_destination_input(client, owner_id, event)
        except Exception as exc:
            print(f'⚠️ copy_protected capture [{owner_id}]: {exc}')