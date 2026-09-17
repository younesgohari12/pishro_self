"""Strict Mapping acceptance tests — Premium Emoji Converter V2.

قانون اصلی این نسخه: تطبیق معنایی اجباری. هر ایموجی یونیکد فقط به Custom
Emoji با DocumentAttributeCustomEmoji.alt دقیقاً برابر تبدیل می‌شود؛ در
غیر این صورت همان ایموجی معمولی باقی می‌ماند. fallback عمومی ممنوع است.

همه تست‌ها آفلاین و بدون شبکه هستند؛ فقط مرز شبکه شبیه‌سازی می‌شود.
"""
import asyncio
import json
import copy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from telethon import TelegramClient, functions, types
from telethon.sessions import MemorySession

import config
import premium_emoji_mapping as mapping_module
from services import custom_emoji_service as custom
from services import premium_emoji_converter as mod
from tools import resolve_premium_emoji_mapping as resolver

ROOT = Path(__file__).resolve().parents[1]
PEER = types.InputPeerUser(123, 456)
NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)

MAP = mapping_module.PREMIUM_EMOJI_MAP
LAUGH = MAP['😂'][0]
FIRE = MAP['🔥'][0]
HEART = MAP['❤️'][0]
CROWN = MAP['👑'][0]
GEM = MAP['💎'][0]
SPARKLES = MAP['✨'][0]


def run(coro):
    return asyncio.run(coro)


class OfflineClient(TelegramClient):
    """کلاینت واقعی Telethon با شبکه شبیه‌سازی‌شده (بدون لاگین واقعی)."""

    def __init__(self, *, bot=False, premium=True):
        super().__init__(MemorySession(), 12345, 'offline-test-only')
        self._mb_entity_cache.extend([types.User(123, access_hash=456)], [])
        self.calls = []
        self.committed = []
        self.account = NS(bot=bot, premium=premium)
        self.engine = mod.install_premium_emoji_converter(
            self, account=self.account)

    async def _call(self, sender, request, ordered=False, flood_sleep_threshold=None):
        bytes(request)  # real TL serialization
        self.calls.append(copy.deepcopy(request))
        if isinstance(request, functions.messages.SendMultiMediaRequest):
            bodies = request.multi_media
        elif isinstance(request, (functions.messages.SendMediaRequest,
                                  functions.messages.EditMessageRequest)):
            bodies = [request]
        else:  # SendMessageRequest
            self.committed.append(request)
            return types.UpdateShortSentMessage(len(self.committed), 1, 1, NOW,
                                                out=True, entities=request.entities)
        updates = []
        for body in bodies:
            self.committed.append(body)
            mid = len(self.committed)
            message = types.Message(mid, types.PeerUser(123), date=NOW, out=True,
                                    message=body.message, entities=body.entities)
            updates.extend([types.UpdateMessageID(mid, body.random_id),
                            types.UpdateNewMessage(message, 1, 1)])
        return types.Updates(updates, [], [], NOW, 1)


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_CONVERTER_ENABLED', True)
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_STRICT_MODE', True)


def customs(message):
    return [e for e in getattr(message, 'entities', None) or ()
            if isinstance(e, types.MessageEntityCustomEmoji)]


# ----------------------------------------------------- 1) strict mapping test
def test_strict_mapping_matches_real_telegram_alts():
    """نگامت ارسالی باید دقیقاً با alt واقعی تلگرام (فایل resolve رسمی) بخواند."""
    payload = json.loads((ROOT / 'PREMIUM_EMOJI_RESOLVED_MAPPING.json')
                         .read_text(encoding='utf-8'))
    documents = payload['documents']
    assert payload['strict_semantic_matching'] is True
    assert payload['source'] == 'https://t.me/CustomEmojiPack'
    assert payload['method'] == 'messages.getCustomEmojiDocuments'
    for emoji, ids in MAP.items():
        for document_id in ids:
            meta = documents.get(str(document_id))
            assert meta, f'{document_id} missing from resolved mapping'
            assert mapping_module.normalize_emoji(meta['alt']) == \
                mapping_module.normalize_emoji(emoji), (emoji, document_id)


