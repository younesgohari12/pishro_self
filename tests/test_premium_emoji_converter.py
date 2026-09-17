"""Acceptance tests for the Premium Emoji Converter (offline, real Telethon).

Only the network boundary is simulated; no login, exported session or live
Telegram send is used or claimed by these tests.
"""
import asyncio
import copy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from telethon import TelegramClient, errors, functions, types
from telethon.sessions import MemorySession

import config
import db
import premium_emoji_mapping as mapping_module
import inline as inline_panel
from services import custom_emoji_service as custom
from services import premium_emoji_converter as mod
from services import premium_report as report

# شناسه ثابت قدیمی فقط به‌عنوان سنتینل ممنوع در تست‌ها استفاده می‌شود.
BANNED_FALLBACK_ID = 5938388342281343001
DOC = BANNED_FALLBACK_ID
PEER = types.InputPeerUser(123, 456)
NOW = datetime(2026, 9, 16, tzinfo=timezone.utc)
MEDIA = types.InputMediaPhoto(types.InputPhoto(100, 200, b'reference'))
# نگاشت فعلی — هر ایموجی فقط شناسه‌های alt-تأییدشدهٔ خودش را دارد.
LAUGH = mapping_module.PREMIUM_EMOJI_MAP['😂'][0]
FIRE = mapping_module.PREMIUM_EMOJI_MAP['🔥'][0]
HEART = mapping_module.PREMIUM_EMOJI_MAP['❤️'][0]
HEART2 = mapping_module.PREMIUM_EMOJI_MAP['❤️'][1]
CROWN = mapping_module.PREMIUM_EMOJI_MAP['👑'][0]
GEM = mapping_module.PREMIUM_EMOJI_MAP['💎'][0]


def run(coro):
    return asyncio.run(coro)


class OfflineClient(TelegramClient):
    def __init__(self, *, bot=False, install=True, premium=True, enabled_flag=None):
        super().__init__(MemorySession(), 12345, 'offline-test-only')
        self._mb_entity_cache.extend([types.User(123, access_hash=456)], [])
        self.calls = []
        self.committed = []
        self.fail = None
        self.account = NS(bot=bot, premium=premium)
        if install:
            self.engine = mod.install_premium_emoji_converter(
                self, account=self.account, is_enabled=enabled_flag)

    async def _call(self, sender, request, ordered=False, flood_sleep_threshold=None):
        bytes(request)  # real TL serialization
        self.calls.append(copy.deepcopy(request))
        if self.fail:
            exc = self.fail(request, len(self.calls))
            if exc:
                raise exc
        if isinstance(request, functions.messages.ForwardMessagesRequest):
            self.committed.append(request)
            return types.Updates([], [], [], NOW, 1)
        if isinstance(request, functions.messages.SendMessageRequest):
            self.committed.append(request)
            return types.UpdateShortSentMessage(len(self.committed), 1, 1, NOW,
                                                out=True, entities=request.entities)
        if isinstance(request, functions.messages.SendMultiMediaRequest):
            bodies = request.multi_media
        elif isinstance(request, (functions.messages.SendMediaRequest,
                                  functions.messages.EditMessageRequest)):
            bodies = [request]
        else:
            raise AssertionError('Unexpected network request: ' + type(request).__name__)
        updates = []
        for body in bodies:
            self.committed.append(body)
            mid = (request.id if isinstance(request, functions.messages.EditMessageRequest)
                   else len(self.committed))
            message = types.Message(mid, types.PeerUser(123), date=NOW, out=True,
                                    message=body.message, entities=body.entities)
            if isinstance(request, functions.messages.EditMessageRequest):
                updates.append(types.UpdateEditMessage(message, 1, 1))
            else:
                updates.extend([types.UpdateMessageID(mid, body.random_id),
                                types.UpdateNewMessage(message, 1, 1)])
        return types.Updates(updates, [], [], NOW, 1)


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_CONVERTER_ENABLED', True)
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_MODE', 'round_robin')


def custom_entities(message):
    return [e for e in getattr(message, 'entities', None) or ()
            if isinstance(e, types.MessageEntityCustomEmoji)]


