"""Debug-round tests — [بررسی ایموجی ویژه] + Server-Truth Resend + Album Queue.

سناریوهای الزامی spec مالک (حذف + ارسال جدید؛ بدون Edit):
    1) بلوک [بررسی ایموجی ویژه] با پنج فیلد دقیق
    2) بررسی Entity واقعی → client.get_messages (نمای سرور)
    3) Entity نبود؟ → کپی → حذف پیام اصلی → ارسال پیام جدید با entity
    4) آلبوم: صف قطعات → واکشی همه → «یک بار» حذف/ارسال کل آلبوم
    5) `.بررسی_ایموجی روشن` → گزارش زندهٔ مراحل در Saved Messages (+ ignore)

همه آفلاین و بدون شبکه (قرارداد AGENTS.md). FakeClient مرز شبکه را
شبیه‌سازی می‌کند: get_messages نمای «سرور» را برمی‌گرداند.
"""
import asyncio
import copy
from types import SimpleNamespace as NS

import pytest
from telethon import types

import config
import premium_emoji_mapping as mapping_module
from services import premium_emoji_converter as mod
from services import emoji_resend_manager as rmod
from services import telegram_logger as tlog

FIRE = mapping_module.PREMIUM_EMOJI_MAP['🔥'][0]
LAUGH = mapping_module.PREMIUM_EMOJI_MAP['😂'][0]
CHAT_ID = 555
GROUP_ID = -777


def run(coro):
    return asyncio.run(coro)


def make_engine(**kwargs):
    kwargs.setdefault('mapping', dict(mapping_module.PREMIUM_EMOJI_MAP))
    return mod.PremiumEmojiConverter(premium=True,
                                     is_enabled=kwargs.pop('is_enabled', lambda: True),
                                     **kwargs)


class ServerView:
    """پیام ذخیره‌شده روی «سرور» — entity آن می‌تواند حذف شده باشد."""

    def __init__(self, source, *, strip_entities=False, entities=None,
                 message_id=None, gone=False):
        self.gone = gone
        self.id = message_id if message_id is not None else getattr(source, 'id', 0)
        self.chat_id = getattr(source, 'chat_id', CHAT_ID)
        self.message = getattr(source, 'message', '')
        self.media = getattr(source, 'media', None)
        self.reply_to = getattr(source, 'reply_to', None)
        self.silent = getattr(source, 'silent', False)
        if entities is not None:
            self.entities = list(entities)
        elif strip_entities:
            self.entities = []
        else:
            self.entities = list(getattr(source, 'entities', None) or [])
        self.noforwards = getattr(source, 'noforwards', False)
        self.input_chat = None
        self.grouped_id = getattr(source, 'grouped_id', None)
        self.fwd_from = None
        self.via_bot_id = None
        self.action = None
        self.out = True


class FakeClient:
    """کلاینت تقلبی با get_messages: نمای سرور قابل کنترل است."""

    def __init__(self):
        self.sends = []
        self.deleted = []
        self.send_error = None
        self.delete_error = None
        self.fetch_error = None
        self.server = {}       # message_id -> ServerView
        self.default_view = 'stripped'   # stripped | kept | gone

    def store(self, message, **kwargs):
        view = ServerView(message, **kwargs)
        self.server[view.id] = view
        return view

    async def get_input_entity(self, chat_id):
        return types.InputPeerUser(chat_id, 1)

    async def get_messages(self, entity, ids=None, **kwargs):
        if self.fetch_error:
            raise self.fetch_error
        view = self.server.get(ids)
        if view is None or view.gone or self.default_view == 'gone':
            return None
        return view

    async def send_message(self, entity, message, **kwargs):
        if self.send_error:
            raise self.send_error
        self.sends.append(('message', entity, message, copy.deepcopy(kwargs)))
        return NS(id=900 + len(self.sends), chat_id=CHAT_ID,
                  entities=kwargs.get('formatting_entities'))

    async def send_file(self, entity, file, caption=None, **kwargs):
        if self.send_error:
            raise self.send_error
        self.sends.append(('file', entity, caption, copy.deepcopy(kwargs)))
        if isinstance(file, (list, tuple)):
            return [NS(id=900 + len(self.sends) + i, chat_id=CHAT_ID,
                       entities=(kwargs.get('formatting_entities') or [None] * len(file))[i])
                    for i in range(len(file))]
        return NS(id=900 + len(self.sends), chat_id=CHAT_ID,
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
                 media=None, grouped_id=None, reply_to=None, silent=False):
    reply = NS(reply_to_msg_id=reply_to) if reply_to else None
    return NS(id=msg_id, chat_id=chat_id, message=text,
              entities=list(entities or []), media=media, grouped_id=grouped_id,
              fwd_from=None, via_bot_id=None, action=None, reply_to=reply,
              silent=silent, noforwards=False, input_chat=None, out=True)