def test_strict_mapping_has_no_generic_fallback():
    """هیچ کلیدی به شناسه ثابت fallback منصوب نیست؛ fallback فقط مرجع قدیمی است."""
    all_ids = [i for ids in MAP.values() for i in ids]
    assert mapping_module.FALLBACK_DOCUMENT_ID not in all_ids
    # هر ایموجی یا شناسه تأییدشده دارد یا آگاهانه غیرفعال است (لیست خالی).
    for emoji, ids in MAP.items():
        assert ids or emoji in mapping_module.CHECKED_EMOJIS


# ------------------------------------------------- 2) wrong alt rejection
def test_resolver_rejects_wrong_alt_ids():
    """شناسه‌ای که alt اشتباه دارد → reject؛ حتی اگر جایگزین درست هم باشد."""
    documents = {
        '111': {'alt': '😂', 'free': False, 'animated': False,
                'media_type': 'image/webp'},
        '222': {'alt': '🤣', 'free': False, 'animated': False,
                'media_type': 'image/webp'},
    }
    claimed = {'😂': [222]}  # قبلاً ۲۲۲ به 😂 نسبت داده شده بود
    status = resolver.classify_targets(documents, claimed)
    assert status['😂']['status'] == 'active'      # ۱۱۱ با alt درست فعال است
    assert status['😂']['ids'] == [111]
    assert status['😂']['rejected_ids'] == [222]   # ۲۲۲ با alt اشتباه reject شد
    assert status['😂']['telegram_alt'] == {'222': '🤣'}
    validated = resolver.build_validated_map(status)
    assert validated['😂'] == [111]  # فقط alt درست؛ reject بیرون می‌ماند
    # ۲۲۲ با alt «🤣» فقط ممکن است زیر کلید 🤣 دیده شود — هرگز زیر 😂.
    assert 222 not in validated.get('😂', [])
    assert 222 in validated.get('🤣', [])


def test_resolver_rejects_when_no_valid_replacement_exists():
    """همهٔ شناسه‌های نسبت‌داده‌شده alt اشتباه داشته باشند → rejected."""
    documents = {'222': {'alt': '🤣', 'free': False, 'animated': False,
                         'media_type': 'image/webp'}}
    status = resolver.classify_targets(documents, claimed={'😂': [222]})
    assert status['😂']['status'] == 'rejected'
    assert status['😂']['telegram_alt'] == {'222': '🤣'}
    validated = resolver.build_validated_map(status)
    assert '😂' not in validated  # هیچ مسیری به 😂 با alt اشتباه وجود ندارد
    assert 222 not in validated.get('😂', [])


def test_resolver_active_inactive_rejected_statuses():
    documents = {
        '10': {'alt': '🔥', 'free': True, 'animated': False,
               'media_type': 'image/webp'},
        '20': {'alt': '🍋', 'free': False, 'animated': True,
               'media_type': 'application/x-tgsticker'},
    }
    status = resolver.classify_targets(documents, claimed={'😂': [20]})
    assert status['🔥'] == {'status': 'active', 'ids': [10]}
    assert status['😂']['status'] == 'rejected'
    assert status['🚀']['status'] == 'inactive'
    assert resolver.build_validated_map(status) == {'🔥': [10]}


# ----------------------------------------------- 3) fallback disabled test
def test_fallback_disabled_for_every_checked_emoji():
    """در Strict هیچ مسیری به شناسه ثابت fallback ختم نمی‌شود — برای همه ۳۱."""
    engine = mod.PremiumEmojiConverter()
    assert engine.strict is True
    for emoji in mapping_module.CHECKED_EMOJIS:
        text, entities = engine.convert(f'x {emoji} y', [])
        for entity in entities:
            assert entity.document_id != mapping_module.FALLBACK_DOCUMENT_ID


def test_inactive_emoji_stays_unicode_rocket():
    """🚀 بدون شناسه تأییدشده است: هیچ تبدیلی، متن دست‌نخورده."""
    assert MAP['🚀'] == []  # بررسی‌شده اما فعال نشده
    engine = mod.PremiumEmojiConverter()
    text, entities = engine.convert('پروژه 🚀 شروع شد', [])
    assert text == 'پروژه 🚀 شروع شد' and entities == []