# ----------------------------------------------------------------- mapping
def test_checked_emoji_mapping_complete_and_valid():
    """نسخه Strict: ۳۱ ایموجی بررسی‌شده؛ کلید بدون تأیید → لیست خالی (غیرفعال)."""
    assert set(mapping_module.PREMIUM_EMOJI_MAP) == set(mapping_module.CHECKED_EMOJIS)
    assert len(mapping_module.PREMIUM_EMOJI_MAP) == 31
    for emoji, ids in mapping_module.PREMIUM_EMOJI_MAP.items():
        assert isinstance(ids, list), emoji
        for document_id in ids:
            assert custom.parse_document_id(document_id) == document_id
    assert mapping_module.EMOJI_SOURCE == 'https://t.me/CustomEmojiPack'


def test_verified_ids_present_and_no_fallback_in_map():
    """شناسه‌های تأییدشدهٔ مالک سر جای خود هستند و fallback عمومی در نگاشت نیست."""
    assert mapping_module.PREMIUM_EMOJI_MAP['❤️'][:2] == [6028444945960932052,
                                                          6028138319655736001]
    assert mapping_module.PREMIUM_EMOJI_MAP['👑'][0] == 6014729749585206092
    assert mapping_module.PREMIUM_EMOJI_MAP['✨'][0] == 6028070583726510691
    assert mapping_module.PREMIUM_EMOJI_MAP['😘'][0] == 6030599524894905379
    assert mapping_module.PREMIUM_EMOJI_MAP['😭'][0] == 6028293496824141193
    all_ids = [i for ids in mapping_module.PREMIUM_EMOJI_MAP.values() for i in ids]
    assert DOC not in all_ids  # fallback هرگز به ایموجیِ دیگر منصوب نمی‌شود


# --------------------------------------------------- pure conversion core
def test_self_message_converts_emoji_text_and_formatting_intact():
    async def scenario():
        client = OfflineClient()
        sent = await client.send_message(PEER, 'سلام 😂🔥❤️', parse_mode=None)
        assert sent.message == 'سلام 😂🔥❤️'  # متن اصلی حفظ می‌شود
        entities = custom_entities(sent)
        assert len(entities) == 3
        # هر ایموجی دقیقاً شناسهٔ alt-تأییدشدهٔ خودش را می‌گیرد (تطبیق معنایی).
        by_offset = {e.offset: e for e in entities}
        assert by_offset[custom.utf16_length('سلام ')].document_id == LAUGH    # 😂
        assert by_offset[custom.utf16_length('سلام 😂')].document_id == FIRE   # 🔥
        assert (by_offset[custom.utf16_length('سلام 😂🔥')].document_id
                == HEART)                                                      # ❤️
        assert len(client.committed) == 1
    run(scenario())


def test_entity_creation_is_real_message_entity_custom_emoji():
    engine = mod.PremiumEmojiConverter()
    text, entities = engine.convert('عالی شد 👑💎')
    assert text == 'عالی شد 👑💎'
    assert all(isinstance(e, types.MessageEntityCustomEmoji) for e in entities)
    assert [e.document_id for e in entities] == [CROWN, GEM]
    assert all(e.length == custom.utf16_length('👑') for e in entities[:1])


def test_utf16_offsets_with_astral_text():
    engine = mod.PremiumEmojiConverter()
    prefix = '🇮🇷🚀 خانواده 👨‍👩‍👧 سلام '
    source = prefix + '😂'
    text, entities = engine.convert(source, [])
    assert text == source  # متن هرگز تغییر نمی‌کند
    target = custom.utf16_length(prefix)  # آفست UTF-16 واقعی تلگرام
    entity = [e for e in entities if e.offset == target]
    assert len(entity) == 1 and entity[0].length == custom.utf16_length('😂') == 2
    # 🚀 در نگاشت Strict تأییدشده نیست: دست‌نخورده می‌ماند (هیچ entityای).
    rocket = [e for e in entities
              if e.offset == custom.utf16_length('🇮🇷')]
    assert not rocket  # بدون fallback عمومی؛ ایموجی اصلی حفظ شد
    # Every offset lands on a UTF-16 boundary of the final text.
    raw = text.encode('utf-16-le')
    for e in entities:
        assert (e.offset + e.length) * 2 <= len(raw)