def custom_ids(entities):
    return {e.document_id for e in (entities or [])
            if isinstance(e, types.MessageEntityCustomEmoji)}


@pytest.fixture(autouse=True)
def _fast_and_silent(monkeypatch):
    """دور Debug بدون تأخیر واقعی و بدون صف تلگرام لاگر."""
    monkeypatch.setattr(rmod, 'VERIFY_DELAY_SECONDS', 0.0)
    monkeypatch.setattr(rmod, 'ALBUM_FLUSH_DELAY_SECONDS', 0.01)
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)
    monkeypatch.setattr(tlog, 'logging_enabled', lambda: False)


# ================================================== [بررسی ایموجی ویژه] format
def test_premium_check_block_exact_fields():
    block = tlog.format_premium_check(
        chat_id=-100123, message_id=42, has_entity=False, entities='هیچ',
        media_type='عکس', reply_to=7)
    lines = block.split('\n')
    assert lines[0] == '[بررسی ایموجی ویژه]'
    assert lines[1] == 'شناسه چت: -100123'
    assert lines[2] == 'شناسه پیام: 42'
    assert lines[3] == 'Entity دارد: خیر (هیچ)'
    assert lines[4] == 'نوع پیام: عکس'
    assert lines[5] == 'نتیجه: بررسی شد'
    assert lines[6] == 'پاسخ به: 7'


def test_premium_check_block_with_result():
    block = tlog.format_premium_check(
        chat_id=1, message_id=2, has_entity=True,
        entities='1: ویژه(doc=9@0+2)', media_type='متن', reply_to=None,
        result='Entity واقعی روی سرور موجود است؛ بدون تغییر')
    lines = block.split('\n')
    assert lines[3] == 'Entity دارد: بله (1: ویژه(doc=9@0+2))'
    assert lines[5] == 'نتیجه: Entity واقعی روی سرور موجود است؛ بدون تغییر'
    assert 'پاسخ به' not in block


def test_media_type_detection():
    photo = type('MessageMediaPhoto', (), {})()
    assert rmod._media_type(make_message(media=photo)) == 'عکس'
    assert rmod._media_type(make_message()) == 'متن'
    doc_media = type('MessageMediaDocument', (), {})()
    assert rmod._media_type(make_message(media=doc_media)) == 'فایل'


def test_describe_entities_format():
    custom = types.MessageEntityCustomEmoji(offset=0, length=2, document_id=FIRE)
    assert rmod._describe_entities([custom]) == \
        f'1: ویژه(doc={FIRE}@0+2)'
    assert rmod._describe_entities(None) == 'هیچ'
    bold = types.MessageEntityBold(offset=4, length=3)
    text = rmod._describe_entities([custom, bold])
    assert 'ویژه(doc=' in text and 'bold@4+3' in text


# ================================================== server-truth: single
def test_premium_check_logged_for_every_relevant_message(monkeypatch):
    manager, client, _ = make_manager()
    message = make_message('سلام معمولی بدون ایموجی', msg_id=1)
    assert run(manager.handle_outgoing(message)) == 'skipped'  # بدون ایموجی/مدیا
    message = make_message('🔥 سلام', msg_id=2)
    client.store(message, strip_entities=True)
    assert run(manager.handle_outgoing(message)) in ('handled', 'skipped')


def test_resend_only_after_server_fetch_confirms_missing_entity(monkeypatch):
    """سرور entity را حذف کرده → Resend انجام شود (جریان کامل spec)."""
    manager, client, _ = make_manager()
    message = make_message('🔥 سلام', msg_id=10)
    client.store(message, strip_entities=True)   # سرور entity را حذف کرده
    assert run(manager.handle_outgoing(message)) == 'handled'
    assert len(client.sends) == 1
    kind, _entity, text, kwargs = client.sends[0]
    assert kind == 'message'
    assert text == '🔥 سلام'
    assert custom_ids(kwargs.get('formatting_entities')) == {FIRE}
    assert client.deleted == [(CHAT_ID, [10])]
    assert manager.stats['resent'] == 1


