"""سرویس Message Saver / Anti-Delete + ذخیره مدیا نابودشونده (TTL).

وظایف:
1) کش متادیتا (+ کپی محلی مدیا) پیام‌هایی که همین حساب دیده است.
2) پس از تشخیص حذف، ارسال هدر + محتوای اصلی به مقصد ذخیرهٔ کاربر.
3) دانلود مدیا زمان‌دار/نابودشونده پیش از انقضا و ارسال آن به Saved
   Messages به‌صورت معمولی پس از پایان تایمر.
تمام داده‌ها و کش‌ها با owner_id جداسازی می‌شوند.
"""
from __future__ import annotations

from services.access_service import can_run

import asyncio
import json
import os
import shutil
import sys
from typing import Any

# Bootstrap: حتی اگر این ماژول مستقیم اجرا شود (python services/deleted_handler.py)
# ریشه پروژه در sys.path قرار می‌گیرد تا import ها نشکنند.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from telethon import events, utils

from config import MESSAGE_SAVE_CACHE_DIR
from database import models
from handlers.save_message import consume_destination_capture, has_destination_capture
from services.logging_service import get_logger, log_api_error
from services.media_sender import (
    destination_matches_chat,
    resolve_destination,
    send_deleted_message,
)

_DOWNLOADABLE_TYPES = {'photo', 'video', 'voice', 'audio', 'file', 'sticker', 'gif'}

# تسک‌های ارسال مدیا نابودشونده به تفکیک مالک: {owner_id: set[Task]}
_TTL_TASKS: dict[int, set] = {}
_logger = get_logger('message_saver')


def _ttl_task_done(owner_id: int, task) -> None:
    """Drop finished TTL tasks and the now-empty owner bucket."""
    uid = int(owner_id)
    tasks = _TTL_TASKS.get(uid)
    if not tasks:
        return
    tasks.discard(task)
    if not tasks:
        _TTL_TASKS.pop(uid, None)


# ========================================
# تشخیص نوع پیام و متادیتا
# ========================================
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
    if getattr(message, 'contact', None):
        return 'contact'
    if getattr(message, 'geo', None) or getattr(message, 'venue', None):
        return 'location'
    if getattr(message, 'document', None):
        return 'file'
    if getattr(message, 'media', None):
        return 'other'
    return 'text'


def _media_id(message) -> str | None:
    media = getattr(message, 'media', None)
    if media is None:
        return None
    # pack_bot_file_id expects the underlying Photo/Document object on
    # Telethon 1.x, not necessarily the MessageMedia wrapper.
    candidate = getattr(message, 'photo', None) or getattr(message, 'document', None) or media
    try:
        return utils.pack_bot_file_id(candidate)
    except Exception:
        return None


def _extra_payload(message, kind: str) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if kind == 'location':
        geo = getattr(message, 'geo', None)
        venue = getattr(message, 'venue', None)
        if venue is not None:
            geo = getattr(venue, 'geo', None) or geo
        extra.update({
            'venue_title': getattr(venue, 'title', None),
            'venue_address': getattr(venue, 'address', None),
        })
        if geo is not None:
            extra['lat'] = getattr(geo, 'lat', None)
            extra['long'] = getattr(geo, 'long', None)
    elif kind == 'contact':
        contact = getattr(message, 'contact', None)
        if contact is not None:
            extra = {
                'phone_number': getattr(contact, 'phone_number', None),
                'first_name': getattr(contact, 'first_name', None),
                'last_name': getattr(contact, 'last_name', None),
                'vcard': getattr(contact, 'vcard', None),
                'user_id': getattr(contact, 'user_id', None),
            }
    elif kind == 'other':
        media = getattr(message, 'media', None)
        # نکته: `class` کلمه رزرو پایتون است؛ نام کلاس با type() گرفته می‌شود.
        extra['media_class'] = type(media).__name__ if media is not None else None
    return extra