def test_round_robin_rotates_verified_pair():
    engine = mod.PremiumEmojiConverter()
    hearts = mapping_module.PREMIUM_EMOJI_MAP['❤️']
    picks = [engine.convert('❤️')[1][0].document_id for _ in range(4)]
    assert picks == [hearts[0], hearts[1], hearts[2], hearts[3]]


def test_invalid_document_id_entries_never_convert_in_strict_mode():
    """Strict: ورودی نامعتبر/خالی → هیچ تبدیلی؛ fallback عمومی ممنوع است."""
    engine = mod.PremiumEmojiConverter(
        mapping={'😂': ['garbage', 0, -5, None, 2**63]}, strict=True)
    text, entities = engine.convert('😂')
    assert text == '😂' and entities == []
    engine_empty = mod.PremiumEmojiConverter(mapping={'😂': []}, strict=True)
    text, entities = engine_empty.convert('😂')
    assert text == '😂' and entities == []


def test_strict_false_never_uses_fallback_anymore():
    """از DEBUG_FINAL به بعد fallback عمومی حذف شده؛ strict=False هم همان
    رفتار Strict را دارد: استخر خالی یعنی ایموجی دست‌نخورده."""
    engine = mod.PremiumEmojiConverter(mapping={'😂': ['garbage']}, strict=False)
    assert engine.convert('😂') == ('😂', [])
    engine_empty = mod.PremiumEmojiConverter(mapping={'😂': []}, strict=False)
    assert engine_empty.convert('😂') == ('😂', [])


def test_duplicate_prevention_is_idempotent():
    engine = mod.PremiumEmojiConverter()
    once_text, once_entities = engine.convert('Premium 😂', [])
    twice_text, twice_entities = engine.convert(once_text, once_entities)
    assert once_text == twice_text
    assert len(once_entities) == len(twice_entities) == 1


def test_existing_custom_emoji_is_never_reconverted():
    engine = mod.PremiumEmojiConverter()
    offset = custom.utf16_length('پیش ')
    entities = [types.MessageEntityCustomEmoji(offset, custom.utf16_length('😂'), DOC + 77)]
    text, result = engine.convert('پیش 😂', entities)
    assert text == 'پیش 😂'
    assert [e.document_id for e in result] == [DOC + 77]


def test_code_pre_url_and_literals_are_protected():
    engine = mod.PremiumEmojiConverter()
    text = 'کد `😂` بلوک ```\n🔥\n``` لینک https://t.me/😂'
    _, entities = engine.convert(text, [])
    assert not any(isinstance(e, types.MessageEntityCustomEmoji) for e in entities)
    code_offset = custom.utf16_length('ایموجی ')
    covered = [types.MessageEntityCode(code_offset, custom.utf16_length('😂'))]
    _, result = engine.convert('ایموجی 😂 داخل کد', covered)
    assert not any(isinstance(e, types.MessageEntityCustomEmoji) for e in result)


def test_unmapped_emoji_and_skin_tone_stay_unicode():
    engine = mod.PremiumEmojiConverter()
    text, entities = engine.convert('🤍 👍🏽 🗿 😂', [])
    assert text == '🤍 👍🏽 🗿 😂'
    offsets = [e.offset for e in entities]
    assert offsets == [custom.utf16_length('🤍 👍🏽 🗿 ')]


def test_empty_and_oversize_inputs_pass_through():
    engine = mod.PremiumEmojiConverter()
    assert engine.convert('', []) == ('', [])
    assert engine.convert(None, None) == (None, [])
    long_text = 'سلام ' * 4000 + '😂'
    text, entities = engine.convert(long_text, [])
    assert text == long_text and entities == []


@pytest.mark.parametrize('mode,text,plain', [
    ('md', '**bold** 😂', 'bold 😂'),
    ('html', '<b>bold</b> 😂', 'bold 😂'),
    ('md', '__italic__ 😂', 'italic 😂'),
    ('html', '<s>strike</s> <u>under</u> 😂', 'strike under 😂'),
])
def test_markdown_and_html_formatting_survives(mode, text, plain):
    async def scenario():
        sent = await OfflineClient().send_message(PEER, text, parse_mode=mode)
        assert sent.message == plain
        formatting = [e for e in sent.entities
                      if not isinstance(e, types.MessageEntityCustomEmoji)]
        assert formatting, 'formatting entity lost'
        customs = custom_entities(sent)
        assert len(customs) == 1 and customs[0].document_id == LAUGH
        # Custom entity must sit exactly on the emoji, after the formatted span.
        assert customs[0].offset == custom.utf16_length(plain) - 2
    run(scenario())


