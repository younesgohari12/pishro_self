# -*- coding: utf-8 -*-
"""ماتریس کامل spec مالک v0.09.18 — سرویس مستقل Premium Resend.

پوشش ۱۱ حالت الزامی + تشخیص جامع + ناظر incoming + بلوک [PREMIUM_RESEND]:
    1) متن با emoji معمولی        2) متن با custom emoji (Entity)
    3) عکس با caption emoji       4) ویدیو با caption emoji
    5) فایل با caption emoji      6) آلبوم (media group)
    7) کانال                      8) گروه
    9) private                    10) resend failure → اصل حفظ
    11) anti loop (TTL cache + آلبوم)

قرارداد v0.09.18: کپی کامل → ارسال نسخه جدید → بعد از موفقیتِ ارسال حذف اصل.
هیچ EditMessageRequest ای در هیچ مسیری مجاز نیست.
"""
import asyncio
import copy
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
from telethon import types

import config
import premium_emoji_mapping as mapping_module
from services import premium_emoji_converter as mod
from services import premium_resend_service as svc
from services import telegram_logger as tlog

NOW = datetime(2026, 9, 18, tzinfo=timezone.utc)
FIRE = mapping_module.PREMIUM_EMOJI_MAP['🔥'][0]
LAUGH = mapping_module.PREMIUM_EMOJI_MAP['😂'][0]
CHAT_ID = 555
GROUP_ID = -777
CHANNEL_ID = -1001234567890
SAVED_ID = 8359698350


def run(coro):
    return asyncio.run(coro)


def make_engine(**kwargs):
    kwargs.setdefault('mapping', dict(mapping_module.PREMIUM_EMOJI_MAP))
    return mod.PremiumEmojiConverter(premium=True,
                                     is_enabled=kwargs.pop('is_enabled',
                                                           lambda: True),
                                     **kwargs)


class FakeClient:
    """کلاینت تقلبی با خط‌زمانی ترتیب فراخوانی‌ها (send قبل از delete)."""

    def __init__(self):
        self.sends = []
        self.deleted = []
        self.timeline = []          # ('send', id) / ('delete', id)
        self.send_error = None
        self.delete_error = None
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
        view = self.server.get(ids)
        if view is None:
            raise RuntimeError('fetch unavailable')
        return view

    async def get_input_entity(self, chat_id):
        return types.InputPeerUser(chat_id, 1)

    async def send_message(self, entity, message, **kwargs):
        if self.send_error:
            raise self.send_error
        self.sends.append(('message', entity, message,
                           copy.deepcopy(kwargs)))
        new_id = 900 + len(self.sends)
        self.timeline.append(('send', new_id))
        return NS(id=new_id, chat_id=getattr(entity, 'user_id', CHAT_ID),
                  entities=kwargs.get('formatting_entities'))

    async def send_file(self, entity, file, caption=None, **kwargs):
        if self.send_error:
            raise self.send_error
        self.sends.append(('file', entity, caption, copy.deepcopy(kwargs)))
        if isinstance(file, (list, tuple)):
            out = []
            for i in range(len(file)):
                new_id = 900 + len(self.sends) + i
                out.append(NS(id=new_id,
                              chat_id=getattr(entity, 'user_id', CHAT_ID),
                              entities=(kwargs.get('formatting_entities')
                                        or [None] * len(file))[i]))
            self.timeline.append(('send', tuple(x.id for x in out)))
            return out
        new_id = 900 + len(self.sends)
        self.timeline.append(('send', new_id))
        return NS(id=new_id, chat_id=getattr(entity, 'user_id', CHAT_ID),
                  entities=kwargs.get('formatting_entities'))

    async def delete_messages(self, entity, message_ids, **kwargs):
        if self.delete_error:
            raise self.delete_error
        if not isinstance(message_ids, (list, tuple)):
            message_ids = [message_ids]
        self.deleted.append((entity, list(message_ids)))
        for mid in message_ids:
            self.timeline.append(('delete', mid))


def make_manager(*, is_enabled=lambda: None, engine=None, client=None):
    engine = engine or make_engine()
    client = client or FakeClient()
    manager = svc.PremiumResendService(client, engine, is_enabled=is_enabled)
    return manager, client, engine


def make_message(text='🔥 سلام', *, msg_id=10, chat_id=CHAT_ID, entities=None,
                 media=None, grouped_id=None, reply_to=None, fwd=False,
                 via_bot=False, action=None, silent=False, out=True,
                 raw_text=None, reply_markup=None):
    reply = NS(reply_to_msg_id=reply_to) if reply_to else None
    return NS(id=msg_id, chat_id=chat_id, message=text,
              entities=list(entities or []), media=media, grouped_id=grouped_id,
              fwd_from='fwd' if fwd else None, via_bot_id=99 if via_bot else None,
              action=NS() if action else None, reply_to=reply, silent=silent,
              noforwards=False, input_chat=None, out=out,
              raw_text=raw_text if raw_text is not None else text,
              reply_markup=reply_markup)


