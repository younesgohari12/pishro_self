"""Reliable forced-membership checks for Telethon bot sessions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MissingMembership:
    title: str
    url: str | None
    reason: str = 'not_member'


def _fallback_url(source: Any) -> str | None:
    value = str(source or '').strip()
    if value.startswith('@') and len(value) > 1:
        return f'https://t.me/{value[1:]}'
    if value.startswith('https://t.me/') or value.startswith('http://t.me/'):
        return value
    return None


def _entity_title(entity: Any, source: Any) -> str:
    return str(
        getattr(entity, 'title', None)
        or getattr(entity, 'username', None)
        or source
        or 'کانال تنظیم‌شده'
    )


def _entity_url(entity: Any, source: Any) -> str | None:
    username = getattr(entity, 'username', None)
    return f'https://t.me/{username}' if username else _fallback_url(source)


def _permission_is_member(permission: Any) -> bool:
    if permission is None:
        return False
    if bool(getattr(permission, 'has_left', False)):
        return False
    participant = getattr(permission, 'participant', None)
    participant_name = type(participant).__name__ if participant is not None else ''
    if participant_name == 'ChannelParticipantLeft':
        return False
    if participant_name == 'ChannelParticipantBanned':
        # Telegram uses this type for both kicked and restricted members.
        # A restricted member with ``left=False`` is still a member.
        return not bool(getattr(participant, 'left', True))
    if bool(getattr(permission, 'is_banned', False)):
        return False
    return True


def _not_participant_error(exc: Exception) -> bool:
    return type(exc).__name__ in {
        'UserNotParticipantError', 'ParticipantIdInvalidError',
        'ChannelParticipantLeft', 'ChannelParticipantBanned',
    }


async def check_required_memberships(bot, user_id: int, channels: list[Any]):
    """Return ``(joined, missing)`` and never accept an unresolved channel."""
    missing: list[MissingMembership] = []
    for source in channels:
        source_text = str(source or '').strip()
        if not source_text:
            continue
        entity = None
        try:
            lookup: Any = int(source_text) if source_text.lstrip('-').isdigit() else source_text
            entity = await bot.get_entity(lookup)
            if hasattr(bot, 'get_chat_member'):
                permission = await bot.get_chat_member(entity, int(user_id))
            else:
                permission = await bot.get_permissions(entity, int(user_id))
            if not _permission_is_member(permission):
                missing.append(MissingMembership(
                    _entity_title(entity, source_text), _entity_url(entity, source_text)
                ))
        except Exception as exc:
            reason = 'not_member' if _not_participant_error(exc) else 'check_failed'
            missing.append(MissingMembership(
                _entity_title(entity, source_text),
                _entity_url(entity, source_text) if entity is not None else _fallback_url(source_text),
                reason,
            ))
            if reason == 'check_failed':
                print(
                    f'⚠️ forced-join check failed for {source_text}: '
                    f'{type(exc).__name__}: {exc}'
                )
    return not missing, missing