# ------------------------------------------------------- send-path wrappers
@pytest.mark.parametrize('via_send_message', [False, True])
def test_caption_and_media_converted(via_send_message):
    async def scenario():
        client = OfflineClient()
        if via_send_message:
            sent = await client.send_message(PEER, 'عکس جدید 🔥', file=MEDIA, parse_mode=None)
        else:
            sent = await client.send_file(PEER, MEDIA, caption='عکس جدید 🔥', parse_mode=None)
        assert sent.message == 'عکس جدید 🔥'
        assert len(custom_entities(sent)) == 1
        assert len(client.calls) == 1 and len(client.committed) == 1
    run(scenario())


@pytest.mark.parametrize('caption', [None, ''])
def test_media_without_caption_sends_no_extra_message(caption):
    async def scenario():
        client = OfflineClient()
        sent = await client.send_file(PEER, MEDIA, caption=caption)
        assert sent.message == '' and not sent.entities
        # فقط خود آپلود رسانه؛ هیچ پیام جدیدی برای کپشن تولید نشده است.
        assert len(client.calls) == 1 and len(client.committed) == 1
    run(scenario())


def test_album_captions_convert_independently():
    async def scenario():
        client = OfflineClient()
        sent = await client.send_file(PEER, [MEDIA] * 3,
                                      caption=['اول 😂', '', '🔥 سوم'], parse_mode=None)
        assert sent[0].message == 'اول 😂' and len(custom_entities(sent[0])) == 1
        assert sent[1].message == '' and not sent[1].entities
        assert sent[2].message == '🔥 سوم' and len(custom_entities(sent[2])) == 1
        assert len(client.calls) == 1 and len(client.committed) == 3
    run(scenario())


def test_edit_message_converts_and_reply_respond_covered():
    async def scenario():
        client = OfflineClient()
        edited = await client.edit_message(PEER, 17, 'متن جدید 🔥', parse_mode=None)
        assert edited.message == 'متن جدید 🔥' and len(custom_entities(edited)) == 1
        message = types.Message(11, types.PeerUser(123), date=NOW, message='input', out=True)
        message._finish_init(client, {}, PEER)
        for method in ('reply', 'respond'):
            sent = await getattr(message, method)('پاسخ خودکار 🤝')
            assert sent.message == 'پاسخ خودکار 🤝' and len(custom_entities(sent)) == 1
    run(scenario())


def test_bot_messages_untouched_even_if_install_attempted():
    async def scenario():
        client = OfflineClient(bot=True, install=True)
        original = 'یک document_id عددی معتبر وارد کنید 😂'
        sent = await client.send_message(PEER, original, parse_mode=None)
        assert sent.message == original and not custom_entities(sent)
        assert not hasattr(client, '_premium_emoji_converter')
    run(scenario())


def test_forward_is_untouched():
    async def scenario():
        client = OfflineClient()
        before = client.forward_messages
        await client.forward_messages(PEER, [17], from_peer=PEER)
        assert client.forward_messages == before
        assert isinstance(client.calls[0], functions.messages.ForwardMessagesRequest)
        assert client.calls[0].id == [17]
        assert not any(hasattr(r, 'entities') and custom_entities(r)
                       for r in client.committed)
    run(scenario())


@pytest.mark.parametrize('error', [errors.PremiumAccountRequiredError,
                                  errors.EmoticonInvalidError])
def test_emoji_rejection_falls_back_once_without_duplicate(error):
    async def scenario():
        client = OfflineClient()
        client.fail = lambda request, n: error(request) if n == 1 else None
        sent = await client.send_message(PEER, 'سلام 😂🔥', parse_mode=None)
        assert sent.message == 'سلام 😂🔥'
        assert not custom_entities(sent)
        assert len(client.calls) == 2 and len(client.committed) == 1
    run(scenario())


