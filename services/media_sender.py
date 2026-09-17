from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from telethon import types, utils

_TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))


def _entity_title(entity: Any) -> str:
    title = getattr(entity, 'title', None)
    if title:
        return str(title)
    first = getattr(entity, 'first_name', None) or ''
    last = getattr(entity, 'last_name', None) or ''
    name = f'{first} {last}'.strip()
    if name:
        return name
    username = getattr(entity, 'username', None)
    return f'@{username}' if username else str(getattr(entity, 'id', 'مقصد'))


def canonical_entity_id(entity: Any) -> int:
    return int(utils.get_peer_id(entity))


async def resolve_destination(client, raw_destination: str):
    raw = (raw_destination or '').strip()
    if not raw:
        raise ValueError('مقصد خالی است.')

    if raw.startswith('@'):
        entity = await client.get_entity(raw)
        canonical = f"@{getattr(entity, 'username', raw.lstrip('@'))}"
        return entity, canonical, _entity_title(entity)

    normalized = raw.replace(' ', '')
    if normalized.lstrip('-').isdigit():
        wanted = int(normalized)

        # StringSession entity caches may be empty after restart. Searching the
        # account's own dialogs makes numeric private/group/channel IDs reliable
        # without joining or discovering new chats.
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            peer_id = canonical_entity_id(entity)
            raw_id = int(getattr(entity, 'id', 0) or 0)
            channel_marked = int(f'-100{raw_id}') if raw_id and peer_id < -10**9 else None
            if wanted in {peer_id, raw_id, channel_marked}:
                return entity, str(peer_id), _entity_title(entity)

        try:
            entity = await client.get_entity(wanted)
            return entity, str(canonical_entity_id(entity)), _entity_title(entity)
        except Exception as exc:
            raise ValueError('این آیدی در گفتگوهای همین حساب پیدا نشد.') from exc

    raise ValueError('مقصد باید آیدی عددی یا یوزرنیم با @ باشد.')


def destination_matches_chat(raw_destination: str | None, *, chat_id: int | None,
                             chat_username: str | None = None) -> bool:
    raw = (raw_destination or '').strip().lower()
    if not raw or chat_id is None:
        return False
    if raw.startswith('@'):
        username = (chat_username or '').strip().lstrip('@').lower()
        return bool(username) and raw == '@' + username
    try:
        return int(raw) == int(chat_id)
    except Exception:
        return False


def _sender_label(row: dict[str, Any]) -> str:
    username = (row.get('sender_username') or '').strip().lstrip('@')
    if username:
        return '@' + username
    sender_id = row.get('sender_id')
    return str(sender_id) if sender_id is not None else 'نامشخص'


def _format_time(value: str | None) -> str:
    if not value:
        return datetime.now(_TEHRAN_TZ).strftime('%Y-%m-%d %H:%M:%S')
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_TEHRAN_TZ).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return str(value)


def build_deleted_header(row: dict[str, Any]) -> str:
    return (
        '🚨 پیام حذف شده\n\n'
        f"👤 فرستنده:\n{_sender_label(row)}\n\n"
        f"💬 چت:\n{row.get('chat_title') or row.get('chat_id') or 'نامشخص'}\n\n"
        f"⏰ زمان:\n{_format_time(row.get('message_date'))}\n\n"
        '--------------------'
    )


def _extra(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get('extra_json')
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


async def _send_location(client, destination, row: dict[str, Any]) -> bool:
    extra = _extra(row)
    lat = extra.get('lat')
    lon = extra.get('long')
    if lat is None or lon is None:
        return False
    media = types.InputMediaGeoPoint(
        geo_point=types.InputGeoPoint(lat=float(lat), long=float(lon), accuracy_radius=None)
    )
    await client.send_message(destination, message=row.get('caption') or '', file=media)
    return True


async def _send_contact(client, destination, row: dict[str, Any]) -> bool:
    extra = _extra(row)
    phone = extra.get('phone_number')
    first_name = extra.get('first_name')
    if not phone or not first_name:
        return False
    media = types.InputMediaContact(
        phone_number=str(phone),
        first_name=str(first_name),
        last_name=str(extra.get('last_name') or ''),
        vcard=str(extra.get('vcard') or ''),
    )
    await client.send_message(destination, message=row.get('caption') or '', file=media)
    return True


async def _send_cached_file(client, destination, row: dict[str, Any]) -> bool:
    caption = row.get('caption') or ''
    message_type = row.get('message_type') or 'file'
    media_path = row.get('media_path')
    media_id = row.get('media_id')

    # First try Telegram's reusable media reference; fall back to the local
    # cache if Telegram has invalidated that reference after deletion.
    if media_id:
        try:
            remote = utils.resolve_bot_file_id(media_id)
            if remote is not None:
                kwargs: dict[str, Any] = {'caption': caption, 'parse_mode': None}
                if message_type == 'voice':
                    kwargs['voice_note'] = True
                if message_type in {'video', 'gif'}:
                    kwargs['supports_streaming'] = True
                await client.send_file(destination, remote, **kwargs)
                return True
        except Exception:
            pass

    if media_path and os.path.exists(media_path):
        kwargs = {'caption': caption, 'parse_mode': None}
        if message_type == 'voice':
            kwargs['voice_note'] = True
        if message_type in {'video', 'gif'}:
            kwargs['supports_streaming'] = True
        await client.send_file(destination, media_path, **kwargs)
        return True
    return False


async def send_deleted_message(client, destination, row: dict[str, Any]) -> None:
    await client.send_message(destination, build_deleted_header(row), parse_mode=None)

    message_type = row.get('message_type') or 'text'
    text = row.get('text') or ''
    caption = row.get('caption') or ''

    if message_type == 'text':
        await client.send_message(destination, text or '[پیام متنی خالی]', parse_mode=None)
        return

    if message_type == 'location' and await _send_location(client, destination, row):
        return

    if message_type == 'contact' and await _send_contact(client, destination, row):
        return

    if await _send_cached_file(client, destination, row):
        return

    # Last-resort representation stays inside Telegram and contains only data
    # already visible to this account; there is no external upload/fallback.
    fallback = caption or text or f'[مدیای حذف‌شده از نوع {message_type}؛ فایل محلی در دسترس نیست]'
    await client.send_message(destination, fallback, parse_mode=None)