def custom_ids(entities):
    return {e.document_id for e in (entities or [])
            if isinstance(e, types.MessageEntityCustomEmoji)}


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(svc, 'VERIFY_DELAY_SECONDS', 0.0)
    monkeypatch.setattr(svc, 'SEND_RETRY_DELAY_SECONDS', 0.0)
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)


# ============================================ تشخیص جامع ایموجی (root-cause)
def test_detect_unicode_emoji_in_text():
    info = svc._detect_emoji(make_message('سلام 🔥 دنیا'))
    assert info['has_emoji'] and info['kind'] == 'unicode'
    assert info['unicode'] and not info['custom']


def test_detect_custom_entity_and_media_attribute():
    custom = types.MessageEntityCustomEmoji(offset=0, length=2,
                                            document_id=FIRE)
    info = svc._detect_emoji(make_message('🔥', entities=[custom]))
    assert info['has_emoji'] and info['kind'] == 'custom' and info['custom']

    doc = NS(attributes=[NS(), types.DocumentAttributeCustomEmoji(
        '🔥', None, True)])
    info2 = svc._detect_emoji(make_message('', media=NS(document=doc)))
    assert info2['custom'] and info2['has_emoji']


def test_detect_emoji_in_media_caption():
    photo = type('MessageMediaPhoto', (), {})()
    info = svc._detect_emoji(make_message('🔥 کپشن', media=photo))
    assert info['has_emoji'] and info['in_caption']
    assert info['media_type'] == 'عکس'


def test_detect_no_emoji():
    for text in ('سلام بدون ایموجی', '', None):
        info = svc._detect_emoji(make_message(text))
        assert not info['has_emoji'] and info['kind'] == 'none'


def test_detect_media_type_labels():
    photo = type('MessageMediaPhoto', (), {})()
    assert svc._media_type(make_message('', media=photo)) == 'عکس'
    Doc = type('MessageMediaDocument', (), {})
    video = Doc()
    video.document = NS(attributes=[types.DocumentAttributeVideo(1, 1, 1)])
    assert svc._media_type(make_message('', media=video)) == 'ویدیو'
    round_doc = Doc()
    round_doc.document = NS(attributes=[types.DocumentAttributeVideo(
        1, 1, 1, round_message=True)])
    assert svc._media_type(make_message('', media=round_doc)) == 'ویدیو نوت'
    voice = Doc()
    voice.document = NS(attributes=[types.DocumentAttributeAudio(
        1, voice=True)])
    assert svc._media_type(make_message('', media=voice)) == 'ویس'
    audio = Doc()
    audio.document = NS(attributes=[types.DocumentAttributeAudio(1)])
    assert svc._media_type(make_message('', media=audio)) == 'فایل صوتی'
    anim = Doc()
    anim.document = NS(attributes=[types.DocumentAttributeAnimated(),
                                   types.DocumentAttributeVideo(1, 1, 1)])
    assert svc._media_type(make_message('', media=anim)) == 'گیف'
    sticker = Doc()
    sticker.document = NS(attributes=[types.DocumentAttributeSticker(
        '🔥', None)])
    assert svc._media_type(make_message('', media=sticker)) == 'استیکر'


# ================================================== ۱۱ حالت ماتریس مالک
def test_case_1_text_with_unicode_emoji():
    manager, client, _ = make_manager()
    verdict = run(manager.handle_outgoing(make_message('🔥 تست', msg_id=10)))
    assert verdict == 'handled'
    assert len(client.sends) == 1
    assert custom_ids(client.sends[0][3]['formatting_entities']) == {FIRE}
    assert client.deleted == [(CHAT_ID, [10])]
    # ترتیب v0.09.18: ارسال قبل از حذف
    order = [kind for kind, _ in client.timeline]
    assert order.index('send') < order.index('delete')


def test_case_2_text_with_custom_emoji_entity_no_double():
    """پیام با Entity ویژه وارد سیستم می‌شود ولی دوباره‌سازی نمی‌شود."""
    manager, client, _ = make_manager()
    custom = types.MessageEntityCustomEmoji(offset=0, length=2,
                                            document_id=FIRE)
    verdict = run(manager.handle_outgoing(
        make_message('🔥', msg_id=11, entities=[custom])))
    assert verdict == 'skipped'                       # از قبل پرمیوم است
    assert client.sends == [] and client.deleted == []