def _chat_title(chat, chat_id: int | None) -> str:
    if chat is not None:
        title = getattr(chat, 'title', None)
        if title:
            return str(title)
        first = getattr(chat, 'first_name', None) or ''
        last = getattr(chat, 'last_name', None) or ''
        name = f'{first} {last}'.strip()
        if name:
            return name
        username = getattr(chat, 'username', None)
        if username:
            return '@' + str(username)
    return str(chat_id or 'نامشخص')


def _sender_username(sender) -> str | None:
    username = getattr(sender, 'username', None) if sender is not None else None
    return str(username) if username else None


def _is_control_message(message) -> bool:
    raw = (getattr(message, 'raw_text', None) or getattr(message, 'message', None) or '').strip()
    return bool(getattr(message, 'out', False) and raw.startswith('.'))


# ========================================
# کش رسانه Anti-Delete
# ========================================
async def _download_media(message, owner_id: int, chat_id: int, message_id: int, kind: str) -> str | None:
    if kind not in _DOWNLOADABLE_TYPES or not getattr(message, 'media', None):
        return None
    folder = os.path.join(MESSAGE_SAVE_CACHE_DIR, str(owner_id), str(chat_id), str(message_id))
    os.makedirs(folder, exist_ok=True)
    try:
        path = await message.download_media(file=folder + os.sep)
        return str(path) if path else None
    except Exception as exc:
        log_api_error('message_saver_media_cache', exc, owner_id=owner_id, chat_id=chat_id)
        return None


async def cache_message(client, owner_id: int, event) -> None:
    settings = models.get_save_settings(owner_id)
    if not settings.get('status'):
        return
    message = event.message
    chat_id = event.chat_id
    if chat_id is None or _is_control_message(message):
        return
    try:
        chat = await event.get_chat()
    except Exception:
        chat = None
    chat_username = getattr(chat, 'username', None) if chat is not None else None
    if destination_matches_chat(settings.get('destination'), chat_id=chat_id, chat_username=chat_username):
        return
    try:
        sender = await event.get_sender()
    except Exception:
        sender = None
    kind = _message_type(message)
    raw_text = getattr(message, 'raw_text', None) or getattr(message, 'message', None) or ''
    text = raw_text if kind == 'text' else None
    caption = raw_text if kind != 'text' else None
    media_id = _media_id(message)
    # Persist metadata first to reduce the race window for immediately-deleted
    # messages, then enrich the row with a downloaded local media copy.
    models.cache_message_record(
        owner_id=owner_id,
        chat_id=int(chat_id),
        sender_id=getattr(message, 'sender_id', None),
        sender_username=_sender_username(sender),
        message_id=int(message.id),
        message_type=kind,
        text=text,
        caption=caption,
        media_id=media_id,
        media_path=None,
        chat_title=_chat_title(chat, chat_id),
        message_date=getattr(message, 'date', None).isoformat() if getattr(message, 'date', None) else None,
        extra_json=json.dumps(_extra_payload(message, kind), ensure_ascii=False),
    )
    media_path = await _download_media(message, owner_id, int(chat_id), int(message.id), kind)
    if media_path:
        models.update_message_cache_media(owner_id, int(chat_id), int(message.id), media_path)


def purge_owner_cache_files(owner_id: int) -> None:
    folder = os.path.join(MESSAGE_SAVE_CACHE_DIR, str(int(owner_id)))
    try:
        shutil.rmtree(folder, ignore_errors=True)
    finally:
        models.clear_message_cache(int(owner_id))


# ========================================
# Capture مقصد ذخیره (آیدی/@username)
# ========================================
async def _handle_destination_input(client, owner_id: int, event) -> bool:
    if not has_destination_capture(owner_id):
        return False
    raw = (event.raw_text or '').strip()
    if not raw:
        return False
    consume_destination_capture(owner_id)
    ignore = getattr(client, '_message_saver_ignore', set())
    ignore.add((event.chat_id, event.id))
    client._message_saver_ignore = ignore
    try:
        await event.delete()
    except Exception:
        pass
    try:
        _, canonical, title = await resolve_destination(client, raw)
        models.set_save_destination(owner_id, canonical)
        await client.send_message(
            event.chat_id,
            f'✅ محل ذخیره ثبت شد.\n\n📍 مقصد: {title}\n🆔 {canonical}\n\nحالا از `.پنل` → `💾 سیو پیام` می‌توانید سرویس را فعال کنید.',
            parse_mode=None,
        )
    except Exception as exc:
        await client.send_message(
            event.chat_id,
            f'❌ مقصد ثبت نشد:\n{exc}\n\nدوباره از `.پنل` → `💾 سیو پیام` → `📍 تعیین محل ذخیره` اقدام کنید.',
            parse_mode=None,
        )
    return True