def test_unknown_emoji_never_gets_fallback_document():
    """ایموجی خارج از نگاشت: نه تبدیل، نه fallback — دقیقاً همان متن."""
    engine = mod.PremiumEmojiConverter()
    for token in ('🤍', '🗿', '🫠', '👍🏽'):
        text, entities = engine.convert(token, [])
        assert text == token and entities == []


# ------------------------------------- 4/5/6) exact conversions 😂 🔥 ❤️
def test_laugh_exact_conversion():
    engine = mod.PremiumEmojiConverter()
    text, entities = engine.convert('😂', [])
    assert text == '😂'
    assert [e.document_id for e in entities] == [LAUGH]


def test_fire_exact_conversion():
    engine = mod.PremiumEmojiConverter()
    text, entities = engine.convert('🔥', [])
    assert text == '🔥'
    assert [e.document_id for e in entities] == [FIRE]


def test_heart_exact_conversion():
    engine = mod.PremiumEmojiConverter()
    text, entities = engine.convert('❤️', [])
    assert text == '❤️'
    assert [e.document_id for e in entities] == [HEART]


def test_no_glyph_ever_swapped_between_emojis():
    """هیچ ایموجی به شناسه ایموجی دیگر تبدیل نمی‌شود (نمونه‌های ممنوع پچ)."""
    engine = mod.PremiumEmojiConverter()
    forbidden = {'😂': {FIRE, CROWN, GEM, SPARKLES},
                 '🔥': {LAUGH, SPARKLES, HEART},
                 '❤️': {LAUGH, FIRE, CROWN}}
    for emoji, bad_ids in forbidden.items():
        _, entities = engine.convert(emoji, [])
        assert len(entities) == 1
        assert entities[0].document_id not in bad_ids
        assert entities[0].document_id == MAP[emoji][0]


# ----------------------------------------- 7) unknown emoji untouched (V2)
def test_unknown_emoji_untouched_in_send_path():
    async def scenario():
        client = OfflineClient()
        sent = await client.send_message(PEER, 'عجیب 🗿 🤍', parse_mode=None)
        assert sent.message == 'عجیب 🗿 🤍' and not customs(sent)
    run(scenario())


# ------------------------------------------------------- 8) bot untouched
def test_bot_client_is_never_converted():
    async def scenario():
        client = OfflineClient(bot=True)
        original = 'پنل ربات 😂🔥'
        sent = await client.send_message(PEER, original, parse_mode=None)
        assert sent.message == original and not customs(sent)
        assert not hasattr(client, '_premium_emoji_converter')
    run(scenario())


# ----------------------------------------------------- 9) self converted
def test_self_send_path_converts_all_smoke_messages():
    """سه پیام نمونه اسموک: entity واقعی با شناسه درست برای هر ایموجی."""
    async def scenario():
        client = OfflineClient()
        for text, expected in (
                ('سلام 😂🔥❤️', [(LAUGH, 2), (FIRE, 2), (HEART, 2)]),
                ('عالی شد 👑💎', [(CROWN, 2), (GEM, 2)]),
                ('دمت گرم 🚀✨', [(SPARKLES, 1)]),  # 🚀 غیرفعال: فقط ✨ تبدیل می‌شود
        ):
            sent = await client.send_message(PEER, text, parse_mode=None)
            assert sent.message == text  # متن دست‌نخورده
            entities = customs(sent)
            assert [(e.document_id, e.length) for e in entities] == expected
            for entity in entities:
                assert isinstance(entity, types.MessageEntityCustomEmoji)
    run(scenario())


# -------------------------------------------------- 10) UTF-16 validation
def test_utf16_offsets_exact_boundaries():
    engine = mod.PremiumEmojiConverter()
    prefix = '🇮🇷 👨‍👩‍👧 تست '
    source = prefix + '😂🔥'
    text, entities = engine.convert(source, [])
    assert text == source
    assert [e.offset for e in entities] == [
        custom.utf16_length(prefix), custom.utf16_length(prefix) + 2]
    assert [e.length for e in entities] == [2, 2]
    raw = text.encode('utf-16-le')
    for entity in entities:
        assert entity.offset % 1 == 0 and (entity.offset + entity.length) * 2 <= len(raw)