def test_case_3_photo_with_caption_emoji():
    manager, client, _ = make_manager()
    message = make_message('🔥 عکس', msg_id=12, media=NS(media_key='photo'))
    assert run(manager.handle_outgoing(message)) == 'handled'
    kind, _entity, caption, kwargs = client.sends[0]
    assert kind == 'file' and caption == '🔥 عکس'
    assert custom_ids(kwargs['formatting_entities']) == {FIRE}
    assert client.deleted == [(CHAT_ID, [12])]


def test_case_4_video_with_caption_emoji():
    manager, client, _ = make_manager()
    message = make_message('😂 ویدیو', msg_id=13, media=NS(media_key='video'))
    assert run(manager.handle_outgoing(message)) == 'handled'
    kind, _entity, caption, kwargs = client.sends[0]
    assert kind == 'file' and caption == '😂 ویدیو'
    assert custom_ids(kwargs['formatting_entities']) == {LAUGH}


def test_case_5_document_with_caption_emoji():
    manager, client, _ = make_manager()
    message = make_message('🔥 فایل', msg_id=14, media=NS(media_key='doc'))
    assert run(manager.handle_outgoing(message)) == 'handled'
    kind, _entity, caption, kwargs = client.sends[0]
    assert kind == 'file' and caption == '🔥 فایل'
    assert custom_ids(kwargs['formatting_entities']) == {FIRE}


def test_case_6_album_resent_as_one_unit():
    manager, client, _ = make_manager()
    monkey_delta = 0.05
    import services.premium_resend_service as m
    original_flush = m.ALBUM_FLUSH_DELAY_SECONDS
    m.ALBUM_FLUSH_DELAY_SECONDS = monkey_delta
    try:
        grouped = 'album-x'
        part1 = make_message('🔥 کپشن', msg_id=50, media=NS(m='p1'),
                             grouped_id=grouped, reply_to=7)
        part2 = make_message('', msg_id=51, media=NS(m='p2'),
                             grouped_id=grouped)
        for part in (part1, part2):
            client.store(part)

        async def scenario():
            assert await manager.handle_outgoing(part1) == 'handled'
            assert await manager.handle_outgoing(part2) == 'handled'
            await asyncio.sleep(0.25)

        run(scenario())
    finally:
        m.ALBUM_FLUSH_DELAY_SECONDS = original_flush
    assert len(client.sends) == 1                     # یک آلبوم یک‌جا
    deleted_ids = sorted(mid for _, mids in client.deleted for mid in mids)
    assert deleted_ids == [50, 51]


def test_case_7_channel_post_emoji():
    manager, client, _ = make_manager()
    message = make_message('🔥 کانال', msg_id=15, chat_id=CHANNEL_ID)
    assert run(manager.handle_outgoing(message)) == 'handled'
    assert client.deleted == [(CHANNEL_ID, [15])]
    assert custom_ids(client.sends[0][3]['formatting_entities']) == {FIRE}


def test_case_8_group_chat_emoji():
    manager, client, _ = make_manager()
    message = make_message('😂 گروه', msg_id=16, chat_id=GROUP_ID)
    assert run(manager.handle_outgoing(message)) == 'handled'
    assert client.deleted == [(GROUP_ID, [16])]
    assert custom_ids(client.sends[0][3]['formatting_entities']) == {LAUGH}


def test_case_9_private_and_saved_chat_emoji():
    manager, client, _ = make_manager()
    for chat, mid in ((CHAT_ID, 17), (SAVED_ID, 18)):
        message = make_message('🔥 خصوصی', msg_id=mid, chat_id=chat)
        assert run(manager.handle_outgoing(message)) == 'handled'
        assert client.deleted[-1] == (chat, [mid])


def test_case_10_resend_failure_keeps_original_and_bounded_retry():
    """ارسال شکست بخورد → اصل حذف نمی‌شود + retry محدود + گزارش خطا."""
    manager, client, _ = make_manager()
    client.send_error = RuntimeError('network down')
    message = make_message('🔥 مهم', msg_id=30)
    assert run(manager.handle_outgoing(message)) == 'skipped'
    assert client.deleted == []                       # اصل دست‌نخورده
    assert client.sends == []
    assert manager.stats['failed'] == 1
    assert manager.stats['resent'] == 0


