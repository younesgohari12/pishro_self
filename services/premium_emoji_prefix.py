"""Prefix self-account output, using the existing emoji-test entity builder.

Only install on an authenticated user client. No catalogue, semantic replacement,
background task, network lookup, global TelegramClient patch, or outgoing handler.
"""
from __future__ import annotations

import copy
import inspect
from contextvars import ContextVar
from functools import wraps

import config
from telethon import errors, utils
from telethon.tl import types
from services import custom_emoji_service as custom

FALLBACK_DOCUMENT_ID = 5938388342281343001  # Visually confirmed by the owner.
PLACEHOLDER = '✨'


def _emoji_rejection(exc):
    # Deliberately exclude timeouts, FloodWait, generic entity/media errors and
    # DocumentInvalid: those do not prove that our custom emoji was rejected.
    return isinstance(exc, (errors.PremiumAccountRequiredError, errors.EmoticonInvalidError)) or (
        isinstance(exc, errors.RPCError) and getattr(exc, 'message', '') in {
        'PREMIUM_ACCOUNT_REQUIRED', 'EMOTICON_INVALID', 'CUSTOM_EMOJI_INVALID',
        'CUSTOM_EMOJI_DOCUMENT_INVALID', 'EMOTICON_DOCUMENT_INVALID',
        'EMOTICON_DOCUMENT_EMPTY',
    })


class PremiumEmojiPrefix:
    def __init__(self, *, premium=False, ids=None):
        # Keep the authenticated premium check as diagnostic state, not a gate:
        # Telegram decides whether the account can send this entity.
        self.premium = bool(premium)
        configured = ids if ids is not None else getattr(
            config, 'PREMIUM_EMOJI_PREFIX_IDS', [FALLBACK_DOCUMENT_ID])
        pool = []
        for value in configured if isinstance(configured, (list, tuple)) else []:
            try:
                value = custom.parse_document_id(value)
            except custom.EmojiError:
                continue
            if value not in pool:
                pool.append(value)
            if len(pool) == 15:
                break
        self.ids = tuple(pool or [FALLBACK_DOCUMENT_ID])
        self.owned_ids = frozenset((*self.ids, FALLBACK_DOCUMENT_ID))
        self.index = 0
        self.bypass = ContextVar('premium_prefix_bypass', default=False)
        self.originals = {}

    def has_prefix(self, text, entities):
        return isinstance(text, str) and text.startswith(PLACEHOLDER + ' ') and any(
            isinstance(e, types.MessageEntityCustomEmoji)
            and e.offset == 0 and e.length == custom.utf16_length(PLACEHOLDER)
            and e.document_id in self.owned_ids for e in entities or []
        )

    def inject(self, text, entities=None):
        """Return text/entities without mutating text or any supplied entity."""
        if not isinstance(text, str) or not text or self.has_prefix(text, entities):
            return text, entities
        document_id = self.ids[self.index]
        self.index = (self.index + 1) % len(self.ids)
        payload = custom.create_custom_emoji(PLACEHOLDER, document_id)
        prefix = payload.text + ' '
        shift = custom.utf16_length(prefix)
        shifted = []
        for entity in entities or []:
            entity = copy.copy(entity)
            entity.offset += shift
            shifted.append(entity)
        return prefix + text, list(payload.entities) + shifted

    def strip_prefix(self, text, entities):
        """Remove only our signature, for rejected already-prefixed edits."""
        if not self.has_prefix(text, entities):
            return text, entities
        shift = custom.utf16_length(PLACEHOLDER + ' ')
        restored = []
        for entity in entities or []:
            if (isinstance(entity, types.MessageEntityCustomEmoji)
                    and entity.offset == 0 and entity.document_id in self.owned_ids):
                continue
            clone = copy.copy(entity)
            end = entity.offset + entity.length
            clone.offset = max(0, entity.offset - shift)
            clone.length = max(0, end - shift) - clone.offset
            if clone.length > 0:
                restored.append(clone)
        return text[len(PLACEHOLDER) + 1:], restored


