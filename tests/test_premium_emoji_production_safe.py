"""Production-Safe enabling matrix for the Premium Emoji Converter (offline).

Verified truth table (real code path, no network):
  1) PREMIUM_EMOJI_ENABLED=False (master)  -> converter OFF, outgoing fix OFF.
  2) Panel choice (db) True/False          -> always wins over config default.
  3) Panel untouched (None)                -> follows PREMIUM_EMOJI_CONVERTER_ENABLED.
  4) Existing installs with the legacy persisted False are flipped once by the
     marker-guarded release update (new update id in main.py).
  5) Panel display (inline.premium_converter_effective) matches the engine.
  6) Every self-client send path stays routed through the unified pipeline.
"""
import asyncio
import copy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from telethon import TelegramClient, functions, types
from telethon.sessions import MemorySession

import config
import db
import premium_emoji_mapping as mapping_module
import inline as inline_panel
import storage
from services import premium_emoji_converter as mod

PEER = types.InputPeerUser(123, 456)
NOW = datetime(2026, 9, 18, tzinfo=timezone.utc)
FIRE = mapping_module.PREMIUM_EMOJI_MAP['🔥'][0]


def run(coro):
    return asyncio.run(coro)


class OfflineClient(TelegramClient):
    def __init__(self, *, enabled_flag=None):
        super().__init__(MemorySession(), 12345, 'offline-test-only')
        self._mb_entity_cache.extend([types.User(123, access_hash=456)], [])
        self.calls = []
        self.account = NS(bot=False, premium=True)
        self.engine = mod.install_premium_emoji_converter(
            self, account=self.account, is_enabled=enabled_flag)

    async def _call(self, sender, request, ordered=False, flood_sleep_threshold=None):
        bytes(request)  # real TL serialization
        self.calls.append(copy.deepcopy(request))
        if isinstance(request, functions.messages.SendMessageRequest):
            return types.UpdateShortSentMessage(len(self.calls), 1, 1, NOW,
                                                out=True, entities=request.entities)
        raise AssertionError('Unexpected network request: ' + type(request).__name__)


def custom_entities(message):
    return [e for e in getattr(message, 'entities', None) or ()
            if isinstance(e, types.MessageEntityCustomEmoji)]


# ------------------------------------------------------- truth table (engine)
def test_master_flag_off_disables_converter_even_when_panel_on(monkeypatch):
    """کلید اصلی خاموش → هیچ ترکیبی از پنل/config کانورتر را روشن نمی‌کند."""
    async def scenario():
        monkeypatch.setattr(config, 'PREMIUM_EMOJI_ENABLED', False)
        client = OfflineClient(enabled_flag=lambda: True)  # پنل صریحاً روشن
        assert client.engine.effective_enabled() is False
        sent = await client.send_message(PEER, 'سلام 🔥', parse_mode=None)
        assert sent.message == 'سلام 🔥' and not custom_entities(sent)
    run(scenario())


def test_master_flag_on_and_panel_untouched_follows_config_default(monkeypatch):
    async def scenario():
        monkeypatch.setattr(config, 'PREMIUM_EMOJI_ENABLED', True)
        monkeypatch.setattr(config, 'PREMIUM_EMOJI_CONVERTER_ENABLED', True)
        client = OfflineClient(enabled_flag=lambda: None)  # پنل لمس‌نشده
        assert client.engine.effective_enabled() is True
        sent = await client.send_message(PEER, 'سلام 🔥', parse_mode=None)
        assert custom_entities(sent)[0].document_id == FIRE
    run(scenario())


def test_panel_explicit_off_wins_over_config_default(monkeypatch):
    async def scenario():
        monkeypatch.setattr(config, 'PREMIUM_EMOJI_ENABLED', True)
        monkeypatch.setattr(config, 'PREMIUM_EMOJI_CONVERTER_ENABLED', True)
        client = OfflineClient(enabled_flag=lambda: False)  # کاربر پنل را خاموش کرده
        assert client.engine.effective_enabled() is False
        sent = await client.send_message(PEER, 'سلام 🔥', parse_mode=None)
        assert sent.message == 'سلام 🔥' and not custom_entities(sent)
    run(scenario())


