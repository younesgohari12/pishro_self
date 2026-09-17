"""Tabchi target discovery and banner sending.

Targets are limited to dialogs already visible to the owner's Telegram account.
The module never joins chats and never discovers arbitrary recipients.
"""
from __future__ import annotations

from typing import Any

from telethon.tl.types import Channel, Chat, User
from telethon.utils import get_peer_id

from database import models


def _dialog_category(entity: Any) -> str | None:
    if isinstance(entity, User):
        if getattr(entity, 'is_self', False) or getattr(entity, 'bot', False) or getattr(entity, 'deleted', False):
            return None
        return 'private'
    if isinstance(entity, Chat):
        return 'group'
    if isinstance(entity, Channel) and bool(getattr(entity, 'megagroup', False)):
        return 'group'
    return None


def _target_title(entity: Any) -> str:
    if isinstance(entity, User):
        name = ' '.join(filter(None, [getattr(entity, 'first_name', None), getattr(entity, 'last_name', None)])).strip()
        return name or getattr(entity, 'username', None) or str(getattr(entity, 'id', 'private'))
    return getattr(entity, 'title', None) or getattr(entity, 'username', None) or str(getattr(entity, 'id', 'group'))


def _is_blacklisted(blocked: set[str], entity: Any, peer_id: int) -> bool:
    candidates = {str(int(peer_id)).lower()}
    raw_id = getattr(entity, 'id', None)
    if raw_id is not None:
        candidates.add(str(int(raw_id)).lower())
    username = getattr(entity, 'username', None)
    if username:
        uname = str(username).strip().lstrip('@').lower()
        if uname:
            candidates.add(uname)
            candidates.add('@' + uname)
    return bool(blocked.intersection(candidates))


async def collect_banner_targets(client, banner: dict, user_id: int) -> list[dict[str, Any]]:
    """Return group/private dialogs selected by this banner.

    Each result includes ``blacklisted`` so the scheduler can explicitly log a
    skipped destination before any send attempt.
    """
    selected = set(models.get_banner_send_targets(banner))
    if not selected:
        return []
    blocked = models.blacklist_values(user_id)
    whitelist_enabled, allowed = models.whitelist_policy(user_id)
    if whitelist_enabled and not allowed:
        return []
    result: list[dict[str, Any]] = []

    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        category = _dialog_category(entity)
        if category is None or category not in selected:
            continue
        try:
            peer_id = int(get_peer_id(entity))
        except Exception:
            continue
        if whitelist_enabled and peer_id not in allowed:
            continue
        result.append({
            'entity': entity,
            'chat_id': peer_id,
            'raw_id': int(getattr(entity, 'id', 0) or 0),
            'username': getattr(entity, 'username', None),
            'title': _target_title(entity)[:200],
            'kind': category,
            'blacklisted': _is_blacklisted(blocked, entity, peer_id),
        })
    return result


async def _source_message(client, banner: dict):
    source_peer = banner.get('source_peer')
    source_message_id = banner.get('source_message_id')
    if not source_peer or not source_message_id:
        return None
    try:
        return await client.get_messages(source_peer, ids=int(source_message_id))
    except Exception:
        return None


class DeliverySkipped(RuntimeError):
    """The destination is no longer permitted; no message was sent."""


def check_delivery_policy(banner, target):
    user_id=int(banner['user_id'])
    entity=target['entity']
    peer_id=int(get_peer_id(entity))
    if peer_id!=int(target['chat_id']):
        raise DeliverySkipped('destination mismatch')
    if _dialog_category(entity) not in models.get_banner_send_targets(banner):
        raise DeliverySkipped('destination category excluded')
    enabled, allowed = models.whitelist_policy(user_id)
    if enabled and peer_id not in allowed:
        raise DeliverySkipped('destination removed from whitelist')
    if _is_blacklisted(models.blacklist_values(user_id),entity,peer_id):
        raise DeliverySkipped('destination is blacklisted')


async def send_banner_to_target(client, banner: dict, target: dict):
    """Send one banner to one already-resolved dialog."""
    peer = target['entity']
    check_delivery_policy(banner,target)
    source = await _source_message(client, banner)
    # Recheck after the network await, just before invoking the send API.
    check_delivery_policy(banner,target)

    if banner['send_mode'] == 'forward':
        if source is None:
            raise RuntimeError('پیام اصلی بنر برای Forward پیدا نشد')
        return await client.forward_messages(peer, source)

    if banner['type'] == 'text':
        text = banner.get('text') or ''
        if not text:
            raise RuntimeError('متن بنر خالی است')
        return await client.send_message(peer, text)

    if source is None or not getattr(source, 'media', None):
        raise RuntimeError('فایل اصلی بنر در تلگرام پیدا نشد')

    caption = banner.get('caption') or ''
    return await client.send_message(peer, caption, file=source.media)
