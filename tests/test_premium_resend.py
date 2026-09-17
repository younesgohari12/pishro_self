"""Acceptance tests — Premium Resend Mode (Copy/Delete/Resend) v0.09.13.

سناریوهای الزامی spec مالک، همه آفلاین و بدون شبکه (قرارداد AGENTS.md):
    1) متن ساده      2) Reply + Entities       3) عکس/مدیا + کپشن
    4) آلبوم         5) حفظ پیام اصلی در شکست   6) ضد حلقه / cooldown
    7) خاموشی با پنل/config                    8) اولویت Resend بر edit

قاعده مالک: پیام اصلی فقط بعد از ارسال موفق نسخه جدید حذف می‌شود؛ اگر
ارسال مجدد یا حذف شکست بخورد، پیام اصلی دست‌نخورده می‌ماند.
"""
import asyncio
import copy
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
from telethon import TelegramClient, functions, types, utils
from telethon.sessions import MemorySession

import config
import premium_emoji_mapping as mapping_module
from services import premium_emoji_converter as mod
from services import premium_resend as rmod
from services import telegram_logger as tlog

NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)
FIRE = mapping_module.PREMIUM_EMOJI_MAP['🔥'][0]
LAUGH = mapping_module.PREMIUM_EMOJI_MAP['😂'][0]
CHAT_ID = 555
GROUP_ID = -777


def run(coro):
    return asyncio.run(coro)


def make_engine(**kwargs):
    # نگاشت داخلی صریح → نتایج قطعی (بدون وابستگی به ترتیب emoji_map.json)
    kwargs.setdefault('mapping', dict(mapping_module.PREMIUM_EMOJI_MAP))
    return mod.PremiumEmojiConverter(premium=True,
                                     is_enabled=kwargs.pop('is_enabled', lambda: True),
                                     **kwargs)


class FakeClient:
    """کلاینت تقلبی: فقط مرز شبکه شبیه‌سازی می‌شود؛ منطق ما واقعی اجرا می‌شود."""

    def __init__(self):
        self.sends = []    # ('message'/'file', entity, text, kwargs)
        self.deleted = []
        self.send_error = None
        self.delete_error = None
        self.entities = {}

    async def get_input_entity(self, chat_id):
        return types.InputPeerUser(chat_id, 1)

    async def send_message(self, entity, message, **kwargs):
        if self.send_error:
            raise self.send_error
        self.sends.append(('message', entity, message, copy.deepcopy(kwargs)))
        return NS(id=900 + len(self.sends), chat_id=getattr(entity, 'user_id', CHAT_ID),
                  entities=kwargs.get('formatting_entities'))

    async def send_file(self, entity, file, caption=None, **kwargs):
        if self.send_error:
            raise self.send_error
        self.sends.append(('file', entity, caption, copy.deepcopy(kwargs)))
        return NS(id=900 + len(self.sends), chat_id=getattr(entity, 'user_id', CHAT_ID),
                  entities=kwargs.get('formatting_entities'))

    async def delete_messages(self, entity, message_ids, **kwargs):
        if self.delete_error:
            raise self.delete_error
        if not isinstance(message_ids, (list, tuple)):
            message_ids = [message_ids]
        self.deleted.append((entity, list(message_ids)))


def make_manager(*, is_enabled=lambda: None, engine=None, client=None):
    engine = engine or make_engine()
    client = client or FakeClient()
    manager = rmod.PremiumResendManager(client, engine, is_enabled=is_enabled)
    return manager, client, engine


def make_message(text='🔥 سلام', *, msg_id=10, chat_id=CHAT_ID, entities=None,
                 media=None, grouped_id=None, reply_to=None, fwd=False,
                 via_bot=False, action=None, silent=False):
    reply = NS(reply_to_msg_id=reply_to) if reply_to else None
    return NS(id=msg_id, chat_id=chat_id, message=text,
              entities=list(entities or []), media=media, grouped_id=grouped_id,
              fwd_from='fwd' if fwd else None, via_bot_id=99 if via_bot else None,
              action=NS() if action else None, reply_to=reply, silent=silent,
              noforwards=False, input_chat=None, out=True)


def custom_ids(entities):
    return {e.document_id for e in (entities or [])
            if isinstance(e, types.MessageEntityCustomEmoji)}