def test_panel_explicit_on_wins_over_config_default(monkeypatch):
    async def scenario():
        monkeypatch.setattr(config, 'PREMIUM_EMOJI_ENABLED', True)
        monkeypatch.setattr(config, 'PREMIUM_EMOJI_CONVERTER_ENABLED', False)
        client = OfflineClient(enabled_flag=lambda: True)
        assert client.engine.effective_enabled() is True
        sent = await client.send_message(PEER, 'سلام 🔥', parse_mode=None)
        assert custom_entities(sent)[0].document_id == FIRE
    run(scenario())


def test_cooldown_still_disables_engine_regardless_of_flags(monkeypatch):
    """cooldown رد تلگرامی همیشه مقدم است؛ production-safe یعنی نه اسپم نه خطا."""
    async def scenario():
        monkeypatch.setattr(config, 'PREMIUM_EMOJI_ENABLED', True)
        client = OfflineClient(enabled_flag=lambda: True)
        client.engine.disabled_until = __import__('time').monotonic() + 300
        assert client.engine.effective_enabled() is False
    run(scenario())


# ------------------------------------------------- panel display consistency
def test_panel_display_matches_engine_truth_table(monkeypatch):
    uid = 616161
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_ENABLED', True)
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_CONVERTER_ENABLED', True)
    # لمس‌نشده → پیش‌فرض config
    db.update_user_settings(uid, {'premium_emoji_converter': None})
    assert inline_panel.premium_converter_effective(uid) is True
    # خاموش صریح پنل → خاموش
    db.update_user_settings(uid, {'premium_emoji_converter': False})
    assert inline_panel.premium_converter_effective(uid) is False
    # روشن صریح پنل → روشن
    db.update_user_settings(uid, {'premium_emoji_converter': True})
    assert inline_panel.premium_converter_effective(uid) is True
    # کلید اصلی خاموش → پنل هم خاموش را نشان می‌دهد
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_ENABLED', False)
    assert inline_panel.premium_converter_effective(uid) is False
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_ENABLED', True)
    # دکمه پنل متن و رنگ درست را نشان می‌دهد
    _, buttons = inline_panel.build_main_menu(uid, 'panel_bot')
    toggle = [b for row in buttons for b in row
              if getattr(b, 'data', b'') == b'peconv_toggle'][0]
    assert 'روشن' in toggle.text


def test_panel_toggle_writes_explicit_choice(monkeypatch):
    """تاگل پنل همیشه انتخاب صریح True/False می‌نویسد (نه None)."""
    uid = 626262
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_ENABLED', True)
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_CONVERTER_ENABLED', True)
    db.update_user_settings(uid, {'premium_emoji_converter': None})
    effective = inline_panel.premium_converter_effective(uid)
    db.update_user_settings(uid, {'premium_emoji_converter': not effective})
    assert db.get_user_settings(uid).get('premium_emoji_converter') is False
    effective = inline_panel.premium_converter_effective(uid)
    db.update_user_settings(uid, {'premium_emoji_converter': not effective})
    assert db.get_user_settings(uid).get('premium_emoji_converter') is True