def test_album_rejection_is_not_replayed():
    async def scenario():
        client = OfflineClient()
        client.fail = lambda r, n: errors.DocumentInvalidError(r) if n == 1 else None
        with pytest.raises(errors.DocumentInvalidError):
            await client.send_file(PEER, [MEDIA] * 3, caption=['😂', '', ''])
        assert len(client.calls) == 1 and not client.committed
    run(scenario())


def test_unrelated_error_never_retried_and_cooldown_disables_engine():
    async def scenario():
        client = OfflineClient()
        client.fail = lambda r, n: TimeoutError()
        with pytest.raises(TimeoutError):
            await client.send_message(PEER, 'سلام 😂')
        assert len(client.calls) == 1 and not client.committed
        # A confirmed rejection disables conversion for a cool-down window.
        client.fail = None
        client.engine.disabled_until = __import__('time').monotonic() + 300
        sent = await client.send_message(PEER, 'سلام 😂')
        assert sent.message == 'سلام 😂' and not custom_entities(sent)
    run(scenario())


# ------------------------------------------------------- enable/disable logic
def test_user_toggle_overrides_config_default():
    off_default = mod.PremiumEmojiConverter(is_enabled=lambda: None)
    monkey_default = config.PREMIUM_EMOJI_CONVERTER_ENABLED
    assert off_default.effective_enabled() == bool(monkey_default)
    forced_on = mod.PremiumEmojiConverter(is_enabled=lambda: True)
    assert forced_on.effective_enabled() is True
    forced_off = mod.PremiumEmojiConverter(is_enabled=lambda: False)
    assert forced_off.effective_enabled() is False


def test_disabled_engine_sends_unicode_without_entities():
    async def scenario():
        client = OfflineClient(enabled_flag=lambda: False)
        sent = await client.send_message(PEER, 'سلام 😂', parse_mode=None)
        assert sent.message == 'سلام 😂' and not custom_entities(sent)
        assert len(client.calls) == 1 and len(client.committed) == 1
    run(scenario())


def test_install_is_per_client_idempotent_and_uninstallable():
    async def scenario():
        client, other = OfflineClient(), OfflineClient(install=False)
        wrapped = client.send_message
        assert mod.install_premium_emoji_converter(
            client, account=client.account) is client.engine
        assert client.send_message is wrapped
        assert (await other.send_message(PEER, 'plain 😂')).message == 'plain 😂'
        mod.uninstall_premium_emoji_converter(client)
        assert (await client.send_message(PEER, 'بدون تبدیل 😂')).message == 'بدون تبدیل 😂'
    run(scenario())


def test_runtime_installs_converter_only_on_self_client():
    root = Path(__file__).resolve().parents[1]
    source = (root / 'self.py').read_text()
    assert 'install_premium_emoji_converter(client, account=me' in source
    assert 'uninstall_premium_emoji_converter(client)' in source
    # Bot runtime never imports or installs the converter.
    for name in ('bot/core.py', 'bot/handlers/__init__.py', 'self_panel_bot.py'):
        path = root / name
        if path.exists():
            assert 'premium_emoji_converter' not in path.read_text(), name
    # The inline panel only reads/writes the per-account toggle; never installs.
    inline_source = (root / 'inline.py').read_text()
    assert 'install_premium_emoji_converter' not in inline_source
    assert "get('premium_emoji_converter')" in inline_source


# ------------------------------------------------------------ panel button
def test_panel_has_premium_emoji_toggle_button():
    uid = 4242
    db.update_user_settings(uid, {'premium_emoji_converter': False})
    text, buttons = inline_panel.build_main_menu(uid, 'panel_bot')
    flat = [btn for row in buttons for btn in row]
    toggle = [b for b in flat if getattr(b, 'data', b'') == b'peconv_toggle']
    assert len(toggle) == 1
    assert 'Premium Emoji' in toggle[0].text and 'خاموش' in toggle[0].text
    db.update_user_settings(uid, {'premium_emoji_converter': True})
    _, buttons_on = inline_panel.build_main_menu(uid, 'panel_bot')
    toggle_on = [b for row in buttons_on for b in row
                 if getattr(b, 'data', b'') == b'peconv_toggle'][0]
    assert 'روشن' in toggle_on.text