# ========================================
# پردازش رویداد حذف
# ========================================
async def _handle_deleted(client, owner_id: int, event) -> None:
    settings = models.get_save_settings(owner_id)
    if not settings.get('status') or not settings.get('destination'):
        return
    try:
        destination, _, _ = await resolve_destination(client, settings['destination'])
    except Exception as exc:
        log_api_error('message_saver_destination_resolve', exc, owner_id=owner_id)
        return
    event_chat_id = getattr(event, 'chat_id', None)
    for message_id in list(getattr(event, 'deleted_ids', []) or []):
        candidates = models.find_cached_deleted_candidates(
            owner_id=owner_id,
            message_id=int(message_id),
            chat_id=int(event_chat_id) if event_chat_id is not None else None,
        )
        # Telegram's non-channel delete update may omit the peer. In that case
        # only act when this message id maps to exactly one cached dialog, so a
        # deletion can never be attributed to the wrong private/group chat.
        if event_chat_id is None and len(candidates) != 1:
            continue
        for row in candidates:
            if models.deleted_message_exists(owner_id, row['chat_id'], row['message_id']):
                continue
            models.record_deleted_message(row)
            try:
                await send_deleted_message(client, destination, row)
                media_path = row.get('media_path')
                models.delete_message_cache(owner_id, row['chat_id'], row['message_id'])
                if media_path:
                    try:
                        os.remove(media_path)
                    except OSError:
                        pass
            except Exception as exc:
                log_api_error('message_saver_send', exc, owner_id=owner_id, chat_id=row.get('chat_id'), message_id=message_id)


# ========================================
# مدیا نابودشونده (TTL) → Saved Messages
# ========================================
def _ttl_seconds(message) -> int:
    """تایمر نابودشدن (ثانیه) از media.ttl_seconds یا message.ttl_seconds."""
    media = getattr(message, 'media', None)
    ttl = getattr(media, 'ttl_seconds', None) if media is not None else None
    if not ttl:
        ttl = getattr(message, 'ttl_seconds', None)
    try:
        return int(ttl or 0)
    except Exception:
        return 0


async def _download_ttl_media(message, owner_id: int, chat_id: int, message_id: int, kind: str) -> str | None:
    """دانلود فوری مدیا نابودشونده در پوشه جدا، پیش از پایان تایمر."""
    if kind not in _DOWNLOADABLE_TYPES or not getattr(message, 'media', None):
        return None
    folder = os.path.join(
        MESSAGE_SAVE_CACHE_DIR, 'ttl', str(owner_id), str(chat_id), str(message_id)
    )
    os.makedirs(folder, exist_ok=True)
    try:
        path = await message.download_media(file=folder + os.sep)
        return str(path) if path else None
    except Exception as exc:
        log_api_error('ttl_media_cache', exc, owner_id=owner_id, chat_id=chat_id)
        return None


async def _deliver_ttl_media(client, owner_id: int, meta: dict[str, Any],
                             path: str, ttl: int, kind: str, caption: str | None) -> None:
    """پس از پایان تایمر، مدیا را به‌صورت معمولی در Saved Messages بفرست."""
    try:
        await asyncio.sleep(max(1, int(ttl)) + 1)

        header = (
            '📸 مدیا نابودشونده ذخیره شد\n\n'
            f"👤 فرستنده:\n{meta['sender']}\n\n"
            f"💬 چت:\n{meta['chat_title']}\n\n"
            f"🕐 تایمر اصلی:\n{ttl} ثانیه\n\n"
            '--------------------'
        )
        await client.send_message('me', header, parse_mode=None)

        kwargs: dict[str, Any] = {'caption': caption or None, 'parse_mode': None}
        if kind == 'voice':
            kwargs['voice_note'] = True
        if kind in {'video', 'gif'}:
            kwargs['supports_streaming'] = True
        await client.send_file('me', path, **kwargs)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log_api_error('ttl_media_deliver', exc, owner_id=owner_id)
    finally:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