# ------------------------------------------- release update for legacy installs
def test_release_update_flips_legacy_false_to_true_once():
    """نصب موجود با config.py پایدار (False انتشار قبل) یک‌باره روشن می‌شود."""
    import tempfile
    with tempfile.TemporaryDirectory() as root:
        path = Path(root) / 'config.py'
        path.write_text(
            "PREMIUM_EMOJI_CONVERTER_ENABLED = False\n"
            "PREMIUM_EMOJI_MODE = 'round_robin'\n"
            "ADMIN_ID = 111\n"
            "_APPLIED_RELEASE_UPDATES = ['v0.09.13-premium-emoji-converter']\n",
            encoding='utf-8')
        patch = config.premium_converter_config_updates()
        assert storage.apply_config_update(root, 'v0.09.13-premium-emoji-converter-default-on', patch)
        tree = {}
        import ast
        for node in ast.parse(path.read_text(encoding='utf-8')).body:
            if isinstance(node, ast.Assign):
                tree[node.targets[0].id] = ast.literal_eval(node.value)
        assert tree['PREMIUM_EMOJI_CONVERTER_ENABLED'] is True
        assert tree['ADMIN_ID'] == 111  # بقیه تنظیمات دست‌نخورده
        assert 'v0.09.13-premium-emoji-converter-default-on' in tree['_APPLIED_RELEASE_UPDATES']
        # بار دوم: marker جلوی اعمال مجدد را می‌گیرد
        assert not storage.apply_config_update(
            root, 'v0.09.13-premium-emoji-converter-default-on', patch)


# ------------------------------------------------------- unified pipeline paths
def test_all_self_client_send_paths_routed_through_pipeline():
    """هر مسیر ارسال سلف از wrapper کانورتر عبور می‌کند (منبع‌سنجی).

    spec مالک: edit_message عمداً wrap نمی‌شود — در سیستم ایموجی ویژه
    هیچ Edit ای مجاز نیست (ارسال مجدد = حذف + ارسال جدید).
    """
    root = Path(__file__).resolve().parents[1]
    converter = (root / 'services' / 'premium_emoji_converter.py').read_text()
    for method in ('send_message', 'send_file', '_send_album'):
        assert f"('{method}'" in converter, method
    assert "('edit_message'" not in converter  # edit هرگز wrap نمی‌شود
    # فراخوانی واقعی edit ممنوع (اشاره در docstring آزاد است)
    assert 'functions.messages.EditMessageRequest(' not in converter
    assert '.edit_message(' not in converter
    # self.py کانورتر + outgoing fix را روی کلاینت سلف نصب می‌کند
    self_source = (root / 'self.py').read_text()
    assert 'install_premium_emoji_converter(client, account=me' in self_source
    assert 'install_premium_emoji_outgoing_injector(client, engine)' in self_source
    assert 'install_emoji_resend_manager(client, engine' in self_source
    # مسیرهای سلف: این ماژول‌ها client خود سلف را می‌گیرند (نه بات را)
    for name in ('services/sender.py', 'services/copy_protected.py',
                 'services/media_sender.py', 'services/deleted_handler.py',
                 'services/custom_emoji_service.py'):
        source = (root / name).read_text()
        assert 'self.bot' not in source, name  # هرگز کلاینت بات را صدا نمی‌زنند
    # raw API ارسال مستقیم در کد اجرایی وجود ندارد (فقط tests)
    for name in ('self.py', 'services/sender.py', 'services/copy_protected.py',
                 'services/media_sender.py', 'services/deleted_handler.py',
                 'services/scheduler.py', 'handlers/ai.py'):
        source = (root / name).read_text()
        assert 'SendMessageRequest' not in source, name
        assert 'SendMediaRequest' not in source, name


def test_outgoing_fix_respects_master_and_panel(monkeypatch):
    """فیکس پس از ارسال (پیام‌های گوشی) هم همان جدول production-safe را دارد."""
    async def scenario():
        monkeypatch.setattr(config, 'PREMIUM_EMOJI_ENABLED', True)
        monkeypatch.setattr(config, 'PREMIUM_EMOJI_OUTGOING_FIX', True)
        client = OfflineClient(enabled_flag=lambda: False)  # پنل خاموش
        assert client.engine.effective_enabled() is False
        monkeypatch.setattr(config, 'PREMIUM_EMOJI_ENABLED', False)
        client2 = OfflineClient(enabled_flag=lambda: True)
        assert client2.engine.effective_enabled() is False
    run(scenario())
