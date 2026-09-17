"""Backward-compatible import shim for the unified Custom Emoji service."""
from services.custom_emoji_service import (  # noqa: F401
    INVALID_CUSTOM_EMOJI,
    CustomEmojiEntityPayload,
    EmojiError,
    build_test_message,
    contains_custom_emoji,
    create_custom_emoji,
    detailed_result_text,
    extract_custom_emojis,
    parse_document_id,
    resolve_custom_emoji,
    result_pages,
    send_custom_emoji,
    utf16_length,
)

__all__ = [
    'INVALID_CUSTOM_EMOJI', 'CustomEmojiEntityPayload', 'EmojiError',
    'build_test_message', 'contains_custom_emoji', 'create_custom_emoji',
    'detailed_result_text', 'extract_custom_emojis', 'parse_document_id',
    'resolve_custom_emoji', 'result_pages', 'send_custom_emoji', 'utf16_length',
]