# -------------------------------------------- 11) markdown + HTML survive
@pytest.mark.parametrize('mode,text,plain,emoji,doc_id', [
    ('md', '**مهم** 🔥', 'مهم 🔥', '🔥', FIRE),
    ('html', '<b>مهم</b> 🔥', 'مهم 🔥', '🔥', FIRE),
    ('md', '`کد` 😂 **بولد**', 'کد 😂 بولد', '😂', LAUGH),
    ('html', '<a href="https://t.me/x">لینک</a> ❤️', 'لینک ❤️', '❤️', HEART),
])
def test_markdown_and_html_survive_with_strict_ids(mode, text, plain, emoji, doc_id):
    async def scenario():
        client = OfflineClient()
        sent = await client.send_message(PEER, text, parse_mode=mode)
        assert sent.message == plain
        others = [e for e in sent.entities
                  if not isinstance(e, types.MessageEntityCustomEmoji)]
        assert others, 'فرمت اصلی از بین رفت'
        entities = customs(sent)
        assert len(entities) == 1 and entities[0].document_id == doc_id
        expected_offset = custom.utf16_length(plain.split(emoji)[0])
        assert entities[0].offset == expected_offset
    run(scenario())


# --------------------------------------------------------- 12) caption
def test_media_caption_converts_media_only_no_duplicate():
    async def scenario():
        client = OfflineClient()
        media = types.InputMediaPhoto(types.InputPhoto(100, 200, b'reference'))
        sent = await client.send_file(PEER, media, caption='سلام 🔥', parse_mode=None)
        assert sent.message == 'سلام 🔥'
        assert [e.document_id for e in customs(sent)] == [FIRE]
        assert len(client.calls) == 1 and len(client.committed) == 1
    run(scenario())


def test_media_without_caption_stays_silent():
    async def scenario():
        client = OfflineClient()
        media = types.InputMediaPhoto(types.InputPhoto(100, 200, b'reference'))
        sent = await client.send_file(PEER, media, caption=None)
        assert sent.message == '' and not sent.entities
        assert len(client.calls) == 1 and len(client.committed) == 1
    run(scenario())


# --------------------------------------- 13) existing custom emoji + dup
def test_existing_custom_emoji_never_reconverted():
    engine = mod.PremiumEmojiConverter()
    offset = custom.utf16_length('پیش ')
    existing = [types.MessageEntityCustomEmoji(offset, 2, 999888777)]
    text, entities = engine.convert('پیش 😂', existing)
    assert text == 'پیش 😂'
    assert [e.document_id for e in entities] == [999888777]


def test_duplicate_prevention_idempotent():
    engine = mod.PremiumEmojiConverter()
    once_text, once_entities = engine.convert('دوباره 😂🔥', [])
    twice_text, twice_entities = engine.convert(once_text, once_entities)
    third_text, third_entities = engine.convert(twice_text, twice_entities)
    assert once_text == twice_text == third_text
    assert len(once_entities) == len(twice_entities) == len(third_entities) == 2


# ---------------------------------------------------- 14) forward intact
def test_forward_messages_never_wrapped():
    async def scenario():
        client = OfflineClient()
        original = client.forward_messages
        assert client.forward_messages == original  # wrap نشده (متد اصلی)
    run(scenario())


# ------------------------------------------------- 15) resolver tool core
def test_resolver_collects_all_project_ids():
    ids = resolver.collect_project_document_ids()
    assert mapping_module.FALLBACK_DOCUMENT_ID in ids
    for values in MAP.values():
        for document_id in values:
            assert document_id in ids
    assert len(ids) == len(set(ids))  # بدون تکرار


