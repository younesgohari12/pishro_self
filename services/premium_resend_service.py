# -*- coding: utf-8 -*-
"""Premium Resend Service — سرویس مستقل ارسال دوباره ایموجی ویژه (v0.09.18).

ماژول رسمی و «تک‌منبع حقیقت» سرویس «ارسال دوباره ایموجی ویژه» در PishroSelf
(طبق spec مالک: ``services/premium_resend_service.py``). نسخه‌های قبلی این
منطق را در ``services/emoji_resend_manager.py`` داشتند؛ از v0.09.18 آن فایل
فقط شیم سازگاری است و همه نمادها را از اینجا بازنشر می‌کند.

هرگونه منطق Edit ممنوع است: در این ماژول و در کل جریان Premium هیچ
EditMessageRequest و هیچ client.edit_message ای صدا زده نمی‌شود.

Flow دقیق و الزامی (spec مالک v0.09.18) برای هر پیام خروجی وقتی
«ایموجی ویژه» و «ارسال دوباره» فعال‌اند:

    1) پیام معمولی ارسال می‌شود.
    2) تشخیص جامع ایموجی — فقط روی متن نیست:
           message.message / raw_text / entities / caption (همان متن مدیا)
           / attributes مدیا (DocumentAttributeCustomEmoji)
           / MessageEntityCustomEmoji
       انواع: یونیکد، Premium، Custom، Entity، داخل متن، داخل کپشن.
    3) کپی کامل محتوا در حافظه: متن، formatting، entities (شامل custom)،
       مدیا، کپشن، reply_to، silent و reply_markup.
    4) ارسال نسخه جدید — «قبل از هر حذفی».
    5) فقط بعد از موفقیتِ ارسال: حذف پیام اصلی.
       - ارسال شکست بخورد → پیام اصلی دست‌نخورده می‌ماند + خطا log +
         retry محدود (SEND_ATTEMPTS + یک تلاش نجات بدون entity).
       - حذف بعد از ارسال شکست بخورد → خطا log می‌شود (دو نسخه موقتاً در
         چت)؛ محتوا هرگز گم نمی‌شود.

پوشش کامل انواع پیام:
    متن، Reply، عکس، ویدیو، فایل، صدا، ویس، استیکر، video_note، گیف/
    انیمیشن و آلبوم (media group) — آلبوم یک‌بار و یک‌جا حذف/ارسال می‌شود.
    همه چت‌ها: Saved، خصوصی، گروه، سوپرگروه، کانال — از طریق هندلر outgoing
    کانورتر (events.NewMessage(outgoing=True)) که فقط این سرویس را صدا می‌زند.

پیام‌های دریافتی (incoming):
    ناظر مستقل این سرویس (events.NewMessage(incoming=True)) پیام‌های دریافتی
    را «فقط شناسایی و گزارش» می‌کند؛ هیچ delete/send ای روی پیام دیگران
    انجام نمی‌شود (اقدام مخرب فقط برای پیام‌های خود اکانت مجاز است).

قواعد امنیتی:
- پیام بدون ایموجی قابل‌تشخیص → هیچ کاری انجام نمی‌شود.
- پیام دارای Entity/مدیا ویژه (از قبل Premium) → بدون تغییر (idempotency).
- پیام‌های forward، via_bot (پنل اینلاین) و اکشن‌ها → دست‌نخورده.
- ضد race: قفل per-chat + بازبینی حافظه داخل قفل.
- ضد loop: cache پردازش‌شده‌ها (chat_id, message_id) با TTL + پیام‌های خود
  ما + گزارش‌های خودِ ما + cooldown کانورتر در شرایط strip.
- کنترل flood: SEND_ATTEMPTS + SEND_RETRY_DELAY_SECONDS +
  STRIP_COOLDOWN_SECONDS + ALBUM_FLUSH_DELAY_SECONDS + یک تلاش برای هر پیام.

لاگ الزامی (spec مالک):
    [بررسی ایموجی ویژه] / [ارسال دوباره] (فارسی) + [PREMIUM_RESEND] (انگلیسی،
    فیلدهای chat_id / message_id / emoji_detected / emoji_type / media_type /
    direction / resend_success).
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict

import config

from services import away_bypass
from services import telegram_logger as tlog
from services.custom_emoji_service import EMOJI_PATTERN
from services.premium_emoji_converter import (
    _chat_kind,
    _new_custom_entities,
)

# فاصله جمع‌آوری قطعات آلبوم: آخرین قطعه پس از این سکوت، آلبوم کامل فرض
# می‌شود. کوتاه نگه داشته شده تا ارسال/حذف مجدد سریع بماند.
ALBUM_FLUSH_DELAY_SECONDS = 1.0
MAX_ALBUM_PARTS = 20
# حافظه پیام‌های خودمان (loop guard) — (chat_id, message_id)
RECENT_SENT_LIMIT = 512
# TTL حافظه ضد-loop (spec مالک: cache با TTL روی message_id + chat_id)
PROCESSED_TTL_SECONDS = 3600
# حافظه پیام‌های گزارش Debug — روی این‌ها هرگز واکنش نمی‌کنیم
IGNORED_LIMIT = 256
# سرور entity را حذف کند: بعد از ۲ بار پیاپی، این مدت تلاش متوقف می‌ماند.
STRIP_COOLDOWN_SECONDS = 300
# بعد از send_message این‌قدر صبر، سپس پیام از تلگرام واکشی و Entity
# نسخهٔ واقعی سرور بررسی می‌شود؛ تصمیم فقط بر اساس نمای سرور است.
VERIFY_DELAY_SECONDS = 0.5
# تلاش‌های ارسال نسخه جدید (retry محدود؛ پیام اصلی تا موفقیت حفظ می‌شود)
SEND_ATTEMPTS = 3
SEND_RETRY_DELAY_SECONDS = 0.4
CUSTOM_ENTITY_NAME = 'MessageEntityCustomEmoji'


def _has_custom_entity(value):
    """True اگر در ساختار entity ها (شامل لیستِ لیستِ آلبوم) Custom باشد."""
    if isinstance(value, (list, tuple)):
        return any(_has_custom_entity(item) for item in value)
    return type(value).__name__ == CUSTOM_ENTITY_NAME


def _is_custom(entity):
    return type(entity).__name__ == CUSTOM_ENTITY_NAME


def _media_type(message):
    """نوع مدیا برای فیلد «نوع پیام» بلوک [بررسی ایموجی ویژه]."""
    media = getattr(message, 'media', None)
    if media is None:
        return 'متن'
    name = type(media).__name__
    if name == 'MessageMediaPhoto':
        return 'عکس'
    if name == 'MessageMediaDocument':
        document = getattr(media, 'document', None)
        attributes = list(getattr(document, 'attributes', None) or [])
        kinds = {type(attribute).__name__ for attribute in attributes}
        if 'DocumentAttributeSticker' in kinds:
            return 'استیکر'
        if 'DocumentAttributeAnimated' in kinds:
            return 'گیف'
        if 'DocumentAttributeVideo' in kinds:
            video = next((a for a in attributes
                          if type(a).__name__ == 'DocumentAttributeVideo'), None)
            if video is not None and getattr(video, 'round_message', False):
                return 'ویدیو نوت'
            return 'ویدیو'
        if 'DocumentAttributeAudio' in kinds:
            audio = next((a for a in attributes
                          if type(a).__name__ == 'DocumentAttributeAudio'), None)
            if audio is not None and getattr(audio, 'voice', False):
                return 'ویس'
            return 'فایل صوتی'
        return 'فایل'
    if name == 'MessageMediaWebPage':
        return 'وب‌پیج'
    return name.replace('MessageMedia', '').lower() or 'مدیا'


def _describe_entities(entities):
    """نمایش فشرده entity ها برای فیلد «Entity دارد» بلوک."""
    entities = list(entities or [])
    if not entities:
        return 'هیچ'
    parts = []
    for entity in entities[:8]:
        name = type(entity).__name__
        offset = getattr(entity, 'offset', '?')
        length = getattr(entity, 'length', '?')
        if name == CUSTOM_ENTITY_NAME:
            parts.append('ویژه('
                         f'doc={getattr(entity, "document_id", "?")}@{offset}+{length})')
        else:
            parts.append(f'{name.replace("MessageEntity", "").lower()}'
                         f'@{offset}+{length}')
    extra = f' (+{len(entities) - 8} بیشتر)' if len(entities) > 8 else ''
    return f'{len(entities)}: ' + ', '.join(parts) + extra


def _custom_emoji_in_media(message):
    """تشخیص Custom Emoji در attributes مدیا (استیکر/ایموجی ویژه سندی)."""
    media = getattr(message, 'media', None)
    if media is None:
        return False
    document = getattr(media, 'document', None)
    for attribute in list(getattr(document, 'attributes', None) or []):
        if type(attribute).__name__ == 'DocumentAttributeCustomEmoji':
            return True
    return False


def _detect_emoji(message):
    """تشخیص جامع ایموجی — root-cause spec مالک v0.09.18.

    فقط روی متن نیست؛ این منابع بررسی می‌شوند:
        - message.message (متن یا کپشن مدیا)
        - message.raw_text (نمای خام Telethon)
        - message.entities (شامل MessageEntityCustomEmoji)
        - attributes مدیا (DocumentAttributeCustomEmoji)

    خروجی dict با کلیدهای:
        has_emoji / kind ('unicode'|'custom'|'none') / unicode / custom /
        in_caption / media_type
    """
    haystacks = []
    for value in (getattr(message, 'message', None),
                  getattr(message, 'raw_text', None)):
        if isinstance(value, str) and value:
            if value not in haystacks:
                haystacks.append(value)
    unicode_emoji = any(EMOJI_PATTERN.search(text) for text in haystacks)
    entities = list(getattr(message, 'entities', None) or [])
    custom_entity = any(_is_custom(entity) for entity in entities)
    custom_media = _custom_emoji_in_media(message)
    custom = bool(custom_entity or custom_media)
    has_emoji = bool(unicode_emoji or custom)
    if custom:
        kind = 'custom'
    elif unicode_emoji:
        kind = 'unicode'
    else:
        kind = 'none'
    has_media = getattr(message, 'media', None) is not None
    return {
        'has_emoji': has_emoji,
        'kind': kind,
        'unicode': bool(unicode_emoji),
        'custom': custom,
        'in_caption': bool(unicode_emoji and has_media),
        'media_type': _media_type(message),
    }


class PremiumResendService:
    """سرویس مستقل ارسال دوباره ایموجی ویژه با تأیید واقعی سرور.

    مسئولیت‌ها (spec مالک): detect emoji، detect custom emoji، clone message،
    resend، safe delete، anti loop، flood control.

    در این کلاس هیچ متدی edit_message یا EditMessageRequest را صدا نمی‌زند؛
    تنها ابزارهای مجاز: client.get_messages، client.delete_messages و
    client.send_message / client.send_file.

    ترتیب الزامی v0.09.18: کپی کامل → ارسال نسخه جدید → بعد از موفقیتِ
    ارسال، حذف پیام اصلی (هیچ حذفی قبل از موفقیتِ ارسال انجام نمی‌شود).
    """

    def __init__(self, client, engine, *, is_enabled=None, owner_id=None):
        self.client = client
        self.engine = engine
        self.is_enabled = is_enabled  # optional callable -> None/True/False
        self.owner_id = owner_id
        self.debug_reports = False  # `.بررسی_ایموجی روشن` — گزارش زنده در Saved
        self._locks = {}
        self._recent = OrderedDict()  # (chat_id, msg_id) -> monotonic time
        self._ignore = OrderedDict()  # پیام‌های گزارش خودمان؛ بدون واکنش
        self._albums = {}  # (chat_id, grouped_id) -> {'parts': [], 'handle': ...}
        self._tasks = set()  # task های flush زنده (برای uninstall تمیز)
        self._strip_streak = {}  # chat_id -> تعداد پیام‌های خودمان بدون entity
        self._incoming_handler = None
        self.stats = {'resent': 0, 'kept': 0, 'deleted': 0, 'failed': 0,
                      'incoming_observed': 0}

    # ------------------------------------------------------------- gating
    def resend_enabled(self):
        """زنجیره فعال‌سازی: کلید اصلی → کانورتر → config → پنل حساب.

        1) موتور کانورتر فعال نباشد (کلید اصلی/کانال پنل/cooldown) → هیچ‌وقت.
        2) config.PREMIUM_EMOJI_RESEND_MODE کلید سخت انتشار است.
        3) انتخاب صریح پنل (True/False) اولویت دارد؛ None یعنی پیش‌فرض روشن.
        """
        if not getattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True):
            return False
        if not self.engine.effective_enabled():
            return False
        if self.is_enabled is not None:
            try:
                choice = self.is_enabled()
            except Exception:  # noqa: BLE001 - دیتابیس هرگز مسیر را نشکند
                choice = None
            if choice is not None:
                return bool(choice)
        return True

    # -------------------------------------------------------- loop guards
    def _mark_recent(self, chat_id, message_id):
        try:
            key = (int(chat_id), int(message_id))
        except (TypeError, ValueError):
            return
        self._recent[key] = time.monotonic()
        self._recent.move_to_end(key)
        while len(self._recent) > RECENT_SENT_LIMIT:
            self._recent.popitem(last=False)

    def _is_recent(self, message):
        try:
            key = (int(getattr(message, 'chat_id', 0) or 0),
                   int(getattr(message, 'id', 0) or 0))
        except (TypeError, ValueError):
            return False
        stamp = self._recent.get(key)
        if stamp is None:
            return False
        # TTL حافظه ضد-loop: بعد از PROCESSED_TTL_SECONDS خط می‌خورد.
        if time.monotonic() - stamp > PROCESSED_TTL_SECONDS:
            self._recent.pop(key, None)
            return False
        return True

    @property
    def processed_message_ids(self):
        """شناسه‌های پردازش‌شده (chat_id, message_id) — cache ضد loop با TTL."""
        return set(self._recent.keys())

    def reset_recent(self):
        self._recent.clear()

    def _mark_ignored(self, sent):
        """پیام گزارش Debug خودمان → هرگز رویش [بررسی ایموجی ویژه] نمی‌زنیم."""
        try:
            key = (int(getattr(sent, 'chat_id', 0) or 0),
                   int(getattr(sent, 'id', 0) or 0))
        except (TypeError, ValueError):
            return
        self._ignore[key] = time.monotonic()
        self._ignore.move_to_end(key)
        while len(self._ignore) > IGNORED_LIMIT:
            self._ignore.popitem(last=False)

    def _is_ignored(self, message):
        try:
            key = (int(getattr(message, 'chat_id', 0) or 0),
                   int(getattr(message, 'id', 0) or 0))
        except (TypeError, ValueError):
            return False
        stamp = self._ignore.get(key)
        if stamp is None:
            return False
        if time.monotonic() - stamp > PROCESSED_TTL_SECONDS:
            self._ignore.pop(key, None)
            return False
        return True

    def _note_strip_condition(self, message):
        """پیام خودمان دوباره بدون entity رسید → سرور entity را حذف می‌کند.

        بعد از دو بار پیاپی در یک چت، موتور کانورتر به cooldown می‌رود تا
        هیچ پیامی بی‌دلیل دوبل نشود (ارسال/حذف مجدد سریع می‌ماند).
        """
        chat_id = getattr(message, 'chat_id', None)
        try:
            streak = self._strip_streak.get(chat_id, 0) + 1
            self._strip_streak[chat_id] = streak
        except TypeError:
            return
        if streak >= 2:
            self._strip_streak[chat_id] = 0
            self.engine.disabled_until = (
                time.monotonic() + STRIP_COOLDOWN_SECONDS)
            tlog.send_premium_event(
                '⚠️ ایموجی ویژه — سرور entity را حذف می‌کند',
                {
                    'دلیل': 'تلگرام در این چت entity ایموجی ویژه را حذف می‌کند',
                    'اقدام': 'ارسال دوباره موقتاً متوقف شد (cooldown)',
                    'مدت': f'{STRIP_COOLDOWN_SECONDS}s',
                    'چت': chat_id,
                    'روش': 'ارسال دوباره ایموجی ویژه',
                },
                level='WARNING', kind='fallback')

    def _lock_for(self, chat_id):
        lock = self._locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[chat_id] = lock
        return lock

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _chat_label(message):
        chat_id = getattr(message, 'chat_id', None)
        try:
            kind = _chat_kind(PremiumResendService._input_chat(message)
                              or chat_id)
        except Exception:  # noqa: BLE001
            kind = 'نامشخص'
        return f'{kind} ({chat_id})'

    @staticmethod
    def _reply_to_of(message):
        reply = getattr(message, 'reply_to', None)
        reply_id = getattr(reply, 'reply_to_msg_id', None) if reply else None
        return reply_id if reply_id else getattr(message, 'reply_to_msg_id',
                                                 None)

    @staticmethod
    def _input_chat(message):
        """input_chat امن؛ بدون کلاینت متصل ValueError می‌دهد نه None."""
        try:
            return getattr(message, 'input_chat', None)
        except (ValueError, AttributeError, TypeError):
            return None

    # --------------------------------------- [بررسی ایموجی ویژه] (Debug)
    def _emit_check(self, message, *, has_custom=None, server_note=None,
                    media_type=None, message_id=None, result=None):
        """بلوک [بررسی ایموجی ویژه] — مشاهده هر پیام قبل از تصمیم."""
        try:
            if has_custom is None:
                existing = list(getattr(message, 'entities', None) or [])
                has_custom = any(_is_custom(item) for item in existing)
            entity_desc = _describe_entities(
                getattr(message, 'entities', None))
            if result is None:
                result = 'Entity سالم است؛ بدون تغییر' if has_custom \
                    else (server_note or 'بررسی شد')
            elif server_note:
                result = f'{result} | {server_note}'
            tlog.send_premium_check(tlog.format_premium_check(
                chat_id=getattr(message, 'chat_id', None),
                message_id=(message_id if message_id is not None
                            else getattr(message, 'id', None)),
                has_entity=has_custom,
                entities=entity_desc,
                media_type=(media_type if media_type is not None
                            else _media_type(message)),
                reply_to=self._reply_to_of(message),
                result=result),
                chat_id=getattr(message, 'chat_id', None))
        except Exception:  # noqa: BLE001 - لاگ هرگز مسیر را نمی‌شکند
            pass

    def _emit_resend_block(self, message, info, *, resend_success,
                           direction='outgoing', note=None):
        """[PREMIUM_RESEND] — بلوک انگلیسی استاندارد spec مالک v0.09.18."""
        try:
            if info is None:
                info = _detect_emoji(message)
            tlog.send_premium_resend_block(tlog.format_premium_resend_block(
                chat_id=getattr(message, 'chat_id', None),
                message_id=getattr(message, 'id', None),
                emoji_detected=bool(info.get('has_emoji')),
                emoji_type=info.get('kind', 'none'),
                media_type=info.get('media_type', 'متن'),
                direction=direction,
                resend_success=resend_success,
                note=note),
                chat_id=getattr(message, 'chat_id', None))
        except Exception:  # noqa: BLE001 - لاگ هرگز مسیر را نمی‌شکند
            pass

    def _emit_trace(self, message, *, is_away, entity_check,
                    delete_called=False, new_send_called=False):
        """[PREMIUM_TRACE] — Audit؛ هرگز مسیر را نمی‌شکند."""
        try:
            tlog.send_premium_trace(tlog.format_premium_trace(
                message_id=getattr(message, 'id', None),
                is_away=is_away,
                entity_check=entity_check,
                delete_called=delete_called,
                new_send_called=new_send_called),
                chat_id=getattr(message, 'chat_id', None))
        except Exception:  # noqa: BLE001 - ثبت هرگز مسیر را نمی‌شکند
            pass

    def _log_resend(self, message, *, deleted, resent, new_message_id=None,
                    reason=None):
        """بلوک [ارسال دوباره] — نتیجه ارسال جدید/حذف (فارسی)."""
        try:
            tlog.send_premium_resend_debug(tlog.format_premium_resend_debug(
                chat=self._chat_label(message),
                message_id=getattr(message, 'id', None),
                deleted=deleted,
                resent=resent,
                new_message_id=new_message_id,
                reason=reason))
        except Exception:  # noqa: BLE001 - لاگ هرگز مسیر را نمی‌شکند
            pass

    async def _fetch_from_server(self, message):
        """واکشی مجدد پیام از تلگرام — بررسی Entity «واقعی» داخل پیام.

        خروجی ``(status, message, note)``:
        - ``('ok', Message, '…')`` — نمای واقعی سرور
        - ``('missing', None, 'پیام روی سرور پیدا نشد')``
        - ``('error', None, 'واکشی ناموفق (…) → نمای event')``
          در خطای واکشی، تصمیم با نمای event ادامه می‌یابد.
        """
        chat_id = getattr(message, 'chat_id', None)
        message_id = getattr(message, 'id', None)
        if chat_id is None or message_id is None:
            return 'error', None, 'واکشی ممکن نبود (بدون چت/شناسه) → نمای event'
        try:
            fetched = await self.client.get_messages(chat_id, ids=message_id)
        except Exception as exc:  # noqa: BLE001 - خطای واکشی = تصمیم با event
            return ('error', None,
                    f'واکشی ناموفق ({type(exc).__name__}) → نمای event')
        if fetched is None:
            return 'missing', None, 'پیام روی سرور پیدا نشد'
        has_entity = _has_custom_entity(getattr(fetched, 'entities', None))
        return 'ok', fetched, f'Entity دارد: {has_entity} (نمای سرور)'

    # ------------------------------------------------------ debug reports
    def set_debug_reports(self, enabled):
        """حالت بررسی (`.بررسی_ایموجی روشن`) — گزارش زنده در Saved Messages."""
        self.debug_reports = bool(enabled)
        return self.debug_reports

    async def _debug_report(self, text):
        """گزارش زنده در Saved Messages؛ بدون تبدیل و بدون واکنش به خودش."""
        if not self.debug_reports or not text:
            return False
        try:
            token = self.engine.bypass.set(True)
            try:
                sent = await self.client.send_message('me', text,
                                                      parse_mode=None,
                                                      link_preview=False)
            finally:
                self.engine.bypass.reset(token)
        except Exception:  # noqa: BLE001 - گزارش هرگز مسیر را نمی‌شکند
            return False
        self._mark_ignored(sent)
        return True

    def _report_head(self, message):
        return (f'شناسه چت: {getattr(message, "chat_id", None)} | '
                f'شناسه پیام: {getattr(message, "id", None)} | '
                f'نوع پیام: {_media_type(message)} | '
                f'پاسخ به: {self._reply_to_of(message) or "ندارد"}')

    # ------------------------------------------------- entry from injector
    async def handle_outgoing(self, message):
        """نقطه ورود پیام‌های خروجی از هندلر outgoing کانورتر.

        خروجی:
        - 'handled'   → این سرویس مالک پیام است (ارسال/حذف شد، بافر شد یا
                        پیام خودِ ماست).
        - 'skipped'   → این پیام موضوع این سرویس نیست؛ هیچ اقدامی مجاز نیست.
        """
        if message is None or getattr(message, 'action', None) is not None:
            return 'skipped'
        # 💤 AWAY_BYPASS_PREMIUM — پاسخ عدم حضور هرگز وارد جریان ارسال
        # دوباره نمی‌شود: بدون بررسی سرور، بدون حذف، بدون ارسال جدید.
        if away_bypass.is_away_reply(message):
            away_bypass.note_resend_away_skip(message)
            self._emit_trace(
                message, is_away=True,
                entity_check='انجام نشد (پاسخ عدم حضور — بدون بررسی سرور)',
                delete_called=False, new_send_called=False)
            return 'skipped'
        if getattr(message, 'fwd_from', None) is not None:
            return 'skipped'  # forward ها طبق قانون هرگز دست‌نخورده می‌مانند
        if getattr(message, 'via_bot_id', None):
            return 'skipped'  # پیام‌های پنل اینلاین
        if self._is_ignored(message):
            return 'handled'  # گزارش‌های Debug خودمان
        active = self.resend_enabled()
        observe = active or self.debug_reports
        if getattr(message, 'grouped_id', None) is not None:
            if active and self._is_recent(message):
                # قطعه آلبومِ خودِ ما (نسخه جدید) — ضد loop آلبوم (v0.09.18)
                await self._observe_own_message(message)
                return 'handled'
            if observe:
                self._emit_check(message, server_note='قطعه آلبوم؛ صف شد'
                                 if active else 'قطعه آلبوم (ارسال دوباره خاموش)')
            if not active:
                return 'skipped'
            return await self._handle_album_part(message)
        if active and self._is_recent(message):
            # پیام خودِ ماست (نسخه جدید/گزارش)؛ مشاهده + شمارش strip
            await self._observe_own_message(message)
            return 'handled'
        if not observe:
            return 'skipped'  # خاموش → پیام همان‌طور که هست می‌ماند
        return await self._handle_single(message, allow_resend=active)

    async def handle_incoming(self, message):
        """پیام‌های دریافتی: فقط شناسایی و مشاهده — هرگز delete/send ممنوع.

        طبق spec مالک همه پیام‌های ارسالی «و دریافتی» بررسی می‌شوند؛ اما
        اقدام مخرب (حذف/ارسال مجدد) فقط برای پیام‌های خود اکانت مجاز است.
        پیام‌های دیگران فقط در بلوک [بررسی ایموجی ویژه]/[PREMIUM_RESEND]
        با direction=incoming ثبت می‌شوند.
        """
        if message is None or getattr(message, 'action', None) is not None:
            return 'skipped'
        if getattr(message, 'out', False):
            # محافظت مسیری: پیام خروجی به جریان اصلی برمی‌گردد
            return await self.handle_outgoing(message)
        if not (self.resend_enabled() or self.debug_reports):
            return 'skipped'  # خاموش → هیچ تغییری و هیچ گزارشی
        info = _detect_emoji(message)
        self._emit_check(message, has_custom=info['custom'],
                         media_type=info['media_type'],
                         result=('پیام دریافتی؛ Entity ویژه دارد'
                                 if info['custom'] else
                                 'پیام دریافتی؛ ایموجی معمولی (فقط مشاهده)'))
        self._emit_resend_block(message, info, resend_success=False,
                                direction='incoming',
                                note='incoming — observe only (no action)')
        if info['has_emoji']:
            self.stats['incoming_observed'] += 1
            return 'observed'
        return 'skipped'

    # ------------------------------------------------- own-message observe
    async def _observe_own_message(self, message):
        """پیام خودِ ما دوباره رسید (loop guard) — مشاهده + شمارش strip."""
        info = _detect_emoji(message)
        existing = list(getattr(message, 'entities', None) or [])
        has_custom = any(_is_custom(item) for item in existing) or info['custom']
        relevant = info['has_emoji'] or getattr(message, 'media', None) is not None
        if not relevant:
            return
        server_note = None
        if not has_custom and info['unicode'] and self.debug_reports:
            await asyncio.sleep(VERIFY_DELAY_SECONDS)
            _status, _fetched, server_note = await self._fetch_from_server(
                message)
            await self._debug_report(
                '🔧 بررسی ایموجی ویژه — نسخه ارسال‌شده جدید\n'
                f'{self._report_head(message)}\n'
                f'Entity در event: {has_custom}\n'
                f'سرور: {server_note}')
        self._emit_check(message, has_custom=has_custom,
                         server_note=server_note)
        if not has_custom and info['unicode']:
            self._note_strip_condition(message)

    # ------------------------------------------------------------ single
    def _conversion(self, message):
        """تبدیل «یک‌بار» برای هر پیام؛ خروجی (text, merged, added) یا None.

        ⚠️ مهم: هر فراخوانی convert در حالت round_robin انتخاب بعدی استخر
        را مصرف می‌کند؛ بنابراین نتیجه این تابع باید مستقیم برای ارسال
        استفاده شود (بدون تبدیل دوباره) تا شناسه‌های چک و ارسال یکی باشند.
        """
        text = getattr(message, 'message', None)
        if not isinstance(text, str) or not text:
            return None
        existing = list(getattr(message, 'entities', None) or [])
        if any(_is_custom(entity) for entity in existing):
            return None  # همین حالا ایموجی ویژه است؛ idempotency
        try:
            _, merged = self.engine.convert(text, existing)
        except Exception:  # noqa: BLE001 - تشخیص هرگز مسیر را نمی‌شکند
            return None
        added = _new_custom_entities(merged, existing)
        if not added:
            return None  # هیچ نگاشت دقیقی نیست؛ پیام اصلی همان است
        return text, merged, added

    async def _handle_single(self, message, *, allow_resend):
        """یک پیام غیرآلبومی: تشخیص جامع → بررسی سرور → کپی → ارسال → حذف."""
        info = _detect_emoji(message)
        if not info['has_emoji']:
            # بدون هیچ نوع ایموجی (متن/کپشن/entity/مدیا) هیچ کاری ممنوع.
            if self.debug_reports:
                self._emit_check(message, has_custom=False,
                                 result='ایموجی قابل‌تشخیص ندارد؛ بدون تغییر')
            self._emit_resend_block(message, info, resend_success=False,
                                    note='emoji_detected=false')
            self._emit_trace(message, is_away=False,
                             entity_check='انجام نشد (ایموجی قابل‌تشخیص ندارد)',
                             delete_called=False, new_send_called=False)
            return 'skipped'
        existing = list(getattr(message, 'entities', None) or [])
        has_custom = (any(_is_custom(entity) for entity in existing)
                      or info['custom'])
        if has_custom:
            self._emit_check(message, has_custom=True,
                             result='Entity/مدیا ویژه داخل پیام موجود است؛ بدون تغییر')
            self._emit_trace(message, is_away=False,
                             entity_check='Entity در event/مدیا موجود است',
                             delete_called=False, new_send_called=False)
            self._emit_resend_block(message, info, resend_success=False,
                                    note='already premium/custom')
            await self._debug_report(
                '🔧 بررسی ایموجی ویژه\n'
                f'{self._report_head(message)}\n'
                'نتیجه: Entity دارد → پیام پرمیوم است؛ بدون تغییر')
            return 'skipped'
        # ⏱ بررسی Entity واقعی: مکث کوتاه → واکشی نمای واقعی سرور
        await asyncio.sleep(VERIFY_DELAY_SECONDS)
        status, fetched, server_note = await self._fetch_from_server(message)
        base = fetched if status == 'ok' else message
        server_entity = (_has_custom_entity(getattr(fetched, 'entities', None))
                         if status == 'ok' else None)
        if status == 'ok' and server_entity:
            self._emit_check(message, has_custom=True,
                             server_note=server_note,
                             result='Entity واقعی روی سرور موجود است؛ بدون تغییر')
            self._emit_trace(message, is_away=False,
                             entity_check='انجام شد (نمای سرور) — Entity موجود',
                             delete_called=False, new_send_called=False)
            self._emit_resend_block(message, info, resend_success=False,
                                    note='server has custom entity')
            await self._debug_report(
                '🔧 بررسی ایموجی ویژه\n'
                f'{self._report_head(message)}\n'
                f'سرور: {server_note}\n'
                'نتیجه: Entity سالم است؛ ارسال دوباره لازم نیست')
            return 'skipped'
        converted = None
        if status == 'missing':
            self._emit_check(message, has_custom=False,
                             server_note=server_note,
                             result='پیام روی سرور نیست؛ کاری انجام نشد')
            self._emit_trace(message, is_away=False,
                             entity_check='انجام شد (نمای سرور) — پیام غایب',
                             delete_called=False, new_send_called=False)
            self._emit_resend_block(message, info, resend_success=False,
                                    note='message missing on server')
            await self._debug_report(
                '🔧 بررسی ایموجی ویژه\n'
                f'{self._report_head(message)}\n'
                f'سرور: {server_note} → پیام روی سرور نیست؛ کاری انجام نشد')
            return 'skipped'
        if allow_resend:
            converted = self._conversion(base)
            if converted is None:
                self._emit_check(message, has_custom=False,
                                 server_note=server_note,
                                 result='نگاشت ایموجی ویژه پیدا نشد؛ پیام حفظ شد')
                self._emit_trace(message, is_away=False,
                                 entity_check='انجام شد (نمای سرور) — نگاشت پیدا نشد',
                                 delete_called=False, new_send_called=False)
                self._emit_resend_block(message, info, resend_success=False,
                                        note='no premium mapping for emoji')
                return 'skipped'
        else:
            self._emit_check(message, has_custom=False,
                             server_note=server_note,
                             result='ارسال دوباره خاموش است؛ پیام حفظ شد')
            self._emit_trace(message, is_away=False,
                             entity_check='انجام شد (نمای سرور) — ارسال دوباره خاموش',
                             delete_called=False, new_send_called=False)
            self._emit_resend_block(message, info, resend_success=False,
                                    note='resend disabled')
            return 'skipped'
        _text, merged, _added = converted
        chat_id = getattr(message, 'chat_id', None)
        async with self._lock_for(chat_id or 0):
            # بعد از انتظار قفل: پیام خودِ ما نباشد (مسیر دیگری نفرستاده باشد)
            if self._is_recent(message):
                return 'handled'
            sent = await self._clone_send_delete(base, _text, merged, info)
            if sent is None:
                return 'skipped'  # ارسال ناموفق؛ پیام اصلی حفظ شد
            return 'handled'

    async def _clone_send_delete(self, message, text, merged, info=None):
        """ترتیب الزامی مالک (spec v0.09.18): کپی کامل → ارسال نسخه جدید →
        فقط بعد از موفقیتِ ارسال، حذف پیام اصلی.

        هیچ Edit ای در این مسیر وجود ندارد.
        - تا وقتی ارسال موفق نشده، پیام اصلی هرگز حذف نمی‌شود.
        - ارسال شکست بخورد → retry محدود + تلاش نجات بدون entity؛ اگر همه
          شکست خورد پیام اصلی باقی می‌ماند و خطا log می‌شود.
        - حذف بعد از ارسال شکست بخورد → خطا log (دو نسخه)؛ محتوا سالم است.
        """
        chat_id = getattr(message, 'chat_id', None)
        # ---- 1) Copy Content (کپی کامل در حافظه)
        copy = {
            'text': text,
            'entities': list(merged or []),
            'media': getattr(message, 'media', None),
            'reply_to': self._reply_to_of(message),
            'silent': bool(getattr(message, 'silent', False)),
            'reply_markup': getattr(message, 'reply_markup', None),
        }
        # ---- 2) Send New Message With Custom Emoji Entity (قبل از حذف)
        sent, with_entity = await self._send_copy(message, copy, chat_id)
        if sent is None:
            # همه تلاش‌های ارسال شکست خورد → پیام اصلی دست‌نخورده ماند
            self.stats['failed'] += 1
            self._emit_trace(message, is_away=False,
                             entity_check='انجام شد (نمای سرور)',
                             delete_called=False, new_send_called=False)
            self._log_resend(message, deleted=False, resent=False,
                             reason='ارسال نسخه جدید پس از چند تلاش ناموفق بود؛ '
                                    'پیام اصلی حفظ شد')
            self._emit_resend_block(message, info, resend_success=False,
                                    note='resend failed after retries; '
                                         'original kept')
            await self._debug_report(
                '🔧 بررسی ایموجی ویژه — شکست ارسال\n'
                f'{self._report_head(message)}\n'
                'ارسال نسخه جدید ناموفق بود → پیام اصلی حذف نشد و سالم ماند')
            return None
        self._mark_recent(getattr(sent, 'chat_id', chat_id),
                          getattr(sent, 'id', 0) or 0)
        # ---- 3) Delete Original Message (فقط بعد از موفقیتِ ارسال)
        deleted = await self._delete_original(message)
        if not deleted:
            self._log_resend(message, deleted=False, resent=True,
                             new_message_id=getattr(sent, 'id', None),
                             reason='حذف پیام اصلی بعد از ارسال ناموفق بود؛ '
                                    'موقتاً دو نسخه در چت است')
            self._emit_resend_block(message, info, resend_success=True,
                                    note='resent ok; original delete failed')
        else:
            self._log_resend(message, deleted=True, resent=True,
                             new_message_id=getattr(sent, 'id', None),
                             reason=None if with_entity
                             else 'نسخه جدید بدون entity ارسال شد (سرور حذف کرد؟)')
            self._emit_resend_block(message, info, resend_success=True)
        self.stats['resent'] += 1
        self._emit_trace(message, is_away=False,
                         entity_check='انجام شد (نمای سرور)',
                         delete_called=deleted, new_send_called=True)
        await self._debug_report(
            '🔧 بررسی ایموجی ویژه — ارسال جدید + حذف اصل کامل شد\n'
            f'{self._report_head(message)}\n'
            f'پیام جدید: msg={getattr(sent, "id", "?")} با '
            f'{len(copy["entities"])} entity | حذف اصل: '
            f'{"بله" if deleted else "خیر"}')
        return sent

    async def _send_copy(self, message, copy, chat_id):
        """ارسال نسخه کپی‌شده با تلاش مجدد؛ خروجی (sent, with_entity)."""
        for attempt in range(SEND_ATTEMPTS):
            try:
                sent = await self._send_once(message, copy, chat_id)
            except Exception as exc:  # noqa: BLE001 - محتوا نباید گم شود
                self._log_resend(message, deleted=False, resent=False,
                                 reason=f'تلاش ارسال {attempt + 1} ناموفق '
                                        f'({type(exc).__name__})')
                if attempt + 1 < SEND_ATTEMPTS:
                    await asyncio.sleep(SEND_RETRY_DELAY_SECONDS)
                continue
            if sent is None:
                continue
            has_entity = _has_custom_entity(getattr(sent, 'entities', None))
            return sent, has_entity
        # تلاش نهایی: بدون entity (ارسال ساده‌تر معمولاً موفق است)
        try:
            rescue = dict(copy)
            rescue['entities'] = []
            sent = await self._send_once(message, rescue, chat_id)
        except Exception:  # noqa: BLE001 - آخرین تلاش؛ دیگر کاری نمی‌توان کرد
            return None, False
        if sent is None:
            return None, False
        self._mark_recent(getattr(sent, 'chat_id', chat_id),
                          getattr(sent, 'id', 0) or 0)
        return sent, False

    async def _send_once(self, message, copy, chat_id):
        """یک تلاش ارسال دقیق (متن یا مدیا+کپشن) با entity های آماده."""
        chat = self._input_chat(message)
        if chat is None and chat_id is not None:
            chat = await self.client.get_input_entity(chat_id)
        common = {
            'parse_mode': None,
            'formatting_entities': copy['entities'] or None,
            'reply_to': copy['reply_to'],
            'silent': copy['silent'],
        }
        # reply markup فقط وقتی هست؛ نام پارامتر تلثون «buttons» است
        markup = copy.get('reply_markup')
        if markup is not None:
            common['buttons'] = markup
        if copy['media'] is not None:
            return await self.client.send_file(
                chat, copy['media'], caption=copy['text'], **common)
        return await self.client.send_message(chat, copy['text'], **common)

    async def _delete_original(self, message):
        """حذف پیام اصلی — فقط بعد از موفقیتِ ارسال فراخوانی می‌شود."""
        try:
            await self.client.delete_messages(
                getattr(message, 'chat_id', None), getattr(message, 'id', None))
            self.stats['deleted'] += 1
            return True
        except Exception as exc:  # noqa: BLE001 - پیام اصلی باید بماند
            self.stats['kept'] += 1
            self._log_resend(message, deleted=False, resent=True,
                             reason=f'حذف ناموفق ({type(exc).__name__})')
            return False

    # ------------------------------------------------------------- album
    async def _handle_album_part(self, message):
        """بافر کردن قطعات آلبوم و ارسال/حذف مجدد یک‌جا بعد از تکمیل."""
        key = (getattr(message, 'chat_id', 0), getattr(message, 'grouped_id', None))
        buffer = self._albums.get(key)
        if buffer is None:
            buffer = {'parts': [], 'handle': None}
            self._albums[key] = buffer
        if message not in buffer['parts']:
            buffer['parts'].append(message)
        if len(buffer['parts']) >= MAX_ALBUM_PARTS:
            self._flush_album_now(key)
            return 'handled'
        if buffer['handle'] is not None:
            buffer['handle'].cancel()
        loop = asyncio.get_running_loop()
        buffer['handle'] = loop.call_later(
            ALBUM_FLUSH_DELAY_SECONDS, self._flush_album_soon, key)
        return 'handled'

    def _flush_album_soon(self, key):
        """پل از تایمر event-loop به coroutine flush."""
        self._spawn(self._flush_album(key))

    def _spawn(self, coro):
        """ساخت task با ردیابی؛ برای uninstall تمیز و بدون تایمر یتیم."""
        try:
            task = asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            return  # بدون event loop فعال؛ بافر تا فراخوانی بعدی می‌ماند
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _flush_album_now(self, key):
        buffer = self._albums.pop(key, None)
        if buffer is None:
            return
        if buffer['handle'] is not None:
            buffer['handle'].cancel()
        self._spawn(self._resend_album_and_cleanup(key, buffer['parts']))

    async def _flush_album(self, key):
        buffer = self._albums.pop(key, None)
        if buffer is None:
            return
        await self._resend_album_and_cleanup(key, buffer['parts'])

    async def _resend_album_and_cleanup(self, key, parts):
        """بررسی سروری آلبوم و ارسال/حذف مجدد «کل آلبوم» فقط یک بار.

        بعد از تکمیل گروه → مکث کوتاه → همه قطعات از تلگرام واکشی می‌شوند
        → اگر هر قطعه entity نداشت و نگاشت موجود بود، کل آلبوم یک بار کپی،
        «آلبوم جدید ارسال» و بعد از موفقیتِ ارسال اصل‌ها حذف می‌شوند.
        """
        parts = [p for p in parts if p is not None]
        if not parts:
            return
        if not self.resend_enabled():
            return  # خاموش شدن در فاصله بافر؛ پیام‌ها دست‌نخورده می‌مانند
        # ⏱ بعد از آخرین قطعه → مکث کوتاه → واکشی نمای واقعی سرور
        await asyncio.sleep(VERIFY_DELAY_SECONDS)
        ordered = sorted(parts, key=lambda p: getattr(p, 'id', 0))
        bases = []
        notes = []
        any_missing = False
        for part in ordered:
            status, fetched, note = await self._fetch_from_server(part)
            part_id = getattr(part, 'id', '?')
            if status == 'ok':
                has_entity = _has_custom_entity(getattr(fetched, 'entities', None))
                if not has_entity:
                    any_missing = True
                notes.append(f'{part_id}: Entity دارد={has_entity}')
                bases.append(fetched)
            else:
                # واکشی ناموفق/پیام غایب → احتیاط: مسیر بررسی طی می‌شود
                any_missing = True
                notes.append(f'{part_id}: {note}')
                bases.append(part)
        conversions = None
        if any_missing:
            # تبدیل دقیقاً یک‌بار برای هر قطعه (نتیجه همان است که ارسال می‌شود)
            conversions = [self._conversion(base) for base in bases]
            if all(item is None for item in conversions):
                conversions = None  # هیچ نگاشتی نیست → آلبوم می‌ماند
        album_ids = ', '.join(str(getattr(base, 'id', '?')) for base in bases)
        album_media = f'آلبوم ({_media_type(bases[0])}×{len(bases)})'
        self._emit_check(
            bases[0], has_custom=not any_missing,
            media_type=album_media, message_id=album_ids,
            server_note='; '.join(notes),
            result=('ارسال دوباره کل آلبوم' if conversions is not None
                    else 'آلبوم حفظ شد (entity یا نگاشت موجود نیست)'))
        if conversions is None:
            self._emit_trace(bases[0], is_away=False,
                             entity_check='انجام شد (نمای سرور) — آلبوم حفظ شد',
                             delete_called=False, new_send_called=False)
            self._emit_resend_block(bases[0], None, resend_success=False,
                                    note='album kept (no mapping/entity)')
            return
        try:
            async with self._lock_for(key[0]):
                # ---- 1) Copy Content (کپی کامل همه قطعات در حافظه)
                copies = []
                for base, converted in zip(bases, conversions):
                    if converted is None:
                        copies.append({
                            'text': getattr(base, 'message', None) or '',
                            'entities': list(getattr(base, 'entities', None) or []),
                            'media': getattr(base, 'media', None),
                            'reply_to': self._reply_to_of(base),
                            'silent': bool(getattr(base, 'silent', False)),
                            'reply_markup': getattr(base, 'reply_markup', None),
                        })
                    else:
                        copies.append({
                            'text': converted[0],
                            'entities': list(converted[1] or []),
                            'media': getattr(base, 'media', None),
                            'reply_to': self._reply_to_of(base),
                            'silent': bool(getattr(base, 'silent', False)),
                            'reply_markup': getattr(base, 'reply_markup', None),
                        })
                # ---- 2) Send New Album (قبل از هر حذفی — spec v0.09.18)
                sent_list = await self._send_album_copy(bases, copies, key[0])
                if not sent_list:
                    self.stats['failed'] += 1
                    self._emit_trace(bases[0], is_away=False,
                                     entity_check='انجام شد (نمای سرور) — آلبوم',
                                     delete_called=False, new_send_called=False)
                    self._log_resend(bases[0], deleted=False, resent=False,
                                     reason='ارسال آلبوم جدید ناموفق بود؛ '
                                            'اصل‌ها حفظ شدند')
                    self._emit_resend_block(bases[0], None,
                                            resend_success=False,
                                            note='album resend failed; '
                                                 'originals kept')
                    await self._debug_report(
                        '🔧 بررسی ایموجی ویژه — شکست ارسال آلبوم\n'
                        f'{self._report_head(bases[0])}\n'
                        'ارسال آلبوم جدید ناموفق بود → اصل‌ها حذف نشدند')
                    return
                for sent in sent_list:
                    self._mark_recent(key[0], getattr(sent, 'id', 0) or 0)
                # ---- 3) Delete Original Messages (بعد از موفقیتِ ارسال)
                deleted = 0
                for base in bases:
                    if await self._delete_original(base):
                        deleted += 1
                if deleted != len(bases):
                    self._emit_trace(bases[0], is_away=False,
                                     entity_check='انجام شد (نمای سرور) — آلبوم',
                                     delete_called=bool(deleted),
                                     new_send_called=True)
                    self._log_resend(
                        bases[0], deleted=bool(deleted), resent=True,
                        new_message_id=', '.join(
                            str(getattr(s, 'id', '?')) for s in sent_list),
                        reason=f'{len(bases) - deleted} حذف ناموفق بود؛ '
                               'اصل‌های باقی‌مانده در چت هستند')
                    self._emit_resend_block(bases[0], None,
                                            resend_success=True,
                                            note='album resent; partial '
                                                 'delete failure')
                    await self._debug_report(
                        '🔧 بررسی ایموجی ویژه — آلبوم؛ حذف ناقص\n'
                        f'{self._report_head(bases[0])} | قطعات={len(bases)}\n'
                        'آلبوم جدید ارسال شد ولی برخی اصل‌ها حذف نشدند')
                else:
                    self._emit_trace(bases[0], is_away=False,
                                     entity_check='انجام شد (نمای سرور) — آلبوم',
                                     delete_called=True, new_send_called=True)
                    self._log_resend(
                        bases[0], deleted=True, resent=True,
                        new_message_id=', '.join(
                            str(getattr(s, 'id', '?')) for s in sent_list),
                        reason=None)
                    self._emit_resend_block(bases[0], None,
                                            resend_success=True)
                    await self._debug_report(
                        '🔧 بررسی ایموجی ویژه — آلبوم ارسال/حذف شد\n'
                        f'{self._report_head(bases[0])} | قطعات={len(bases)}\n'
                        'آلبوم جدید ارسال و همه اصل‌ها حذف شدند')
                self.stats['resent'] += 1
        except Exception as exc:  # noqa: BLE001 - گزارش خطا
            self.stats['failed'] += 1
            self._log_resend(bases[0], deleted=False, resent=False,
                             reason=f'ارسال مجدد آلبوم ناموفق '
                                    f'({type(exc).__name__})')
            self._emit_resend_block(bases[0], None, resend_success=False,
                                    note=f'album error: {type(exc).__name__}')
            await self._debug_report(
                '🔧 بررسی ایموجی ویژه — شکست آلبوم\n'
                f'{self._report_head(bases[0])}\n'
                f'خطا: {type(exc).__name__}')

    async def _send_album_copy(self, parts, copies, chat_id):
        """ارسال آلبوم کپی‌شده یک‌جا؛ خروجی لیست پیام‌های ارسالی یا []."""
        files = [copy['media'] for copy in copies]
        if any(f is None for f in files):
            return []
        captions = [copy['text'] for copy in copies]
        entity_lists = [list(copy['entities'] or []) for copy in copies]
        chat = self._input_chat(parts[0])
        if chat is None and chat_id is not None:
            chat = await self.client.get_input_entity(chat_id)
        reply_ids = {copy['reply_to'] for copy in copies} - {None}
        reply_to = reply_ids.pop() if len(reply_ids) == 1 else None
        sent_list = await self.client.send_file(
            chat, files, caption=captions,
            formatting_entities=entity_lists, parse_mode=None,
            reply_to=reply_to,
            silent=bool(getattr(parts[0], 'silent', False)),
            supports_streaming=True)
        if not isinstance(sent_list, (list, tuple)):
            sent_list = [sent_list]
        return list(sent_list)

    async def _resend_album(self, parts, conversions=None):
        """کپی دقیق آلبوم (مسیر تست/فراخوانی مستقیم): کپی → ارسال → حذف.

        ``conversions`` نتیجه _conversion هر قطعه است (بدون تبدیل دوباره)؛
        اگر داده نشود، همان‌جا تبدیل می‌شود. ترتیب همان الزامی v0.09.18 است:
        کپی → ارسال آلبوم جدید → بعد از موفقیتِ ارسال، حذف اصل‌ها.
        """
        pairs = sorted(zip(parts, conversions if conversions else [None] * len(parts)),
                       key=lambda pair: getattr(pair[0], 'id', 0))
        parts = [p for p, _ in pairs]
        if not parts:
            return []
        resolved = []
        for part, converted in pairs:
            if converted is None:
                converted = self._conversion(part)
            if converted is None:
                existing = list(getattr(part, 'entities', None) or [])
                resolved.append({'text': getattr(part, 'message', None) or '',
                                 'entities': existing,
                                 'media': getattr(part, 'media', None),
                                 'reply_to': self._reply_to_of(part),
                                 'silent': bool(getattr(part, 'silent', False)),
                                 'reply_markup': getattr(part, 'reply_markup', None)})
            else:
                resolved.append({'text': converted[0],
                                 'entities': list(converted[1] or []),
                                 'media': getattr(part, 'media', None),
                                 'reply_to': self._reply_to_of(part),
                                 'silent': bool(getattr(part, 'silent', False)),
                                 'reply_markup': getattr(part, 'reply_markup', None)})
        chat_id = getattr(parts[0], 'chat_id', None)
        sent_list = await self._send_album_copy(parts, resolved, chat_id)
        if not sent_list:
            return []
        for sent in sent_list:
            self._mark_recent(chat_id, getattr(sent, 'id', 0) or 0)
        for part in parts:
            await self._delete_original(part)
        return sent_list

    # ---------------------------------------------- incoming observer
    def _install_incoming_observer(self):
        """ناظر پیام‌های دریافتی — فقط شناسایی/گزارش؛ هرگز delete/send ممنوع."""
        if self._incoming_handler is not None:
            return
        on = getattr(self.client, 'on', None)
        if not callable(on):
            return  # کلاینت تقلبی/بدون سیستم رویداد — ناظر اختیاری است
        try:
            from telethon import events as _events

            @self.client.on(_events.NewMessage(incoming=True))
            async def _incoming_premium_observer(event):
                try:
                    await self.handle_incoming(getattr(event, 'message', None))
                except Exception:  # noqa: BLE001 - ناظر هرگز نمی‌شکند
                    return

            self._incoming_handler = _incoming_premium_observer
        except Exception:  # noqa: BLE001 - ثبت ناظر اختیاری است
            self._incoming_handler = None

    def _uninstall_incoming_observer(self):
        handler = self._incoming_handler
        if handler is None:
            return
        self._incoming_handler = None
        try:
            self.client.remove_event_handler(handler)
        except Exception:  # noqa: BLE001
            pass


# ============================================================ نصب سرویس
def install_premium_resend_service(client, engine, *, is_enabled=None,
                                   owner_id=None):
    """نصب سرویس ارسال دوباره ایموجی ویژه روی کلاینت Self (یک‌بار).

    - ناظر incoming (فقط مشاهده) هم ثبت می‌شود (روی کلاینت‌های واقعی).
    - هندلر outgoing کانورتر فقط این سرویس را صدا می‌زند (handle_outgoing).
    """
    if engine is None:
        return None  # کلاینت بات یا بدون Unified Pipeline: هرگز
    existing = getattr(client, '_premium_resend_manager', None)
    if existing is not None:
        return existing
    if getattr(client, '_premium_emoji_converter', None) is not engine:
        return None  # فقط کلاینت Self مجهز به Unified Pipeline
    manager = PremiumResendService(client, engine, is_enabled=is_enabled,
                                   owner_id=owner_id)
    client._premium_resend_manager = manager
    manager._install_incoming_observer()
    return manager


def uninstall_premium_resend_service(client):
    manager = getattr(client, '_premium_resend_manager', None)
    if manager is not None:
        manager._uninstall_incoming_observer()
        for task in list(getattr(manager, '_tasks', ())):
            task.cancel()
        for buffer in list(getattr(manager, '_albums', {}).values()):
            handle = buffer.get('handle')
            if handle is not None:
                handle.cancel()
        del client._premium_resend_manager


# نام‌های سازگاری (shim های قدیمی به این‌ها وابسته‌اند)
EmojiResendManager = PremiumResendService
install_emoji_resend_manager = install_premium_resend_service
uninstall_emoji_resend_manager = uninstall_premium_resend_service


def get_service(client):
    """نمونه نصب‌شده سرویس روی کلاینت Self را برمی‌گرداند (یا None)."""
    return getattr(client, '_premium_resend_manager', None)

__all__ = [
    'PremiumResendService', 'EmojiResendManager',
    'install_premium_resend_service', 'uninstall_premium_resend_service',
    'install_emoji_resend_manager', 'uninstall_emoji_resend_manager',
    'ALBUM_FLUSH_DELAY_SECONDS', 'MAX_ALBUM_PARTS', 'RECENT_SENT_LIMIT',
    'PROCESSED_TTL_SECONDS', 'IGNORED_LIMIT', 'STRIP_COOLDOWN_SECONDS',
    'VERIFY_DELAY_SECONDS', 'SEND_ATTEMPTS', 'SEND_RETRY_DELAY_SECONDS',
    'CUSTOM_ENTITY_NAME',
    '_has_custom_entity', '_is_custom', '_media_type',
    '_describe_entities', '_custom_emoji_in_media', '_detect_emoji',
]