async def _handle_ttl_media(client, owner_id: int, event) -> None:
    """ثبت و زمان‌بندی مدیا زمان‌دار/نابودشونده."""
    message = event.message
    if message is None or not getattr(message, 'media', None):
        return

    ttl = _ttl_seconds(message)
    if ttl <= 0:
        return

    # اگر سرویس سلف برای این کاربر خاموش است، چیزی ذخیره نشود.
    try:
        import db
        if not db.get_user_settings(owner_id).get('self_enabled', True):
            return
    except Exception:
        pass

    kind = _message_type(message)
    if kind not in _DOWNLOADABLE_TYPES:
        return

    chat_id = event.chat_id
    if chat_id is None:
        return

    path = await _download_ttl_media(message, owner_id, int(chat_id), int(message.id), kind)
    if not path:
        return

    try:
        sender = await event.get_sender()
    except Exception:
        sender = None
    try:
        chat = await event.get_chat()
    except Exception:
        chat = None

    sender_username = _sender_username(sender)
    meta = {
        'sender': ('@' + sender_username) if sender_username else str(getattr(message, 'sender_id', None) or 'نامشخص'),
        'chat_title': _chat_title(chat, chat_id),
    }
    raw_text = getattr(message, 'raw_text', None) or getattr(message, 'message', None) or ''
    caption = raw_text if kind != 'text' else None

    task = asyncio.create_task(
        _deliver_ttl_media(client, owner_id, meta, path, ttl, kind, caption)
    )
    tasks = _TTL_TASKS.setdefault(int(owner_id), set())
    tasks.add(task)
    task.add_done_callback(
        lambda done, uid=int(owner_id): _ttl_task_done(uid, done)
    )


def cancel_owner_ttl_tasks(owner_id: int) -> None:
    """لغو همه ارسال‌های در انتظار مدیا نابودشونده یک کاربر."""
    tasks = _TTL_TASKS.pop(int(owner_id), set())
    for task in list(tasks):
        if not task.done():
            task.cancel()


# ========================================
# ثبت هندلرها روی کلاینت سلف
# ========================================
def register_deleted_message_handlers(client, owner_id: int) -> None:
    owner_id = int(owner_id)
    client._message_saver_ignore = getattr(client, '_message_saver_ignore', set())

    @client.on(events.NewMessage(outgoing=True))
    async def destination_capture_handler(event):
        if not can_run(owner_id):
            return
        try:
            await _handle_destination_input(client, owner_id, event)
        except Exception as exc:
            log_api_error('saver_destination_capture', exc, owner_id=owner_id)

    @client.on(events.NewMessage)
    async def message_cache_handler(event):
        if not can_run(owner_id):
            return
        try:
            key = (event.chat_id, event.id)
            ignore = getattr(client, '_message_saver_ignore', set())
            if key in ignore:
                ignore.discard(key)
                return
            await cache_message(client, owner_id, event)
        except Exception as exc:
            log_api_error('saver_cache', exc, owner_id=owner_id)

    @client.on(events.NewMessage(incoming=True))
    async def ttl_media_handler(event):
        if not can_run(owner_id):
            return
        try:
            await _handle_ttl_media(client, owner_id, event)
        except Exception as exc:
            log_api_error('ttl_saver_capture', exc, owner_id=owner_id)

    @client.on(events.MessageDeleted)
    async def message_deleted_handler(event):
        if not can_run(owner_id):
            return
        try:
            await _handle_deleted(client, owner_id, event)
        except Exception as exc:
            log_api_error('saver_delete_event', exc, owner_id=owner_id)

            