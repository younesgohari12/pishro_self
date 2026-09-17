"""Premium Resend Mode — ارسال مجدد هوشمند (Copy/Delete/Resend) + دور Debug.

روش تبدیل با Entity در بعضی چت‌ها و شرایط Telegram Server کار نمی‌کند (سرور
entity را بی‌صدا حذف می‌کند یا پیام از دستگاه دیگری رسیده و اصلاً از کانورتر
عبور نکرده). این ماژول لایه تأیید/ترمیم بعد از ارسال است:

    Send → [PREMIUM_CHECK] → sleep 0.5s → get_messages (نمای واقعی سرور)
         → Entity نبود؟ → کپی دقیق → ارسال با Custom Emoji → حذف پیام اصلی

🔴 دور Debug (نسخه PREMIUM_RESEND_CLOSE_AWAY → DEBUG):
- قبل از تصمیم Resend برای «هر» پیام خروجیِ مرتبط بلوک [PREMIUM_CHECK] با
  شش فیلد الزامی spec مالک (chat_id / message_id / has_entity / entities /
  media_type / reply_to) صادر می‌شود.
- بعد از send_message، ۰٫۵ ثانیه صبر و سپس پیام دوباره از تلگرام واکشی
  (client.get_messages) و Entity نسخهٔ واقعی سرور بررسی می‌شود؛ تصمیم
  Resend فقط بر اساس نمای سرور است، نه شیء محلی Telethon.
- آلبوم‌ها صف می‌شوند: قطعات گروه Media جمع، یک بار واکشی و «کل آلبوم یک
  بار» ارسال مجدد می‌شود (نه هر پیام جدا).
- `.premium debug on` گزارش زنده همهٔ مراحل را در Saved Messages می‌فرستد.

همه چت‌ها و انواع پیام پشتیبانی می‌شوند: Saved Messages، Private، Group،
Channel (با داشتن اجازه)، Reply، Photo/Video/Document Caption و آلبوم.

قواعد امنیتی (مطابق spec مالک):
- پیام اصلی فقط «بعد از» ارسال موفق نسخه جدید حذف می‌شود؛ اگر ارسال مجدد یا
  حذف شکست بخورد، پیام اصلی دست‌نخورده می‌ماند (هرگز پیام گم نمی‌شود).
- کپی دقیق است: متن، Entities/Markdown، Reply To، عکس، ویدیو، فایل، کپشن،
  آلبوم، silent و noforwards حفظ می‌شود. هیچ ایموجی ثابتی اضافه نمی‌شود؛
  فقط نگاشت دقیق مرکزی (Strict) اعمال می‌شود.
- ضد race: قفل per-chat + حافظه پیام‌های خودمان (loop guard) + احترام به
  cooldown موتور کانورتر + یک تلاش برای هر پیام.
- ارسال مجدد از wrapper های Unified Pipeline عبور می‌کند (convert قبل از
  ارسال)؛ بنابراین خودِ Resend هم مسیر اصلی است، ارسال مستقیم نیست.

این ماژول هیچ event handler مستقلی ثبت نمی‌کند؛ از داخل هندلر outgoing
موجود (install_premium_emoji_outgoing_injector) فراخوانی می‌شود تا ترتیب
「Resend → Edit fallback」 قطعی باشد و هیچ مسیر موازی‌ای شکل نگیرد.
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict

import config

from services import telegram_logger as tlog
from services.custom_emoji_service import EMOJI_PATTERN
from services.premium_emoji_converter import (
    _chat_kind,
    _new_custom_entities,
)

# فاصله جمع‌آوری قطعات آلبوم: آخرین قطعه پس از این سکوت، آلبوم کامل فرض
# می‌شود. کوتاه نگه داشته شده تا حذف/ارسال مجدد سریع بماند.
ALBUM_FLUSH_DELAY_SECONDS = 1.0
MAX_ALBUM_PARTS = 20
# حافظه پیام‌های خودمان (loop guard) — (chat_id, message_id)
RECENT_SENT_LIMIT = 512
# حافظه پیام‌های گزارش Debug — روی این‌ها هرگز واکنش نمی‌کنیم
IGNORED_LIMIT = 256
# سرور entity را حذف کند: بعد از ۲ بار پیاپی، این مدت تلاش متوقف می‌ماند.
STRIP_COOLDOWN_SECONDS = 300
# ⏱ spec مالک (دور Debug): بعد از send_message این‌قدر صبر، سپس پیام از
# تلگرام واکشی و Entity نسخهٔ سرور بررسی می‌شود؛ Resend فقط بعد از آن.
VERIFY_DELAY_SECONDS = 0.5
CUSTOM_ENTITY_NAME = 'MessageEntityCustomEmoji'


def _has_custom_entity(value):
    """True اگر در ساختار entity ها (شامل لیستِ لیستِ آلبوم) Custom باشد."""
    if isinstance(value, (list, tuple)):
        return any(_has_custom_entity(item) for item in value)
    return type(value).__name__ == CUSTOM_ENTITY_NAME


def _is_custom(entity):
    return type(entity).__name__ == CUSTOM_ENTITY_NAME


def _media_type(message):
    """نوع مدیا برای فیلد media_type بلوک [PREMIUM_CHECK]."""
    media = getattr(message, 'media', None)
    if media is None:
        return 'none'
    name = type(media).__name__
    if name == 'MessageMediaPhoto':
        return 'photo'
    if name == 'MessageMediaDocument':
        document = getattr(media, 'document', None)
        attributes = list(getattr(document, 'attributes', None) or [])
        kinds = {type(attribute).__name__ for attribute in attributes}
        if 'DocumentAttributeSticker' in kinds:
            return 'sticker'
        if 'DocumentAttributeVideo' in kinds:
            return 'video'
        if 'DocumentAttributeAudio' in kinds:
            audio = next((a for a in attributes
                          if type(a).__name__ == 'DocumentAttributeAudio'), None)
            if audio is not None and getattr(audio, 'voice', False):
                return 'voice'
            return 'audio'
        return 'document'
    if name == 'MessageMediaWebPage':
        return 'webpage'
    return name.replace('MessageMedia', '').lower() or 'media'


def _describe_entities(entities):
    """نمایش فشرده و قابل‌خواندن entity ها برای فیلد entities بلوک."""
    entities = list(entities or [])
    if not entities:
        return 'none'
    parts = []
    for entity in entities[:8]:
        name = type(entity).__name__
        offset = getattr(entity, 'offset', '?')
        length = getattr(entity, 'length', '?')
        if name == CUSTOM_ENTITY_NAME:
            parts.append('custom(doc='
                         f'{getattr(entity, "document_id", "?")}@{offset}+{length})')
        else:
            parts.append(f'{name.replace("MessageEntity", "").lower()}'
                         f'@{offset}+{length}')
    extra = f' (+{len(entities) - 8} more)' if len(entities) > 8 else ''
    return f'{len(entities)}: ' + ', '.join(parts) + extra


class PremiumResendManager:
    """مدیریت Copy/Delete/Resend برای کلاینت Self با تأیید سرور (get_messages)."""

    def __init__(self, client, engine, *, is_enabled=None, owner_id=None):
        self.client = client
        self.engine = engine
        self.is_enabled = is_enabled  # optional callable -> None/True/False
        self.owner_id = owner_id
        self.debug_reports = False  # `.premium debug on` — گزارش زنده در Saved
        self._locks = {}
        self._recent = OrderedDict()  # (chat_id, msg_id) -> monotonic time
        self._ignore = OrderedDict()  # پیام‌های گزارش خودمان؛ بدون واکنش
        self._albums = {}  # (chat_id, grouped_id) -> {'parts': [], 'handle': ...}
        self._tasks = set()  # task های flush زنده (برای uninstall تمیز)
        self._strip_streak = {}  # chat_id -> تعداد پیام‌های خودمان بدون entity
        self.stats = {'resent': 0, 'kept': 0, 'deleted': 0, 'failed': 0}

    # ------------------------------------------------------------- gating
    def resend_enabled(self):
        """زنجیره Production-Safe: کلید اصلی → کانورتر → config → پنل حساب.

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
        # حافظه فقط برای loop-guard است؛ بعد از یک ساعت خط می‌خورد.
        if time.monotonic() - stamp > 3600:
            self._recent.pop(key, None)
            return False
        return True

    def _mark_ignored(self, sent):
        """پیام گزارش Debug خودمان → هرگز رویش [PREMIUM_CHECK]/Resend نمی‌زنیم."""
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
        if time.monotonic() - stamp > 3600:
            self._ignore.pop(key, None)
            return False
        return True

    def _note_strip_condition(self, message):
        """پیام خودمان دوباره بدون entity رسید → سرور entity را حذف می‌کند.

        بعد از دو بار پیاپی در یک چت، موتور کانورتر به cooldown می‌رود تا
        هیچ پیامی بی‌دلیل دوبل نشود (حذف/ارسال مجدد سریع می‌ماند).
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
                '⚠️ Premium Emoji Fallback',
                {
                    'Reason': 'Telegram strips custom emoji entities in this chat',
                    'Action': 'Resend paused (cooldown)',
                    'Cooldown': f'{STRIP_COOLDOWN_SECONDS}s',
                    'Chat': chat_id,
                    'Method': 'premium_resend',
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
            kind = _chat_kind(PremiumResendManager._input_chat(message)
                              or chat_id)
        except Exception:  # noqa: BLE001
            kind = 'Unknown'
        return f'{kind} ({chat_id})'

    def _log_block(self, message, *, converted, deleted, resent, reason=None):
        try:
            tlog.send_premium_resend_debug(tlog.format_premium_resend_debug(
                chat=self._chat_label(message),
                message_id=getattr(message, 'id', None),
                converted=converted, deleted=deleted, resent=resent,
                reason=reason))
        except Exception:  # noqa: BLE001 - لاگ هرگز مسیر را نمی‌شکند
            pass

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

    # ------------------------------------------------- [PREMIUM_CHECK]
    def _emit_premium_check(self, message, *, has_custom=None, server_check=None,
                            media_type=None, message_id=None):
        """بلوک [PREMIUM_CHECK] — مشاهده هر پیام قبل از تصمیم Resend.

        شش فیلد اول دقیقاً مطابق spec مالک است؛ ``server_check`` نتیجه
        واکشی مجدد از تلگرام (بعد از sleep 0.5s) را اضافه می‌کند.
        """
        try:
            if has_custom is None:
                existing = list(getattr(message, 'entities', None) or [])
                has_custom = any(_is_custom(item) for item in existing)
            tlog.send_premium_check(tlog.format_premium_check(
                chat_id=getattr(message, 'chat_id', None),
                message_id=(message_id if message_id is not None
                            else getattr(message, 'id', None)),
                has_entity=has_custom,
                entities=_describe_entities(getattr(message, 'entities', None)),
                media_type=(media_type if media_type is not None
                            else _media_type(message)),
                reply_to=self._reply_to_of(message),
                server_check=server_check),
                chat_id=getattr(message, 'chat_id', None))
        except Exception:  # noqa: BLE001 - لاگ هرگز مسیر را نمی‌شکند
            pass

    async def _fetch_from_server(self, message):
        """واکشی مجدد پیام از تلگرام — spec مالک: client.get_messages.

        خروجی ``(status, message, note)``:
        - ``('ok', Message, 'fetched after 0.5s → has_entity=…')``
        - ``('missing', None, 'fetched after 0.5s → message not found')``
        - ``('error', None, 'fetch failed (…) → event view used')``
          در خطای واکشی، تصمیم با نمای event ادامه می‌یابد (رفتار قبلی).
        """
        delay = f'{VERIFY_DELAY_SECONDS:g}s'
        chat_id = getattr(message, 'chat_id', None)
        message_id = getattr(message, 'id', None)
        if chat_id is None or message_id is None:
            return 'error', None, 'fetch skipped (no chat/id) → event view used'
        try:
            fetched = await self.client.get_messages(chat_id, ids=message_id)
        except Exception as exc:  # noqa: BLE001 - خطای واکشی = تصمیم با event
            return ('error', None,
                    f'fetch failed ({type(exc).__name__}) → event view used')
        if fetched is None:
            return 'missing', None, f'fetched after {delay} → message not found'
        has_entity = _has_custom_entity(getattr(fetched, 'entities', None))
        return 'ok', fetched, f'fetched after {delay} → has_entity={has_entity}'

    # ------------------------------------------------------ debug reports
    def set_debug_reports(self, enabled):
        """حالت Debug (`.premium debug on`) — گزارش زنده مراحل در Saved Messages."""
        self.debug_reports = bool(enabled)
        return self.debug_reports

    async def _debug_report(self, text):
        """گزارش زنده در Saved Messages؛ بدون تبدیل و بدون واکنش به خودش.

        ارسال با bypass کانورتر انجام می‌شود (گزارش هرگز به Custom Emoji
        تبدیل نمی‌شود) و message_id آن در لیست ignore می‌رود تا حلقه
        [PREMIUM_CHECK] روی خودِ گزارش شکل نگیرد.
        """
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
        return (f'chat: {getattr(message, "chat_id", None)} | '
                f'msg: {getattr(message, "id", None)} | '
                f'media: {_media_type(message)} | '
                f'reply: {self._reply_to_of(message) or "none"}')

    # ------------------------------------------------- entry from injector
    async def handle_outgoing(self, message):
        """نقطه ورود از هندلر outgoing کانورتر.

        خروجی:
        - 'handled'   → این ماژول مالک پیام است (ارسال مجدد شد/بافر شد/پیام
                        خودِ ماست)؛ هندلر injector نباید edit کند.
        - 'skipped'   → این پیام موضوع این ماژول نیست؛ injector ادامه دهد.

        جریان Debug (spec مالک):
            [PREMIUM_CHECK] → sleep 0.5s → get_messages → بررسی Entity
            → Entity نبود؟ → Copy/Delete/Resend (آلبوم: یک بار برای کل)
        """
        if message is None or getattr(message, 'action', None) is not None:
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
            if observe:
                self._emit_premium_check(
                    message,
                    server_check='album part buffered'
                    if active else 'album part (resend off → edit path)')
            if not active:
                return 'skipped'
            return await self._handle_album_part(message)
        if active and self._is_recent(message):
            # پیام خودِ ماست (نسخه resend/گزارش)؛ مشاهده + شمارش strip
            await self._observe_resent(message)
            return 'handled'
        if not observe:
            return 'skipped'  # خاموش → مسیر edit قبلی injector دست‌نخورده
        return await self._handle_single(message, allow_resend=active)

    async def _observe_resent(self, message):
        """پیام خودِ ما دوباره رسید (loop guard) — مشاهده + شمارش strip.

        در حالت Debug پیام از سرور هم واکشی می‌شود تا دقیقاً معلوم شود
        تلگرام entity نسخه ارسال‌مجدد را نگه داشته یا حذف کرده (هدف اصلی
        این دور Debug).
        """
        text = getattr(message, 'message', None) or ''
        existing = list(getattr(message, 'entities', None) or [])
        has_custom = any(_is_custom(item) for item in existing)
        relevant = (bool(EMOJI_PATTERN.search(text))
                    or getattr(message, 'media', None) is not None)
        if not relevant:
            return
        server_check = None
        if not has_custom and text and self.debug_reports:
            await asyncio.sleep(VERIFY_DELAY_SECONDS)
            _status, _fetched, server_check = await self._fetch_from_server(
                message)
            await self._debug_report(
                '🔧 Premium Debug — نسخه Resend شده\n'
                f'{self._report_head(message)}\n'
                f'entity در event: {has_custom}\n'
                f'سرور: {server_check}')
        self._emit_premium_check(message, has_custom=has_custom,
                                 server_check=server_check)
        if not has_custom and text and EMOJI_PATTERN.search(text):
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
            return None  # همین حالا پرمیوم است؛ idempotency
        try:
            _, merged = self.engine.convert(text, existing)
        except Exception:  # noqa: BLE001 - تشخیص هرگز مسیر را نمی‌شکند
            return None
        added = _new_custom_entities(merged, existing)
        if not added:
            return None  # هیچ نگاشت دقیقی نیست؛ پیام اصلی همان است
        return text, merged, added

    async def _handle_single(self, message, *, allow_resend):
        """یک پیام غیرآلبومی: [PREMIUM_CHECK] → fetch سرور → تصمیم → Resend."""
        text = getattr(message, 'message', None)
        if not isinstance(text, str) or not text:
            return 'skipped'
        existing = list(getattr(message, 'entities', None) or [])
        has_custom = any(_is_custom(entity) for entity in existing)
        media = getattr(message, 'media', None)
        has_emoji = bool(EMOJI_PATTERN.search(text))
        if not has_emoji and media is None:
            return 'skipped'  # بدون ایموجی/مدیا؛ موضوع پرمیوم نیست
        if has_custom:
            self._emit_premium_check(message, has_custom=True,
                                     server_check='entity present (event view)')
            await self._debug_report(
                '🔧 Premium Debug\n'
                f'{self._report_head(message)}\n'
                '1) event: has_entity=True → پیام پرمیوم است؛ بدون تغییر')
            return 'skipped'
        if not has_emoji:
            self._emit_premium_check(message, has_custom=False,
                                     server_check='no emoji in text → nothing to convert')
            return 'skipped'
        # ⏱ spec مالک: بعد از send_message → sleep 0.5s → fetch از تلگرام
        await asyncio.sleep(VERIFY_DELAY_SECONDS)
        status, fetched, server_note = await self._fetch_from_server(message)
        base = fetched if status == 'ok' else message
        server_entity = (_has_custom_entity(getattr(fetched, 'entities', None))
                         if status == 'ok' else None)
        mapping_note = ''
        converted = None
        if status == 'ok' and server_entity:
            mapping_note = 'server kept entity'
        elif allow_resend:
            converted = self._conversion(base)
            if converted is None:
                mapping_note = 'mapping: none → keep'
            else:
                mapping_note = f'mapping: {len(converted[2])} custom entity(ies)'
        else:
            mapping_note = 'mapping: not checked (resend off)'
        self._emit_premium_check(
            message, has_custom=False,
            server_check=f'{server_note} | {mapping_note}'
            if mapping_note else server_note)
        if status == 'missing':
            await self._debug_report(
                '🔧 Premium Debug\n'
                f'{self._report_head(message)}\n'
                f'سرور: {server_note} → پیام روی سرور نیست؛ کاری انجام نشد')
            return 'skipped'
        if server_entity:
            # سرور entity را نگه داشته (نمای event قدیمی بود) → پیام سالم است
            await self._debug_report(
                '🔧 Premium Debug\n'
                f'{self._report_head(message)}\n'
                f'2) سرور: {server_note}\n'
                '3) نتیجه: entity سالم است؛ Resend لازم نیست')
            return 'skipped'
        if not allow_resend or converted is None:
            return 'skipped'
        _text, merged, _added = converted
        chat_id = getattr(message, 'chat_id', None)
        async with self._lock_for(chat_id or 0):
            # بعد از انتظار قفل: پیام خودِ ما نباشد (مسیر دیگری نفرستاده باشد)
            if self._is_recent(message):
                return 'handled'
            try:
                sent = await self._resend_single(base, _text, merged)
            except Exception as exc:  # noqa: BLE001 - هیچ پیامی گم نمی‌شود
                self.stats['failed'] += 1
                self._log_block(message, converted=True, deleted=False,
                                resent=False,
                                reason=f'resend failed ({type(exc).__name__})')
                await self._debug_report(
                    '🔧 Premium Debug — شکست Resend\n'
                    f'{self._report_head(message)}\n'
                    f'خطا: {type(exc).__name__} → پیام اصلی حفظ شد')
                return 'skipped'  # injector مسیر edit را امتحان می‌کند
            if sent is None or not _has_custom_entity(
                    getattr(sent, 'entities', None)):
                # نسخه ارسالی entity نداشت (مثلاً clean retry بعد از reject
                # تلگرام) → پیام اصلی حفظ می‌شود؛ مسیر edit تلاش می‌کند.
                self._log_block(message, converted=True, deleted=False,
                                resent=False,
                                reason='resent without entity; original kept')
                await self._debug_report(
                    '🔧 Premium Debug — Resend بی‌entity\n'
                    f'{self._report_head(message)}\n'
                    'نسخه ارسالی entity نداشت → پیام اصلی حفظ شد')
                return 'skipped'
            self._mark_recent(getattr(base, 'chat_id', chat_id),
                              getattr(sent, 'id', 0) or 0)
            deleted = await self._delete_original(base)
            self.stats['resent'] += 1
            if deleted:
                self.stats['deleted'] += 1
            self._log_block(message, converted=True, deleted=deleted,
                            resent=True)
            await self._debug_report(
                '🔧 Premium Debug — Resend کامل شد\n'
                f'{self._report_head(message)}\n'
                f'نسخه جدید: msg={getattr(sent, "id", "?")} با '
                f'{len(merged)} entity\n'
                f'پیام اصلی: {"حذف شد" if deleted else "حذف نشد (خطا)"}')
            return 'handled'

    async def _resend_single(self, message, text, merged):
        """کپی دقیق یک پیام (متن یا مدیا+کپشن) با entity های ازپیش‌ساخته."""
        chat = self._input_chat(message)
        if chat is None:
            chat = await self.client.get_input_entity(message.chat_id)
        reply_to = self._reply_to_of(message)
        common = {
            'parse_mode': None,
            'formatting_entities': merged or None,
            'reply_to': reply_to,
            'silent': bool(getattr(message, 'silent', False)),
        }
        media = getattr(message, 'media', None)
        if media is not None:
            sent = await self.client.send_file(
                chat, media, caption=text, **common)
        else:
            sent = await self.client.send_message(chat, text, **common)
        return sent

    async def _delete_original(self, message):
        try:
            await self.client.delete_messages(
                getattr(message, 'chat_id', None), getattr(message, 'id', None))
            return True
        except Exception as exc:  # noqa: BLE001 - پیام اصلی باید بماند
            self.stats['kept'] += 1
            self._log_block(message, converted=True, deleted=False, resent=True,
                            reason=f'delete failed ({type(exc).__name__})')
            return False

    # ------------------------------------------------------------- album
    async def _handle_album_part(self, message):
        """بافر کردن قطعات آلبوم و ارسال مجدد یک‌جا بعد از تکمیل."""
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
        """بررسی سروری آلبوم و ارسال مجدد «کل آلبوم» فقط یک بار.

        spec مالک (دور Debug): بعد از تکمیل گروه → sleep 0.5s → همه قطعات
        از تلگرام واکشی می‌شوند → اگر هر قطعه entity نداشت، کل آلبوم یک بار
        کپی/ارسال مجدد و اصل‌ها حذف می‌شوند (نه هر پیام جدا). هیچ پیامی
        گم نمی‌شود.
        """
        parts = [p for p in parts if p is not None]
        if not parts:
            return
        if not self.resend_enabled():
            return  # خاموش شدن در فاصله بافر؛ injector edit مسیر قبلی خودش
        # ⏱ spec مالک: بعد از آخرین قطعه → sleep 0.5s → واکشی از تلگرام
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
                verdict = ('has_entity=True' if has_entity
                           else 'has_entity=False')
                notes.append(f'{part_id}: {verdict}')
                bases.append(fetched)
            else:
                # واکشی ناموفق/پیام غایب → احتیاط: مسیر بررسی Resend طی می‌شود
                any_missing = True
                notes.append(f'{part_id}: {note}')
                bases.append(part)
        conversions = None
        if any_missing:
            # تبدیل دقیقاً یک‌بار برای هر قطعه (نتیجه همان است که ارسال می‌شود)
            conversions = [self._conversion(base) for base in bases]
            if all(item is None for item in conversions):
                conversions = None  # هیچ نگاشتی نیست → Resend بی‌معنا؛ آلبوم می‌ماند
        album_ids = ', '.join(str(getattr(base, 'id', '?')) for base in bases)
        album_media = f'album({_media_type(bases[0])}×{len(bases)})'
        self._emit_premium_check(
            bases[0], has_custom=not any_missing,
            media_type=album_media, message_id=album_ids,
            server_check=('; '.join(notes)
                          + (' | resend whole album' if conversions is not None
                             else ' | album kept (entity or no mapping)')))
        if conversions is None:
            return
        try:
            async with self._lock_for(key[0]):
                sent_list = await self._resend_album(bases, conversions)
                if not sent_list:
                    return
                if not _has_custom_entity(
                        [getattr(s, 'entities', None) for s in sent_list]):
                    # هیچ نسخه‌ای entity نگرفت → پیام‌های اصلی حفظ می‌شوند
                    self._log_block(bases[0], converted=True, deleted=False,
                                    resent=False,
                                    reason='album resent without entity; originals kept')
                    await self._debug_report(
                        '🔧 Premium Debug — آلبوم بدون entity\n'
                        f'{self._report_head(bases[0])} | parts={len(bases)}\n'
                        'نسخه ارسالی entity نداشت → اصل‌ها حفظ شدند')
                    return
                for sent in sent_list:
                    self._mark_recent(key[0], getattr(sent, 'id', 0) or 0)
                deleted = 0
                for base in bases:
                    if await self._delete_original(base):
                        deleted += 1
                self.stats['resent'] += 1
                self.stats['deleted'] += deleted
                self._log_block(
                    bases[0], converted=True, deleted=deleted == len(bases),
                    resent=True,
                    reason=None if deleted == len(bases)
                    else f'{len(bases) - deleted} delete(s) failed; originals kept')
                await self._debug_report(
                    '🔧 Premium Debug — آلبوم Resend شد\n'
                    f'{self._report_head(bases[0])} | parts={len(bases)}\n'
                    f'کل آلبوم یک‌بار ارسال مجدد شد؛ حذف: {deleted}/{len(bases)}')
        except Exception as exc:  # noqa: BLE001 - پیام اصلی باید بماند
            self.stats['failed'] += 1
            self._log_block(bases[0], converted=True, deleted=False, resent=False,
                            reason=f'album resend failed ({type(exc).__name__})')
            await self._debug_report(
                '🔧 Premium Debug — شکست آلبوم\n'
                f'{self._report_head(bases[0])}\n'
                f'خطا: {type(exc).__name__} → اصل‌ها حفظ شدند')

    async def _resend_album(self, parts, conversions=None):
        """کپی دقیق آلبوم: همه مدیاها یک‌جا + کپشن/کپشن‌های اصلی.

        ``conversions`` نتیجه _conversion هر قطعه است (بدون تبدیل دوباره)؛
        اگر داده نشود، همان‌جا تبدیل می‌شود (مسیر تست/فراخوانی مستقیم).
        """
        pairs = sorted(zip(parts, conversions if conversions else [None] * len(parts)),
                       key=lambda pair: getattr(pair[0], 'id', 0))
        parts = [p for p, _ in pairs]
        if not parts:
            return []
        files = [getattr(p, 'media', None) for p in parts]
        if any(f is None for f in files):
            return []
        captions = []
        entity_lists = []
        for part, converted in pairs:
            if converted is None:
                converted = self._conversion(part)
            if converted is None:
                # بدون نگاشت دقیق: کپشن اصلی با entity های اصلی حفظ می‌شود
                existing = list(getattr(part, 'entities', None) or [])
                captions.append(getattr(part, 'message', None) or '')
                entity_lists.append(existing)
            else:
                captions.append(converted[0])
                entity_lists.append(list(converted[1] or []))
        chat = self._input_chat(parts[0])
        if chat is None:
            chat = await self.client.get_input_entity(parts[0].chat_id)
        reply_ids = {self._reply_to_of(p) for p in parts} - {None}
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

    # ------------------------------------------------------------ command
    def reset_recent(self):
        self._recent.clear()


def install_premium_resend(client, engine, *, is_enabled=None, owner_id=None):
    """نصب مدیر Resend روی کلاینت Self مجهز به کانورتر (یک‌بار؛ بدون handler)."""
    if engine is None:
        return None  # کلاینت بات یا بدون Unified Pipeline: هرگز
    existing = getattr(client, '_premium_resend_manager', None)
    if existing is not None:
        return existing
    if getattr(client, '_premium_emoji_converter', None) is not engine:
        return None  # فقط کلاینت Self مجهز به Unified Pipeline
    manager = PremiumResendManager(client, engine, is_enabled=is_enabled,
                                   owner_id=owner_id)
    client._premium_resend_manager = manager
    return manager


def uninstall_premium_resend(client):
    manager = getattr(client, '_premium_resend_manager', None)
    if manager is not None:
        for task in list(getattr(manager, '_tasks', ())):
            task.cancel()
        for buffer in list(getattr(manager, '_albums', {}).values()):
            handle = buffer.get('handle')
            if handle is not None:
                handle.cancel()
        del client._premium_resend_manager