def test_converter_setting_persists_in_database():
    uid = 515151
    assert db.get_user_settings(uid).get('premium_emoji_converter') is None
    db.update_user_settings(uid, {'premium_emoji_converter': True})
    assert db.get_user_settings(uid)['premium_emoji_converter'] is True
    db.update_user_settings(uid, {'premium_emoji_converter': False})
    assert db.get_user_settings(uid)['premium_emoji_converter'] is False
    # None هرگز به False خراب نمی‌شود؛ نرمال‌سازی مقدار حفظ‌شده را نگه می‌دارد.
    stored = db._get_internal(str(uid))
    assert stored['premium_emoji_converter'] is False


# ------------------------------------------------------------ config keys
def test_release_config_keys_and_defaults():
    # Release defaults come from import-time _USER_DEFAULTS (monkeypatch-proof).
    # Production-Safe: پیش‌فرض انتشار کانورتر روشن است؛ انتخاب پنل حساب اولویت دارد.
    assert config._USER_DEFAULTS['PREMIUM_EMOJI_CONVERTER_ENABLED'] is True
    assert config._USER_DEFAULTS['PREMIUM_EMOJI_MODE'] in mapping_module.PREMIUM_EMOJI_MODES
    updates = config.premium_converter_config_updates()
    assert set(updates) == {'PREMIUM_EMOJI_CONVERTER_ENABLED', 'PREMIUM_EMOJI_MODE'}
    assert updates['PREMIUM_EMOJI_CONVERTER_ENABLED'] is True
    assert 'PREMIUM_EMOJI_CONVERTER_ENABLED' in config._USER_CONFIG_KEYS
    source = (Path(__file__).resolve().parents[1] / 'config.py').read_text()
    assert 'PREMIUM_EMOJI_CONVERTER_ENABLED = True' in source
    # Release update marker جدید در main.py: نصب‌های موجود False انتشار قبل → True
    main_source = (Path(__file__).resolve().parents[1] / 'main.py').read_text()
    assert 'v0.09.13-premium-emoji-converter-default-on' in main_source


def test_strict_mode_config_default_and_engine_reading():
    # قانون نسخه Strict: پیش‌فرض انتشار روشن است و موتور آن را می‌خواند.
    assert config.PREMIUM_EMOJI_STRICT_MODE is True
    source = (Path(__file__).resolve().parents[1] / 'config.py').read_text()
    assert 'PREMIUM_EMOJI_STRICT_MODE = True' in source
    assert mod.PremiumEmojiConverter().strict is True
    assert mod.PremiumEmojiConverter(strict=False).strict is False


# -------------------------------------------------------- admin report tool
def test_admin_report_is_env_only_and_noops_without_token(monkeypatch):
    import services.premium_report as pr
    monkeypatch.delenv('PREMIUM_REPORT_BOT_TOKEN', raising=False)
    assert report.report_enabled() is False
    # بدون توکن: هیچ درخواست شبکه‌ای زده نمی‌شود و False برمی‌گردد.
    assert run(pr.send_report_text('تست')) is False
    assert run(pr.send_report_document('/nonexistent/file.zip')) is False
    monkeypatch.setenv('PREMIUM_REPORT_BOT_TOKEN', '123:abc')
    assert report.report_enabled() is True
    assert report.ADMIN_REPORT_ID == 8359698350
    # مقدار توکن واقعی هرگز داخل کد پروژه ذخیره نشده است (فقط نام ENV).
    # این تست بدون نگه‌داشتن خود توکن کار می‌کند: هر رشته‌ای با شکل
    # «<bot_id>:<token>» تلگرام (الگوی عمومی توکن) داخل سورس غیرتستی ممنوع است.
    import re
    root = Path(__file__).resolve().parents[1]
    token_shape = re.compile(r'\b\d{8,10}:AA[A-Za-z0-9_\-]{25,}')
    for path in root.rglob('*.py'):
        if 'tests' in path.parts:
            continue
        assert not token_shape.search(path.read_text()), \
            f'{path} contains a hardcoded bot token'