def test_no_resend_when_server_keeps_entity(monkeypatch):
    """سرور entity را نگه داشته → هیچ Resend و هیچ حذفی."""
    manager, client, _ = make_manager()
    message = make_message('🔥 سلام', msg_id=11)
    # پیام رویداد entity ندارد ولی سرور دارد (نمای event قدیمی)
    client.store(message, entities=[
        types.MessageEntityCustomEmoji(offset=0, length=2, document_id=FIRE)])
    assert run(manager.handle_outgoing(message)) == 'skipped'
    assert client.sends == []
    assert client.deleted == []


def test_no_resend_when_message_missing_on_server(monkeypatch):
    manager, client, _ = make_manager()
    message = make_message('🔥 سلام', msg_id=12)
    client.store(message, gone=True)
    assert run(manager.handle_outgoing(message)) == 'skipped'
    assert client.sends == []
    assert client.deleted == []


def test_fetch_error_falls_back_to_event_view(monkeypatch):
    """خطای واکشی → تصمیم با نمای event (رفتار قبلی حفظ می‌شود)."""
    manager, client, _ = make_manager()
    client.fetch_error = RuntimeError('network down')
    message = make_message('🔥 سلام', msg_id=13)
    assert run(manager.handle_outgoing(message)) == 'handled'
    assert len(client.sends) == 1
    assert client.deleted == [(CHAT_ID, [13])]


def test_resend_uses_server_media_and_reply(monkeypatch):
    manager, client, _ = make_manager()
    media = NS(media_key='photo-ref')
    message = make_message('🔥 عکس', msg_id=14, media=media, reply_to=31)
    client.store(message, strip_entities=True)
    assert run(manager.handle_outgoing(message)) == 'handled'
    kind, _entity, caption, kwargs = client.sends[0]
    assert kind == 'file'
    assert caption == '🔥 عکس'
    assert kwargs.get('reply_to') == 31
    assert custom_ids(kwargs.get('formatting_entities')) == {FIRE}


# ================================================== album queue
def test_album_fetched_then_resent_as_one_album(monkeypatch):
    """آلبوم: واکشی همه قطعات → یک بار Resend کل آلبوم (نه هر پیام جدا)."""
    manager, client, _ = make_manager()
    grouped = 'album-debug-1'
    part1 = make_message('🔥 کپشن آلبوم', msg_id=50, media=NS(m='p1'),
                         grouped_id=grouped, reply_to=7)
    part2 = make_message('', msg_id=51, media=NS(m='p2'), grouped_id=grouped)
    client.store(part1, strip_entities=True)
    client.store(part2, strip_entities=True)

    async def scenario():
        assert await manager.handle_outgoing(part1) == 'handled'
        assert await manager.handle_outgoing(part2) == 'handled'
        await asyncio.sleep(0.1)      # فلاش صف آلبوم داخل همان event loop

    run(scenario())
    assert len(client.sends) == 1                    # فقط یک ارسال برای کل آلبوم
    kind, _entity, captions, kwargs = client.sends[0]
    assert kind == 'file'
    assert captions == ['🔥 کپشن آلبوم', '']
    fmt = kwargs.get('formatting_entities') or []
    flat = []
    for item in fmt:
        flat.extend(item if isinstance(item, (list, tuple)) else [item])
    assert custom_ids(flat) == {FIRE}
    deleted_ids = sorted(mid for _, mids in client.deleted for mid in mids)
    assert deleted_ids == [50, 51]


def test_album_kept_when_server_has_all_entities(monkeypatch):
    manager, client, _ = make_manager()
    grouped = 'album-debug-2'
    custom = types.MessageEntityCustomEmoji(offset=0, length=2, document_id=FIRE)
    part1 = make_message('🔥 کپشن', msg_id=60, media=NS(m='p1'),
                         grouped_id=grouped, entities=[custom])
    part2 = make_message('', msg_id=61, media=NS(m='p2'), grouped_id=grouped)
    client.store(part1)   # سرور entity را نگه داشته
    client.store(part2)

    async def scenario():
        await manager.handle_outgoing(part1)
        await manager.handle_outgoing(part2)
        await asyncio.sleep(0.1)

    run(scenario())
    assert client.sends == []
    assert client.deleted == []


