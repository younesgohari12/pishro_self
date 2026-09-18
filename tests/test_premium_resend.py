"""Acceptance tests — ارسال دوباره ایموجی ویژه (Delete + New Send) v0.09.13.

ماتریس تست اجباری spec مالک، همه آفلاین و بدون شبکه (قرارداد AGENTS.md):
    1) متن            2) Reply           3) عکس با Caption
    4) ویدیو با Caption  5) آلبوم          6) گروه          7) خصوصی

نتیجه الزامی هر تست:
    پیام اول حذف شده + پیام دوم ایجاد شده + Custom Emoji فعال

🔴 قاعده مالک: هیچ EditMessageRequest و هیچ edit_message ای در کل جریان
مجاز نیست. ترتیب الزامی (spec v0.09.18): کپی محتوا → ارسال پیام جدید →
بعد از موفقیتِ ارسال، حذف پیام اصلی. اگر ارسال شکست بخورد، پیام اصلی
باقی می‌ماند (هیچ حذفی قبل از موفقیتِ ارسال انجام نمی‌شود).
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
from services import emoji_resend_manager as rmod
from services import premium_resend_service as rsvc
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
        self.fetch_error = None
        self.server = {}

    def store(self, message, *, entities=None):
        self.server[getattr(message, 'id', 0)] = (
            message if entities is None else NS(
                id=getattr(message, 'id', 0),
                chat_id=getattr(message, 'chat_id', CHAT_ID),
                message=getattr(message, 'message', ''),
                entities=list(entities),
                media=getattr(message, 'media', None),
                reply_to=getattr(message, 'reply_to', None),
                silent=getattr(message, 'silent', False)))

    async def get_messages(self, entity, ids=None, **kwargs):
        if self.fetch_error:
            raise self.fetch_error
        view = self.server.get(ids)
        if view is None:
            # واکشی برای پیام‌های ذخیره‌نشده ناموفق است → تصمیم با نمای event
            raise RuntimeError('fetch unavailable')
        return view

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
        if isinstance(file, (list, tuple)):
            return [NS(id=900 + len(self.sends) + i,
                       chat_id=getattr(entity, 'user_id', CHAT_ID),
                       entities=(kwargs.get('formatting_entities') or [None] * len(file))[i])
                    for i in range(len(file))]
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
    manager = rmod.EmojiResendManager(client, engine, is_enabled=is_enabled)
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


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(rsvc, 'VERIFY_DELAY_SECONDS', 0.0)
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)


# ================================================== gating
def test_resend_disabled_when_config_hard_off(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', False)
    manager, _, _ = make_manager()
    assert manager.resend_enabled() is False


def test_resend_disabled_when_converter_off(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)
    engine = make_engine(is_enabled=lambda: False)
    manager = rmod.EmojiResendManager(FakeClient(), engine)
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


# ================================================== ماتریس اجباری مالک
def test_1_text_message_delete_then_new_send(monkeypatch):
    """۱) متن: پیام اول حذف، پیام دوم با Custom Emoji ایجاد شود."""
    manager, client, _ = make_manager()
    message = make_message('🔥 تست', msg_id=10)
    verdict = run(manager.handle_outgoing(message))
    assert verdict == 'handled'
    # حذف اصل بعد از موفقیتِ ارسال نسخه جدید (spec v0.09.18)
    assert len(client.deleted) == 1
    assert client.deleted == [(CHAT_ID, [10])]
    assert len(client.sends) == 1
    kind, entity, text, kwargs = client.sends[0]
    assert kind == 'message'
    assert text == '🔥 تست'                        # متن دست‌نخورده
    assert custom_ids(kwargs.get('formatting_entities')) == {FIRE}  # فعال
    assert kwargs.get('parse_mode') is None
    assert manager.stats['resent'] == 1
    assert manager.stats['deleted'] == 1


def test_2_reply_delete_then_new_send(monkeypatch):
    """۲) Reply: حذف + ارسال جدید با حفظ reply و Custom Emoji."""
    manager, client, _ = make_manager()
    bold = types.MessageEntityBold(offset=2, length=4)
    message = make_message('🔥 سلام دنیا', msg_id=11, entities=[bold],
                           reply_to=42)
    verdict = run(manager.handle_outgoing(message))
    assert verdict == 'handled'
    assert client.deleted == [(CHAT_ID, [11])]
    kind, entity, text, kwargs = client.sends[0]
    assert text == '🔥 سلام دنیا'
    assert kwargs.get('reply_to') == 42              # reply حفظ شد
    fmt = kwargs.get('formatting_entities')
    assert any(isinstance(e, types.MessageEntityBold) for e in fmt)  # فرمت حفظ
    assert custom_ids(fmt) == {FIRE}                 # Custom Emoji فعال


def test_3_photo_with_caption_delete_then_new_send(monkeypatch):
    """۳) عکس با Caption: حذف + ارسال فایل جدید با کپشن و Custom Emoji."""
    manager, client, _ = make_manager()
    media = NS(media_key='photo-ref')
    message = make_message('🔥 عکس جدید', msg_id=12, media=media)
    assert run(manager.handle_outgoing(message)) == 'handled'
    assert client.deleted == [(CHAT_ID, [12])]
    kind, entity, caption, kwargs = client.sends[0]
    assert kind == 'file'
    assert caption == '🔥 عکس جدید'
    assert custom_ids(kwargs.get('formatting_entities')) == {FIRE}


def test_4_video_with_caption_delete_then_new_send(monkeypatch):
    """۴) ویدیو با Caption: حذف + ارسال فایل جدید با کپشن و Custom Emoji."""
    manager, client, _ = make_manager()
    media = NS(media_key='video-ref')
    message = make_message('🔥 ویدیو جدید', msg_id=13, media=media)
    assert run(manager.handle_outgoing(message)) == 'handled'
    assert client.deleted == [(CHAT_ID, [13])]
    kind, entity, caption, kwargs = client.sends[0]
    assert kind == 'file'
    assert caption == '🔥 ویدیو جدید'
    assert custom_ids(kwargs.get('formatting_entities')) == {FIRE}


def test_5_album_delete_then_new_send_as_one_album(monkeypatch):
    """۵) آلبوم: کل آلبوم یک‌بار حذف و یک‌بار ارسال می‌شود."""
    manager, client, _ = make_manager()
    monkeypatch.setattr(rsvc, 'ALBUM_FLUSH_DELAY_SECONDS', 0.05)
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
    assert captions == ['🔥 کپشن آلبوم', '']
    fmt = kwargs.get('formatting_entities')
    flat = []
    for item in (fmt or []):
        flat.extend(item if isinstance(item, (list, tuple)) else [item])
    assert custom_ids(flat) == {FIRE}                 # Custom Emoji فعال
    assert kwargs.get('reply_to') == 7                # reply آلبوم حفظ شد
    deleted_ids = sorted(mid for _, mids in client.deleted for mid in mids)
    assert deleted_ids == [50, 51]                    # هر دو قطعه حذف شدند


def test_6_group_delete_then_new_send(monkeypatch):
    """۶) گروه: حذف + ارسال جدید در گروه."""
    manager, client, _ = make_manager()
    group_msg = make_message('😂 گروه', msg_id=20, chat_id=GROUP_ID)
    assert run(manager.handle_outgoing(group_msg)) == 'handled'
    assert client.deleted == [(GROUP_ID, [20])]
    kind, entity, text, kwargs = client.sends[0]
    assert text == '😂 گروه'
    assert custom_ids(kwargs.get('formatting_entities')) == {LAUGH}


def test_7_private_and_saved_delete_then_new_send(monkeypatch):
    """۷) خصوصی/Saved: حذف + ارسال جدید."""
    manager, client, _ = make_manager()
    saved_msg = make_message('😂 سیو', msg_id=21, chat_id=8359698350)
    assert run(manager.handle_outgoing(saved_msg)) == 'handled'
    assert client.deleted == [(8359698350, [21])]
    assert len(client.sends) == 1
    kind, entity, text, kwargs = client.sends[0]
    assert custom_ids(kwargs.get('formatting_entities')) == {LAUGH}


# ================================================== skip cases
def test_skip_when_custom_entity_already_present(monkeypatch):
    manager, client, _ = make_manager()
    custom = types.MessageEntityCustomEmoji(offset=0, length=2, document_id=FIRE)
    message = make_message('🔥 سلام', entities=[custom])
    assert run(manager.handle_outgoing(message)) == 'skipped'
    assert client.sends == [] and client.deleted == []


def test_skip_when_no_mappable_emoji(monkeypatch):
    manager, client, _ = make_manager()
    for text in ('سلام بدون ایموجی', '🙂', ''):
        assert run(manager.handle_outgoing(
            make_message(text))) == 'skipped', text
    assert client.sends == []
    assert client.deleted == []                       # هیچ حذفی هم رخ نداد


def test_skip_when_panel_off_or_hard_off(monkeypatch):
    manager, client, _ = make_manager(is_enabled=lambda: False)
    assert run(manager.handle_outgoing(make_message())) == 'skipped'
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', False)
    assert run(manager.handle_outgoing(make_message())) == 'skipped'
    assert client.sends == []
    assert client.deleted == []


def test_skip_forward_via_bot_and_action(monkeypatch):
    manager, client, _ = make_manager()
    assert run(manager.handle_outgoing(make_message(fwd=True))) == 'skipped'
    assert run(manager.handle_outgoing(make_message(via_bot=True))) == 'skipped'
    assert run(manager.handle_outgoing(make_message(action=True))) == 'skipped'
    assert client.sends == []
    assert client.deleted == []


# ================================================== failure safety
def test_delete_failure_after_resend_keeps_content(monkeypatch):
    """حذف بعد از ارسال موفق شکست بخورد → نسخه جدید هست؛ اصل باقی می‌ماند.

    spec v0.09.18: هیچ حذفی قبل از موفقیتِ ارسال انجام نمی‌شود؛ پس حذفِ
    ناموفق فقط یعنی «موقتاً دو نسخه» — محتوا هرگز گم نمی‌شود و خطا log می‌شود.
    """
    manager, client, _ = make_manager()
    client.delete_error = RuntimeError('no rights')
    message = make_message('🔥 مهم', msg_id=31)
    verdict = run(manager.handle_outgoing(message))
    assert verdict == 'handled'
    assert len(client.sends) == 1      # نسخه جدید ارسال شد (اولویت: حفظ محتوا)
    assert client.deleted == []        # حذف اصل ناموفق ماند
    assert manager.stats['resent'] == 1
    assert manager.stats['kept'] == 1  # پیام اصلی هم باقی ماند (گزارش شد)


def test_send_failure_retries_then_rescue_without_entity(monkeypatch):
    """ارسال شکست بخورد → چند تلاش؛ در نهایت نجات محتوا بدون entity."""
    monkeypatch.setattr(rsvc, 'SEND_RETRY_DELAY_SECONDS', 0.0)
    manager, client, _ = make_manager()
    attempts = {'n': 0}

    async def flaky_send(entity, message, **kwargs):
        attempts['n'] += 1
        if attempts['n'] <= 3:
            raise RuntimeError('network down')
        # تلاش نجات: بدون entity موفق است
        return NS(id=999, chat_id=CHAT_ID, entities=None)

    client.send_message = flaky_send
    message = make_message('🔥 مهم', msg_id=30)
    verdict = run(manager.handle_outgoing(message))
    assert verdict == 'handled'
    assert attempts['n'] == 4                        # ۳ تلاش + ۱ نجات
    assert client.deleted == [(CHAT_ID, [30])]       # اصل بعد از ارسال موفق حذف شد
    assert manager.stats['failed'] == 0              # محتوا نجات یافت


def test_total_send_failure_keeps_original(monkeypatch):
    """حتی نجات محتوا هم شکست بخورد → پیام اصلی دست‌نخورده + گزارش شکست.

    spec v0.09.18: هیچ حذفی قبل از موفقیتِ ارسال انجام نمی‌شود؛ پس شکست
    کامل ارسال یعنی پیام اصلی ساده و سالم سر جای خودش می‌ماند.
    """
    monkeypatch.setattr(rsvc, 'SEND_RETRY_DELAY_SECONDS', 0.0)
    manager, client, _ = make_manager()
    client.send_error = RuntimeError('network down')
    message = make_message('🔥 مهم', msg_id=33)
    verdict = run(manager.handle_outgoing(message))
    assert verdict == 'skipped'
    assert manager.stats['failed'] == 1
    assert client.deleted == []                      # اصل حذف نشد (حفظ محتوا)
    assert client.sends == []                        # هیچ نسخه‌ای ساخته نشد


# ================================================== loop guards
def test_no_loop_for_resent_message(monkeypatch):
    manager, client, _ = make_manager()
    original = make_message('🔥 سلام', msg_id=40)
    assert run(manager.handle_outgoing(original)) == 'handled'
    resent = make_message('🔥 سلام', msg_id=901, entities=[])  # همان نسخه ارسالی
    calls_before = len(client.sends)
    assert run(manager.handle_outgoing(resent)) == 'handled'
    assert len(client.sends) == calls_before          # ارسال دوباره نشد
    assert client.deleted == [(CHAT_ID, [40])]        # حذف جدید هم نداشت


def test_strip_condition_sets_cooldown(monkeypatch):
    manager, client, engine = make_manager()
    for msg_id in (910, 911):
        stripped = make_message('🔥', msg_id=msg_id)
        manager._mark_recent(CHAT_ID, msg_id)
        assert run(manager.handle_outgoing(stripped)) == 'handled'
    assert engine.disabled_until > 0                  # cooldown فعال شد


def test_cooldown_blocks_resend(monkeypatch):
    manager, client, engine = make_manager()
    engine.disabled_until = 10 ** 12                  # همیشه در cooldown
    assert run(manager.handle_outgoing(make_message())) == 'skipped'
    assert client.sends == []
    assert client.deleted == []


# ================================================== album keep cases
def test_album_with_entities_released_without_resend(monkeypatch):
    monkeypatch.setattr(rsvc, 'ALBUM_FLUSH_DELAY_SECONDS', 0.05)
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


def test_album_not_resent_when_any_delete_fails(monkeypatch):
    """آلبوم: اگر حذف حتی یک قطعه شکست بخورد، آلبوم جدید ارسال نمی‌شود."""
    monkeypatch.setattr(rsvc, 'ALBUM_FLUSH_DELAY_SECONDS', 0.05)
    manager, client, _ = make_manager()
    part1 = make_message('🔥 کپشن', msg_id=80, media=NS(m='p1'),
                         grouped_id='album-3')
    part2 = make_message('', msg_id=81, media=NS(m='p2'), grouped_id='album-3')
    calls = {'n': 0}

    async def flaky_delete(entity, message_ids, **kwargs):
        calls['n'] += 1
        if calls['n'] == 2:
            raise RuntimeError('no rights')
        client.deleted.append((entity, list(
            message_ids if isinstance(message_ids, (list, tuple))
            else [message_ids])))

    client.delete_messages = flaky_delete

    async def scenario():
        await manager.handle_outgoing(part1)
        await manager.handle_outgoing(part2)
        await asyncio.sleep(0.2)

    run(scenario())
    assert len(client.sends) == 1        # آلبوم جدید ارسال شد (قبل از حذف)
    deleted_ids = sorted(mid for _, mids in client.deleted for mid in mids)
    assert deleted_ids == [80]           # فقط قطعه اول حذف شد؛ قطعه دوم ماند
    assert manager.stats['kept'] == 1    # گزارش حذف ناقص
    assert manager.stats['resent'] == 1


# ================================================== install / uninstall
def test_install_requires_converter_engine():
    client = FakeClient()
    engine = make_engine()
    client._premium_emoji_converter = engine
    manager = rmod.install_emoji_resend_manager(client, engine)
    assert manager is client._premium_resend_manager
    rmod.uninstall_emoji_resend_manager(client)
    assert not hasattr(client, '_premium_resend_manager')


def test_install_rejects_client_without_converter():
    client = FakeClient()
    engine = make_engine()
    assert rmod.install_emoji_resend_manager(client, engine) is None


def test_compat_shim_reexports():
    """shim قدیمی premium_resend همه نمادها را بازنشر می‌کند."""
    from services import premium_resend as shim
    assert shim.PremiumResendManager is rmod.EmojiResendManager
    assert shim.install_premium_resend is rmod.install_emoji_resend_manager
    assert shim.uninstall_premium_resend is rmod.uninstall_emoji_resend_manager


# ================================================== log blocks (فارسی)
def test_premium_resend_log_block_format():
    block = tlog.format_premium_resend_debug(
        chat='Group (-777)', message_id=10, deleted=True, resent=True,
        new_message_id=901)
    lines = block.split('\n')
    assert lines[0] == '[ارسال دوباره]'
    assert 'شناسه چت: Group (-777)' in lines
    assert 'شناسه پیام: 10' in lines
    assert 'پیام حذف شد: بله' in lines
    assert 'پیام جدید ارسال شد: بله (msg=901)' in lines
    assert 'نتیجه:' not in lines


def test_premium_resend_log_block_with_reason():
    block = tlog.format_premium_resend_debug(
        chat='Saved (1)', message_id=3, deleted=False, resent=False,
        reason='حذف پیام اصلی ناموفق بود')
    assert 'پیام حذف شد: خیر' in block.split('\n')
    assert 'پیام جدید ارسال شد: خیر' in block.split('\n')
    assert 'نتیجه: حذف پیام اصلی ناموفق بود' in block.split('\n')


# ================================================== integration: injector never edits
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
            return types.messages.AffectedMessages(pts=1, pts_count=1)
        if isinstance(request, functions.messages.EditMessageRequest):
            self.committed.append(request)
            raise AssertionError('EditMessageRequest مطلقاً ممنوع است')
        return None  # _call converts None into the unexpected-request error


def test_injector_full_flow_delete_and_new_send(monkeypatch):
    """پیام «رسیده از گوشی»: حذف + ارسال جدید؛ EditMessageRequest ممنوع."""
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_OUTGOING_FIX', True)
    monkeypatch.setattr(rsvc, 'VERIFY_DELAY_SECONDS', 0.0)
    client = OfflineClient()
    account = NS(bot=False, premium=True, id=8359698350)
    engine = mod.install_premium_emoji_converter(
        client, account=account, is_enabled=lambda: True)
    manager = rmod.install_emoji_resend_manager(client, engine)
    injector = mod.install_premium_emoji_outgoing_injector(client, engine)

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
    assert len(sends) == 1                            # نسخه جدید با entity
    assert custom_ids(sends[0].entities) == {FIRE}
    assert len(deletes) == 1                          # پیام اصلی حذف شد
    assert edits == []                                # edit مطلقاً انجام نشد
    # ترتیب الزامی مالک (v0.09.18): ارسال قبل از حذف
    kinds = [type(r).__name__ for r in client.committed]
    assert kinds.index('SendMessageRequest') < kinds.index('DeleteMessagesRequest')


def test_injector_without_manager_keeps_message_untouched(monkeypatch):
    """مدیر ارسال دوباره نصب نباشد → پیام دست‌نخورده (هیچ edit، هیچ delete)."""
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_OUTGOING_FIX', True)
    client = OfflineClient()
    account = NS(bot=False, premium=True, id=8359698350)
    engine = mod.install_premium_emoji_converter(
        client, account=account, is_enabled=lambda: True)
    injector = mod.install_premium_emoji_outgoing_injector(client, engine)
    message = types.Message(78, types.PeerUser(123), date=NOW, out=True,
                            message='🔥 سلام', entities=[])
    message._input_chat = types.PeerUser(123)
    run(injector(NS(message=message, chat_id=123)))
    edits = [r for r in client.committed
             if isinstance(r, functions.messages.EditMessageRequest)]
    sends = [r for r in client.committed
             if isinstance(r, functions.messages.SendMessageRequest)]
    deletes = [r for r in client.committed
               if isinstance(r, functions.messages.DeleteMessagesRequest)]
    assert edits == [] and sends == [] and deletes == []


def test_converter_never_wraps_edit_message():
    """کانورتر هرگز edit_message را wrap نمی‌کند (هیچ Edit در سیستم)."""
    client = OfflineClient()
    account = NS(bot=False, premium=True, id=8359698350)
    engine = mod.install_premium_emoji_converter(
        client, account=account, is_enabled=lambda: True)
    assert engine is not None
    # edit_message باید همان متد اصلی Telethon باشد (بدون wrap)
    assert 'edit_message' not in engine.originals


def test_bot_client_never_gets_resend():
    client = OfflineClient()
    account = NS(bot=True, premium=False, id=1)
    engine = mod.install_premium_emoji_converter(client, account=account)
    assert engine is None
    assert rmod.install_emoji_resend_manager(client, engine) is None