def install_premium_prefix(client, *, account):
    """Install once, only after get_me() verifies a non-bot account.

    Public wrappers preserve Telethon parsing and returned Message objects.
    _send_album is wrapped separately because send_file can commit multiple
    ten-item albums; fallback must never replay an earlier successful album.
    """
    if getattr(account, 'bot', None) is not False:
        return None
    installed = getattr(client, '_premium_emoji_prefix', None)
    if isinstance(installed, PremiumEmojiPrefix):
        installed.premium = bool(getattr(account, 'premium', False))
        return installed
    engine = PremiumEmojiPrefix(premium=getattr(account, 'premium', False))

    def wrap(original, field, method):
        signature = inspect.signature(original)

        @wraps(original)
        async def wrapped(*args, **kwargs):
            if (engine.bypass.get()
                    or not getattr(config, 'PREMIUM_EMOJI_ENABLED', True)
                    or not getattr(config, 'PREMIUM_EMOJI_PREFIX_ENABLED', True)):
                return await original(*args, **kwargs)
            bound = signature.bind(*args, **kwargs)
            target = field
            if method == 'edit_message':
                peer = bound.arguments.get('entity')
                if isinstance(peer, (types.InputBotInlineMessageID, types.InputBotInlineMessageID64)):
                    return await original(*args, **kwargs)
                if isinstance(peer, types.Message):
                    target = 'message'  # edit_message(message_object, new_text)
            # Telethon's album dispatcher does not itself send a message.
            # Delegate to the wrapped _send_album, which retries one RPC group.
            if method in ('send_message', 'send_file') and utils.is_list_like(bound.arguments.get('file')):
                return await original(*args, **kwargs)

            value = bound.arguments.get(target, '')
            supplied = bound.arguments.get('formatting_entities')
            mode = bound.arguments.get('parse_mode', ())
            fallback = signature.bind(*args, **kwargs)

            async def prepare(text, formatting):
                if not isinstance(text, str) or not text:
                    return text, formatting, text, formatting, False
                if formatting is None:
                    parsed, existing = await client._parse_message_text(text, mode)
                else:
                    parsed, existing = text, formatting
                updated, result = engine.inject(parsed, existing)
                plain, plain_entities = engine.strip_prefix(parsed, existing)
                return updated, result, plain, plain_entities, engine.has_prefix(updated, result)

            if isinstance(value, types.Message):
                text, entities, plain, plain_entities, changed = await prepare(value.message, value.entities or [])
                if changed:
                    updated, restored = copy.copy(value), copy.copy(value)
                    updated.message, updated.entities = text, entities
                    restored.message, restored.entities = plain, plain_entities
                    bound.arguments[target], fallback.arguments[target] = updated, restored
            elif method == '_send_album':
                captions = list(value) if utils.is_list_like(value) else [value]
                supplied = supplied or []
                if supplied and not all(utils.is_list_like(x) for x in supplied):
                    supplied = [supplied]
                prepared = [await prepare(caption, supplied[i] if i < len(supplied) else None)
                            for i, caption in enumerate(captions)]
                changed = any(item[4] for item in prepared)
                if changed:
                    for arguments, text_index, entity_index in ((bound.arguments, 0, 1), (fallback.arguments, 2, 3)):
                        arguments[target] = [item[text_index] or '' for item in prepared]
                        arguments['formatting_entities'] = [item[entity_index] or [] for item in prepared]
                        arguments['parse_mode'] = None
            else:
                text, entities, plain, plain_entities, changed = await prepare(value, supplied)
                if changed:
                    for arguments, body, fmt in ((bound.arguments, text, entities),
                                                 (fallback.arguments, plain, plain_entities)):
                        arguments[target] = body
                        arguments['formatting_entities'] = fmt
                        arguments['parse_mode'] = None
            if not changed:
                return await original(*args, **kwargs)
            token = engine.bypass.set(True)
            try:
                try:
                    return await original(*bound.args, **bound.kwargs)
                except errors.RPCError as exc:
                    if not _emoji_rejection(exc):
                        raise
                    # The rejected call committed no message. Retry exactly once
                    # with original content, not a Unicode-only prefix. Nested
                    # send_message -> send_file calls remain bypassed as well.
                    return await original(*fallback.args, **fallback.kwargs)
            finally:
                engine.bypass.reset(token)
        return wrapped

    for method, field in (('send_message', 'message'), ('send_file', 'caption'),
                          ('edit_message', 'text'), ('_send_album', 'caption')):
        original = getattr(client, method, None)
        if callable(original) and field in inspect.signature(original).parameters:
            engine.originals[method] = original
            setattr(client, method, wrap(original, field, method))
    client._premium_emoji_prefix = engine
    return engine


def uninstall_premium_prefix(client):
    engine = getattr(client, '_premium_emoji_prefix', None)
    if isinstance(engine, PremiumEmojiPrefix):
        for name, original in engine.originals.items():
            setattr(client, name, original)
        del client._premium_emoji_prefix