def test_case_11_anti_loop_cache_ttl_and_album_guard():
    """پیام resend شده دوباره trigger نمی‌شود (cache با TTL + گارد آلبوم)."""
    manager, client, _ = make_manager()
    original = make_message('🔥 سلام', msg_id=40)
    assert run(manager.handle_outgoing(original)) == 'handled'
    sent_id = client.timeline[0][1]
    # cache ضد-loop پر شده: message_id + chat_id با TTL
    assert (CHAT_ID, sent_id) in manager.processed_message_ids
    assert svc.PROCESSED_TTL_SECONDS == 3600
    calls_before = len(client.sends)
    resent = make_message('🔥 سلام', msg_id=sent_id)
    assert run(manager.handle_outgoing(resent)) == 'handled'
    assert len(client.sends) == calls_before          # پردازش دوباره نشد
    # گارد ضد-loop آلبوم: قطعه‌ی خودِ ما بافر نمی‌شود
    part = make_message('', msg_id=sent_id, media=NS(m='p'),
                        grouped_id='own-album')
    assert run(manager.handle_outgoing(part)) == 'handled'
    assert manager._albums == {}


# ================================================== جریان‌های امنیت دیگر
def test_feature_off_means_no_change(monkeypatch):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_RESEND_MODE', False)
    manager, client, _ = make_manager(is_enabled=lambda: False)
    message = make_message('🔥 خاموش', msg_id=20)
    assert run(manager.handle_outgoing(message)) == 'skipped'
    assert client.sends == [] and client.deleted == []
    # پیام دریافتی هم در حالت خاموش هیچ اثری ندارد
    incoming = make_message('🔥', msg_id=21, out=False)
    assert run(manager.handle_incoming(incoming)) == 'skipped'


def test_incoming_is_observe_only():
    """پیام دریافتی: شناسایی می‌شود؛ هرگز delete/send انجام نمی‌شود."""
    manager, client, _ = make_manager()
    incoming = make_message('🔥 از دوست', msg_id=25, out=False)
    assert run(manager.handle_incoming(incoming)) == 'observed'
    assert client.sends == [] and client.deleted == []
    assert manager.stats['incoming_observed'] == 1
    incoming_plain = make_message('بدون ایموجی', msg_id=26, out=False)
    assert run(manager.handle_incoming(incoming_plain)) == 'skipped'


def test_incoming_with_custom_entity_observed_not_resent():
    manager, client, _ = make_manager()
    custom = types.MessageEntityCustomEmoji(offset=0, length=2,
                                            document_id=FIRE)
    incoming = make_message('🔥', msg_id=27, out=False, entities=[custom])
    assert run(manager.handle_incoming(incoming)) == 'observed'
    assert client.sends == [] and client.deleted == []


def test_reply_markup_is_cloned_when_present():
    manager, client, _ = make_manager()
    markup = types.ReplyInlineMarkup([types.KeyboardButtonRow([
        types.KeyboardButtonCallback('ok', b'data')])])
    message = make_message('🔥 دکمه‌دار', msg_id=35, reply_markup=markup)
    assert run(manager.handle_outgoing(message)) == 'handled'
    assert client.sends[0][3].get('buttons') == markup


def test_no_reply_markup_kwarg_when_absent():
    manager, client, _ = make_manager()
    message = make_message('🔥 ساده', msg_id=36)
    assert run(manager.handle_outgoing(message)) == 'handled'
    assert 'buttons' not in client.sends[0][3]


def test_premium_resend_block_exact_format():
    block = tlog.format_premium_resend_block(
        chat_id=-1001234567890, message_id=10, emoji_detected=True,
        emoji_type='custom', media_type='عکس', resend_success=True)
    lines = block.split('\n')
    assert lines[0] == '[PREMIUM_RESEND]'
    assert 'chat_id=-1001234567890' in lines
    assert 'message_id=10' in lines
    assert 'emoji_detected=true' in lines
    assert 'emoji_type=custom' in lines
    assert 'media_type=عکس' in lines
    assert 'direction=outgoing' in lines
    assert 'resend_success=true' in lines


def test_premium_resend_block_incoming_and_note():
    block = tlog.format_premium_resend_block(
        chat_id=1, message_id=2, emoji_detected=False, emoji_type='none',
        resend_success=False, direction='incoming', note='observe only')
    assert 'direction=incoming' in block.split('\n')
    assert 'resend_success=false' in block.split('\n')
    assert 'note=observe only' in block.split('\n')


def test_service_identity_and_processed_cache():
    """کلاس سرویس رسمی همان مدیر قدیمی است (بدون منطق تکراری)."""
    from services import emoji_resend_manager as shim
    assert shim.EmojiResendManager is svc.PremiumResendService
    assert shim.install_emoji_resend_manager is svc.install_premium_resend_service
    manager, client, _ = make_manager()
    manager._mark_recent(CHAT_ID, 999)
    assert (CHAT_ID, 999) in manager.processed_message_ids
    manager.reset_recent()
    assert manager.processed_message_ids == set()