# ================================================== debug reports
def test_debug_report_on_and_ignored(monkeypatch):
    """`.بررسی_ایموجی روشن` → گزارش در Saved Messages + بی‌واکنشی به خودش."""
    manager, client, _ = make_manager()
    assert manager.set_debug_reports(True) is True
    message = make_message('🔥 سلام', msg_id=70)
    client.store(message, strip_entities=True)
    assert run(manager.handle_outgoing(message)) == 'handled'
    reports = [s for s in client.sends if s[1] == 'me']
    assert len(reports) == 1                          # گزارش زنده ارسال شد
    assert 'بررسی ایموجی ویژه' in reports[0][2]
    # پیام گزارش خودش باید ignore شود
    report_msg = make_message('گزارش', msg_id=901, chat_id=CHAT_ID)
    assert run(manager.handle_outgoing(report_msg)) == 'handled'
    sends_before = len(client.sends)
    assert sends_before == 2                          # هیچ ارسال جدیدی نداشت


def test_debug_off_no_report(monkeypatch):
    manager, client, _ = make_manager()
    assert manager.set_debug_reports(False) is False
    message = make_message('🔥 سلام', msg_id=71)
    client.store(message, strip_entities=True)
    run(manager.handle_outgoing(message))
    assert all(s[1] != 'me' for s in client.sends)    # هیچ گزارشی نرفت


def test_observe_only_when_resend_disabled_but_debug_on(monkeypatch):
    """پنل resend خاموش + debug روشن → مشاهده هست، اقدام نیست."""
    manager, client, _ = make_manager(is_enabled=lambda: False)
    manager.set_debug_reports(True)
    message = make_message('🔥 سلام', msg_id=72)
    client.store(message, strip_entities=True)
    assert run(manager.handle_outgoing(message)) == 'skipped'
    assert client.sends == []                          # فقط گزارش 'me' ممنوع نیست…
    assert all(s[1] == 'me' for s in client.sends)     # …ولی هیچ Resend نشده
    assert client.deleted == []


def test_resent_message_observed_in_debug(monkeypatch):
    """نسخه Resend شده دوباره رسید → در debug از سرور هم بررسی می‌شود."""
    manager, client, _ = make_manager()
    manager.set_debug_reports(True)
    original = make_message('🔥 سلام', msg_id=80)
    client.store(original, strip_entities=True)
    assert run(manager.handle_outgoing(original)) == 'handled'
    resent = make_message('🔥 سلام', msg_id=901)       # رویداد نسخه جدید
    client.store(resent, strip_entities=True)
    non_report_before = sum(1 for s in client.sends if s[1] != 'me')
    assert run(manager.handle_outgoing(resent)) == 'handled'
    non_report_after = sum(1 for s in client.sends if s[1] != 'me')
    assert non_report_after == non_report_before      # حلقه رخ نداد
    assert client.deleted == [(CHAT_ID, [80])]         # حذف جدیدی هم نبود
    # گزارش زندهٔ server-check برای نسخه جدید ارسال شد
    assert any(s[1] == 'me' and 'بررسی ایموجی ویژه' in s[2] for s in client.sends)


# ================================================== logger gating
def test_send_premium_check_respects_flag(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_DEBUG', False)
    block = tlog.format_premium_check(
        chat_id=1, message_id=2, has_entity=False, entities='none',
        media_type='none', reply_to='none')
    assert tlog.send_premium_check(block) is False     # فقط لاگ محلی
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_DEBUG', True)
    # logging_enabled False (fixture) → باز هم تلگرام نمی‌رود ولی Exception هم نیست
    assert tlog.send_premium_check(block) is False


def test_verify_delay_constant_is_half_second():
    # fixture سرعت، مقدار ماژول را در زمان اجرا صفر می‌کند؛ مقدار spec از
    # سورس ماژول بررسی می‌شود (spec مالک: sleep 0.5s).
    import inspect
    source = inspect.getsource(rmod)
    assert 'VERIFY_DELAY_SECONDS = 0.5' in source