# ================================================== gating
def test_resend_disabled_when_config_hard_off(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', False)
    manager, _, _ = make_manager()
    assert manager.resend_enabled() is False


def test_resend_disabled_when_converter_off(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)
    engine = make_engine(is_enabled=lambda: False)
    manager = rmod.PremiumResendManager(FakeClient(), engine)
    assert manager.resend_enabled() is False


def test_resend_panel_choice_priority(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)
    # None (لمس‌نشده) → پیش‌فرض روشن
    manager, _, _ = make_manager(is_enabled=lambda: None)
    assert manager.resend_enabled() is True
    # پنل صریح خاموش → خاموش
    manager, _, _ = make_manager(is_enabled=lambda: False)
    assert manager.resend_enabled() is False
    # پنل صریح روشن → روشن
    manager, _, _ = make_manager(is_enabled=lambda: True)
    assert manager.resend_enabled() is True


# ================================================== single text
def test_resend_single_text_when_entity_missing(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)
    manager, client, _ = make_manager()
    message = make_message('🔥 سلام', msg_id=10)
    verdict = run(manager.handle_outgoing(message))
    assert verdict == 'handled'
    assert len(client.sends) == 1
    kind, entity, text, kwargs = client.sends[0]
    assert kind == 'message'
    assert text == '🔥 سلام'                       # متن دست‌نخورده
    assert custom_ids(kwargs.get('formatting_entities')) == {FIRE}
    assert kwargs.get('parse_mode') is None
    assert client.deleted == [(CHAT_ID, [10])]     # حذف بعد از ارسال موفق
    assert manager.stats['resent'] == 1
    assert manager.stats['deleted'] == 1


def test_resend_preserves_reply_and_existing_entities(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)
    manager, client, _ = make_manager()
    bold = types.MessageEntityBold(offset=2, length=4)
    message = make_message('🔥 سلام دنیا', msg_id=11, entities=[bold],
                           reply_to=42)
    verdict = run(manager.handle_outgoing(message))
    assert verdict == 'handled'
    kind, entity, text, kwargs = client.sends[0]
    assert text == '🔥 سلام دنیا'
    assert kwargs.get('reply_to') == 42
    fmt = kwargs.get('formatting_entities')
    assert any(isinstance(e, types.MessageEntityBold) for e in fmt)  # فرمت حفظ
    assert custom_ids(fmt) == {FIRE}                 # ایموجی تبدیل شد
    assert client.deleted == [(CHAT_ID, [11])]


def test_resend_media_caption_uses_send_file(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)
    manager, client, _ = make_manager()
    media = NS(media_key='photo-ref')
    message = make_message('🔥 عکس جدید', msg_id=12, media=media)
    verdict = run(manager.handle_outgoing(message))
    assert verdict == 'handled'
    kind, entity, caption, kwargs = client.sends[0]
    assert kind == 'file'
    assert caption == '🔥 عکس جدید'
    assert custom_ids(kwargs.get('formatting_entities')) == {FIRE}
    assert client.deleted == [(CHAT_ID, [12])]


def test_resend_in_group_and_saved(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)
    manager, client, _ = make_manager()
    # Group
    group_msg = make_message('😂 گروه', msg_id=20, chat_id=GROUP_ID)
    assert run(manager.handle_outgoing(group_msg)) == 'handled'
    # Saved (chat_id = خود حساب)
    saved_msg = make_message('😂 سیو', msg_id=21, chat_id=8359698350)
    assert run(manager.handle_outgoing(saved_msg)) == 'handled'
    assert len(client.sends) == 2
    assert len(client.deleted) == 2


# ================================================== skip cases
def test_skip_when_custom_entity_already_present(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)
    manager, client, _ = make_manager()
    custom = types.MessageEntityCustomEmoji(offset=0, length=2, document_id=FIRE)
    message = make_message('🔥 سلام', entities=[custom])
    assert run(manager.handle_outgoing(message)) == 'skipped'
    assert client.sends == [] and client.deleted == []


def test_skip_when_no_mappable_emoji(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)
    manager, client, _ = make_manager()
    for text in ('سلام بدون ایموجی', '🙂', ''):
        assert run(manager.handle_outgoing(
            make_message(text))) == 'skipped', text
    assert client.sends == []


def test_skip_when_panel_off_or_hard_off(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)
    manager, client, _ = make_manager(is_enabled=lambda: False)
    assert run(manager.handle_outgoing(make_message())) == 'skipped'
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', False)
    assert run(manager.handle_outgoing(make_message())) == 'skipped'
    assert client.sends == []


def test_skip_forward_via_bot_and_action(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)
    manager, client, _ = make_manager()
    assert run(manager.handle_outgoing(make_message(fwd=True))) == 'skipped'
    assert run(manager.handle_outgoing(make_message(via_bot=True))) == 'skipped'
    assert run(manager.handle_outgoing(make_message(action=True))) == 'skipped'
    assert client.sends == []


# ================================================== failure safety
def test_original_kept_when_send_fails(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)
    manager, client, _ = make_manager()
    client.send_error = RuntimeError('network down')
    message = make_message('🔥 مهم', msg_id=30)
    verdict = run(manager.handle_outgoing(message))
    # 'skipped' یعنی injector مسیر edit (رفتار قبلی) را امتحان می‌کند
    assert verdict == 'skipped'
    assert client.deleted == []          # پیام اصلی حذف نشد
    assert manager.stats['failed'] == 1


def test_original_kept_when_delete_fails(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)
    manager, client, _ = make_manager()
    client.delete_error = RuntimeError('no rights')
    message = make_message('🔥 مهم', msg_id=31)
    verdict = run(manager.handle_outgoing(message))
    assert verdict == 'handled'
    assert len(client.sends) == 1        # نسخه جدید ارسال شد
    assert manager.stats['kept'] == 1    # اما پیام اصلی باقی ماند
    assert manager.stats['resent'] == 1


# ================================================== loop guards
def test_no_loop_for_resent_message(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)
    manager, client, _ = make_manager()
    original = make_message('🔥 سلام', msg_id=40)
    assert run(manager.handle_outgoing(original)) == 'handled'
    resent = make_message('🔥 سلام', msg_id=901, entities=[])  # همان نسخه ارسالی
    calls_before = len(client.sends)
    assert run(manager.handle_outgoing(resent)) == 'handled'
    assert len(client.sends) == calls_before          # ارسال دوباره نشد
    assert client.deleted == [(CHAT_ID, [40])]        # حذف جدید هم نداشت


def test_strip_condition_sets_cooldown(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)
    manager, client, engine = make_manager()
    for msg_id in (910, 911):
        stripped = make_message('🔥', msg_id=msg_id)
        manager._mark_recent(CHAT_ID, msg_id)
        assert run(manager.handle_outgoing(stripped)) == 'handled'
    assert engine.disabled_until > 0                  # cooldown فعال شد


def test_cooldown_blocks_resend(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)
    manager, client, engine = make_manager()
    engine.disabled_until = 10 ** 12                  # همیشه در cooldown
    assert run(manager.handle_outgoing(make_message())) == 'skipped'
    assert client.sends == []


# ================================================== album
def test_album_resend_as_one_album(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)
    monkeypatch.setattr(rmod, 'ALBUM_FLUSH_DELAY_SECONDS', 0.05)
    manager, client, _ = make_manager()
    grouped = 'album-1'
    part1 = make_message('🔥 کپشن آلبوم', msg_id=50, media=NS(m='p1'),
                         grouped_id=grouped, reply_to=7)
    part2 = make_message('', msg_id=51, media=NS(m='p2'), grouped_id=grouped)

    async def scenario():
        assert await manager.handle_outgoing(part1) == 'handled'
        assert await manager.handle_outgoing(part2) == 'handled'
        await asyncio.sleep(0.25)   # فلاش داخل همان event loop

    run(scenario())
    assert len(client.sends) == 1                     # یک آلبوم یک‌جا
    kind, entity, captions, kwargs = client.sends[0]
    assert kind == 'file'
    assert captions == ['🔥 کپشن آلبوم', '']          # فقط قطعه کپشن‌دار
    fmt = kwargs.get('formatting_entities')
    flat = []
    for item in (fmt or []):
        flat.extend(item if isinstance(item, (list, tuple)) else [item])
    assert custom_ids(flat) == {FIRE}                 # کپشن آلبوم تبدیل شد
    assert kwargs.get('reply_to') == 7                # reply آلبوم حفظ شد
    deleted_ids = sorted(mid for _, mids in client.deleted for mid in mids)
    assert deleted_ids == [50, 51]                    # هر دو قطعه حذف شد


def test_album_with_entities_released_without_resend(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)
    monkeypatch.setattr(rmod, 'ALBUM_FLUSH_DELAY_SECONDS', 0.05)
    manager, client, _ = make_manager()
    custom = types.MessageEntityCustomEmoji(offset=0, length=2, document_id=FIRE)
    part1 = make_message('🔥 کپشن', msg_id=60, media=NS(m='p1'),
                         grouped_id='album-2', entities=[custom])
    part2 = make_message('', msg_id=61, media=NS(m='p2'), grouped_id='album-2')

    async def scenario():
        assert await manager.handle_outgoing(part1) == 'handled'
        assert await manager.handle_outgoing(part2) == 'handled'
        await asyncio.sleep(0.2)

    run(scenario())
    assert client.sends == []                          # تبدیل‌شده؛ نیازی نیست
    assert client.deleted == []


# ================================================== install / uninstall
def test_install_requires_converter_engine():
    client = FakeClient()
    engine = make_engine()
    client._premium_emoji_converter = engine
    manager = rmod.install_premium_resend(client, engine)
    assert manager is client._premium_resend_manager
    rmod.uninstall_premium_resend(client)
    assert not hasattr(client, '_premium_resend_manager')


def test_install_rejects_client_without_converter():
    client = FakeClient()
    engine = make_engine()
    assert rmod.install_premium_resend(client, engine) is None


# ================================================== log block
def test_premium_resend_log_block_format():
    block = tlog.format_premium_resend_debug(
        chat='Group (-777)', message_id=10, converted=True, deleted=True,
        resent=True)
    lines = block.split('\n')
    assert lines[0] == '[PremiumResend]'
    assert 'chat: Group (-777)' in lines
    assert 'message_id: 10' in lines
    assert 'converted: True' in lines
    assert 'deleted: True' in lines
    assert 'resent: True' in lines
    assert 'reason:' not in lines


def test_premium_resend_log_block_with_reason():
    block = tlog.format_premium_resend_debug(
        chat='Saved (1)', message_id=3, converted=True, deleted=False,
        resent=False, reason='resend failed (RuntimeError)')
    assert 'reason: resend failed (RuntimeError)' in block.split('\n')


# ================================================== integration: injector prefers resend
class OfflineClient(TelegramClient):
    """کلاینت واقعی Telethon؛ فقط مرز شبکه شبیه‌سازی شده (مطابق تست‌های پایپ‌لاین)."""

    def __init__(self):
        super().__init__(MemorySession(), 12345, 'offline-test-only')
        self._mb_entity_cache.extend([types.User(123, access_hash=456)], [])
        self.committed = []

    async def _call(self, sender, request, ordered=False, flood_sleep_threshold=None):
        try:
            bytes(request)  # real TL serialization (some requests skip it)
        except TypeError:
            pass
        if utils.is_list_like(request):  # delete_messages sends a list
            results = []
            for single in request:
                result = await self._handle_single_request(single)
                if result is not None:
                    results.append(result)
            return results[0] if len(results) == 1 else results
        result = await self._handle_single_request(request)
        if result is None:
            raise AssertionError('Unexpected request: ' + type(request).__name__)
        return result

    async def _handle_single_request(self, request):
        if isinstance(request, functions.messages.SendMessageRequest):
            self.committed.append(request)
            return types.Updates(
                [types.UpdateMessageID(len(self.committed), request.random_id),
                 types.UpdateNewMessage(types.Message(
                     len(self.committed), types.PeerUser(123), date=NOW, out=True,
                     message=request.message, entities=request.entities), 1, 1)],
                [], [], NOW, 1)
        if isinstance(request, functions.messages.DeleteMessagesRequest):
            self.committed.append(request)
            return types.messages.AffectedMessages(pts=1, pts_count=1,
                                                   random_ids=[])
        if isinstance(request, functions.messages.EditMessageRequest):
            self.committed.append(request)
            return types.Updates(
                [types.UpdateEditMessage(types.Message(
                    request.id, types.PeerUser(123), date=NOW, out=True,
                    message=request.message, entities=request.entities), 1, 1)],
                [], [], NOW, 1)
        return None  # _call converts None into the unexpected-request error


def test_injector_prefers_resend_over_edit(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_OUTGOING_FIX', True)
    client = OfflineClient()
    account = NS(bot=False, premium=True, id=8359698350)
    engine = mod.install_premium_emoji_converter(
        client, account=account, is_enabled=lambda: True)
    manager = rmod.install_premium_resend(client, engine)
    injector = mod.install_premium_emoji_outgoing_injector(client, engine)

    # پیام «رسیده از گوشی»: بدون entity → باید resend شود نه edit
    message = types.Message(77, types.PeerUser(123), date=NOW, out=True,
                            message='🔥 سلام', entities=[])
    message._input_chat = types.PeerUser(123)   # مطابق الگوی تست‌های پایپ‌لاین
    event = NS(message=message, chat_id=123)
    run(injector(event))
    sends = [r for r in client.committed
             if isinstance(r, functions.messages.SendMessageRequest)]
    edits = [r for r in client.committed
             if isinstance(r, functions.messages.EditMessageRequest)]
    deletes = [r for r in client.committed
               if isinstance(r, functions.messages.DeleteMessagesRequest)]
    assert len(sends) == 1                            # کپی با entity ارسال شد
    assert custom_ids(sends[0].entities) == {FIRE}
    assert len(deletes) == 1                          # پیام اصلی حذف شد
    assert edits == []                                # edit انجام نشد (resend برنده)


def test_injector_edit_fallback_when_resend_off(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_OUTGOING_FIX', True)
    client = OfflineClient()
    account = NS(bot=False, premium=True, id=8359698350)
    engine = mod.install_premium_emoji_converter(
        client, account=account, is_enabled=lambda: True)
    # پنل: resend خاموش → رفتار قبلی (edit) باید حفظ شود
    manager = rmod.install_premium_resend(client, engine,
                                          is_enabled=lambda: False)
    injector = mod.install_premium_emoji_outgoing_injector(client, engine)
    message = types.Message(78, types.PeerUser(123), date=NOW, out=True,
                            message='🔥 سلام', entities=[])
    message._input_chat = types.PeerUser(123)
    run(injector(NS(message=message, chat_id=123)))
    edits = [r for r in client.committed
             if isinstance(r, functions.messages.EditMessageRequest)]
    sends = [r for r in client.committed
             if isinstance(r, functions.messages.SendMessageRequest)]
    assert len(edits) == 1
    assert sends == []
    assert custom_ids(edits[0].entities) == {FIRE}


def test_bot_client_never_gets_resend():
    client = OfflineClient()
    account = NS(bot=True, premium=False, id=1)
    engine = mod.install_premium_emoji_converter(client, account=account)
    assert engine is None
    assert rmod.install_premium_resend(client, engine) is None


# ================================================== guard: نسخه بدون entity
def test_original_kept_when_resent_copy_lacks_entity(monkeypatch):
    """اگر نسخه ارسالی entity نداشت (مثلاً clean retry)، اصل حذف نشود."""
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)
    manager, client, _ = make_manager()

    def send_without_entities(entity, message, **kwargs):
        # شبیه‌سازی clean-retry: ارسال بدون entity
        client.sends.append(('message', entity, message, kwargs))
        return NS(id=950, chat_id=CHAT_ID, entities=None)

    client.send_message = send_without_entities
    message = make_message('🔥 مهم', msg_id=70)
    verdict = run(manager.handle_outgoing(message))
    assert verdict == 'skipped'          # injector/edit مسیر خودش را می‌رود
    assert client.deleted == []          # پیام اصلی حذف نشد
    assert manager.stats['resent'] == 0


def test_album_originals_kept_when_no_entity_in_result(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)
    monkeypatch.setattr(rmod, 'ALBUM_FLUSH_DELAY_SECONDS', 0.05)
    manager, client, _ = make_manager()
    part1 = make_message('🔥 کپشن', msg_id=80, media=NS(m='p1'),
                         grouped_id='album-3')
    part2 = make_message('', msg_id=81, media=NS(m='p2'), grouped_id='album-3')

    async def send_album_without_entities(entity, files, caption=None, **kwargs):
        client.sends.append(('file', entity, caption, kwargs))
        return [NS(id=960, chat_id=CHAT_ID, entities=None),
                NS(id=961, chat_id=CHAT_ID, entities=None)]

    client.send_file = send_album_without_entities

    async def scenario():
        await manager.handle_outgoing(part1)
        await manager.handle_outgoing(part2)
        await asyncio.sleep(0.2)

    run(scenario())
    assert client.deleted == []          # هر دو اصل حفظ شدند