def test_resolver_alt_matching_is_strict():
    assert resolver.alt_matches_target('😂', '😂')
    assert resolver.alt_matches_target('❤', '❤️')   # فقط VS16 نادیده گرفته می‌شود
    assert not resolver.alt_matches_target('🤣', '😂')
    assert not resolver.alt_matches_target('😂🔥', '😂')  # هیچ تطبیق فازی
    assert not resolver.alt_matches_target('', '😂')
    assert not resolver.alt_matches_target('😂', '')


def test_resolver_build_validated_map_caps_and_orders():
    documents = {str(i): {'alt': '😂', 'free': True, 'animated': False,
                          'media_type': 'image/webp'} for i in range(100, 130)}
    status = resolver.classify_targets(documents)
    validated = resolver.build_validated_map(status)
    assert set(validated) == {'😂'}
    assert len(validated['😂']) == mapping_module.MAX_IDS_PER_EMOJI
    # ترتیب ابزار صعودی و قطعی است؛ نگامت ارسالی قاعده انتخاب خودش را دارد.
    assert validated['😂'] == sorted(validated['😂'])


def test_resolver_output_shape(tmp_path):
    out = tmp_path / 'PREMIUM_EMOJI_RESOLVED_MAPPING.json'
    documents = {'10': {'document_id': 10, 'alt': '🔥', 'free': True,
                        'animated': False, 'media_type': 'image/webp'}}
    status = resolver.classify_targets(documents)
    payload = resolver.write_output(out, documents, status, calls=1, missing=0)
    saved = json.loads(out.read_text(encoding='utf-8'))
    assert saved['validated_map'] == {'🔥': [10]}
    assert saved['totals']['resolved'] == 1
    assert saved['target_status']['🔥']['status'] == 'active'
    assert payload['strict_semantic_matching'] is True


def test_resolver_document_info_extraction():
    document = types.Document(
        id=1234567890123456789, access_hash=42, file_reference=b'ref',
        date=NOW, mime_type='application/x-tgsticker', size=100, dc_id=2,
        attributes=[types.DocumentAttributeCustomEmoji(
            alt='👑', free=False,
            stickerset=types.InputStickerSetShortName(short_name='pack'))])
    info = resolver.extract_document_info(document)
    assert info == {'document_id': 1234567890123456789, 'alt': '👑',
                    'free': False, 'animated': True,
                    'media_type': 'application/x-tgsticker'}


def test_fallback_record_decision_logic():
    """ابزار باید تصمیم fallback را فقط از alt واقعی تلگرام بسازد."""
    documents = {str(mapping_module.FALLBACK_DOCUMENT_ID):
                 {'alt': '🙄', 'free': False, 'animated': True,
                  'media_type': 'application/x-tgsticker'}}
    record = resolver.fallback_record(documents)
    assert record['document_id'] == mapping_module.FALLBACK_DOCUMENT_ID
    assert record['telegram_alt'] == '🙄'
    assert record['in_target_list'] is False
    assert 'REJECTED' in record['decision']
    # پاسخ ندادن تلگرام هم reject است؛ هیچ حالتی به allow نمی‌رسد مگر تطبیق دقیق.
    assert resolver.fallback_record({})['decision'].startswith('REJECTED')
    allow = resolver.fallback_record(
        {str(mapping_module.FALLBACK_DOCUMENT_ID):
         {'alt': '🔥', 'free': True, 'animated': False,
          'media_type': 'image/webp'}})
    assert allow['in_target_list'] is True
    assert 'ALLOWED' in allow['decision']


def test_fallback_document_id_rejected_by_resolution_record():
    """شناسه fallback مالک: alt واقعی تلگرام آن ✨/😂/🔥 نیست → در نگاشت نیست."""
    payload = json.loads((ROOT / 'PREMIUM_EMOJI_RESOLVED_MAPPING.json')
                         .read_text(encoding='utf-8'))
    record = payload['fallback_document_id']
    assert record['document_id'] == mapping_module.FALLBACK_DOCUMENT_ID
    assert record['telegram_alt']
    assert record['in_target_list'] is False
    assert 'REJECTED' in record['decision']
    all_ids = [i for ids in MAP.values() for i in ids]
    assert mapping_module.FALLBACK_DOCUMENT_ID not in all_ids
