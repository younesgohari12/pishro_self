"""Premium Resend Mode — ارسال مجدد هوشمند (Copy/Delete/Resend).

روش تبدیل با Entity در بعضی چت‌ها و شرایط Telegram Server کار نمی‌کند (سرور
entity را بی‌صدا حذف می‌کند یا پیام از دستگاه دیگری رسیده و اصلاً از کانورتر
عبور نکرده). این ماژول لایه تأیید/ترمیم بعد از ارسال است:

    Send → Verify (entity در پیام نهایی؟) → کپی دقیق → ارسال با Custom Emoji
         → حذف پیام اصلی

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
# سرور entity را حذف کند: بعد از ۲ بار پیاپی، این مدت تلاش متوقف می‌ماند.
STRIP_COOLDOWN_SECONDS = 300


def _has_custom_entity(value):
    """True اگر در ساختار entity ها (شامل لیستِ لیستِ آلبوم) Custom باشد."""
    if isinstance(value, (list, tuple)):
        return any(_has_custom_entity(item) for item in value)
    return type(value).__name__ == 'MessageEntityCustomEmoji'


class PremiumResendManager:
    """مدیریت Copy/Delete/Resend برای کلاینت Self؛ بدون شبکه در تصمیم‌گیری."""

    def __init__(self, client, engine, *, is_enabled=None, owner_id=None):
        self.client = client
        self.engine = engine
        self.is_enabled = is_enabled  # optional callable -> None/True/False
        self.owner_id = owner_id
        self._locks = {}
        self._recent = OrderedDict()  # (chat_id, msg_id) -> monotonic time
        self._albums = {}  # (chat_id, grouped_id) -> {'parts': [], 'handle': TimerHandle}
        self._strip_streak = {}  # chat_id -> تعداد پیام‌های خودمان که entity نداشتند
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

    # ------------------------------------------------- entry from injector
    async def handle_outgoing(self, message):
        """نقطه ورود از هندلر outgoing کانورتر.

        خروجی:
        - 'handled'   → این ماژول مالک پیام است (ارسال مجدد شد/بافر شد/پیام
                        خودِ ماست)؛ هندلر injector نباید edit کند.
        - 'skipped'   → این پیام موضوع این ماژول نیست؛ injector ادامه دهد.
        """
        if message is None or getattr(message, 'action', None) is not None:
            return 'skipped'
        if getattr(message, 'fwd_from', None) is not None:
            return 'skipped'  # forward ها طبق قانون هرگز دست‌نخورده می‌مانند
        if getattr(message, 'via_bot_id', None):
            return 'skipped'  # پیام‌های پنل اینلاین
        if not self.resend_enabled():
            return 'skipped'  # خاموش → مسیر edit قبلی injector دست‌نخورده
        if self._is_recent(message):
            # پیام خودِ ماست؛ اگر باز هم بدون entity رسیده یعنی سرور حذفش
            # کرده → شمارش وضعیت strip (بدون هیچ تلاش دوباره).
            text = getattr(message, 'message', None) or ''
            existing = list(getattr(message, 'entities', None) or [])
            has_custom = any(type(e).__name__ == 'MessageEntityCustomEmoji'
                             for e in existing)
            if not has_custom and text and EMOJI_PATTERN.search(text):
                self._note_strip_condition(message)
            return 'handled'
        chat_id = getattr(message, 'chat_id', None)
        if chat_id is None:
            return 'skipped'
        if getattr(message, 'grouped_id', None) is not None:
            return await self._handle_album_part(message)
        return await self._handle_single(message)

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
        if any(type(e).__name__ == 'MessageEntityCustomEmoji' for e in existing):
            return None  # همین حالا پرمیوم است؛ idempotency
        try:
            _, merged = self.engine.convert(text, existing)
        except Exception:  # noqa: BLE001 - تشخیص هرگز مسیر را نمی‌شکند
            return None
        added = _new_custom_entities(merged, existing)
        if not added:
            return None  # هیچ نگاشت دقیقی نیست؛ پیام اصلی همان است
        return text, merged, added

    async def _handle_single(self, message):
        converted = self._conversion(message)
        if converted is None:
            return 'skipped'
        text, merged, added = converted
        async with self._lock_for(getattr(message, 'chat_id', 0)):
            # بعد از انتظار قفل: پیام خودِ ما نباشد (مسیر دیگری نفرستاده باشد)
            if self._is_recent(message):
                return 'handled'
            try:
                sent = await self._resend_single(message, text, merged)
            except Exception as exc:  # noqa: BLE001 - هیچ پیامی گم نمی‌شود
                self.stats['failed'] += 1
                self._log_block(message, converted=True, deleted=False,
                                resent=False,
                                reason=f'resend failed ({type(exc).__name__})')
                return 'skipped'  # injector مسیر edit را امتحان می‌کند
            if sent is None:
                return 'skipped'
            if not _has_custom_entity(getattr(sent, 'entities', None)):
                # نسخه ارسالی entity نداشت (مثلاً clean retry بعد از reject
                # تلگرام) → پیام اصلی حفظ می‌شود؛ مسیر edit تلاش می‌کند.
                self._log_block(message, converted=True, deleted=False,
                                resent=False,
                                reason='resent without entity; original kept')
                return 'skipped'
            self._mark_recent(message.chat_id, getattr(sent, 'id', 0) or 0)
            deleted = await self._delete_original(message)
            self.stats['resent'] += 1
            if deleted:
                self.stats['deleted'] += 1
            self._log_block(message, converted=True, deleted=deleted, resent=True)
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
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._flush_album(key))
        except RuntimeError:
            # بدون event loop فعال؛ بافر تا فراخوانی بعدی می‌ماند (بدون crash)
            pass

    def _flush_album_now(self, key):
        buffer = self._albums.pop(key, None)
        if buffer is None:
            return
        if buffer['handle'] is not None:
            buffer['handle'].cancel()
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._resend_album_and_cleanup(key, buffer['parts']))
        except RuntimeError:
            pass

    async def _flush_album(self, key):
        buffer = self._albums.pop(key, None)
        if buffer is None:
            return
        await self._resend_album_and_cleanup(key, buffer['parts'])

    async def _resend_album_and_cleanup(self, key, parts):
        """ارسال مجدد آلبوم کامل + حذف اصول‌ها؛ هیچ پیامی گم نمی‌شود."""
        parts = [p for p in parts if p is not None]
        if not parts:
            return
        if not self.resend_enabled():
            return  # خاموش شدن در فاصله بافر؛ injector edit مسیر قبلی خودش
        # تبدیل دقیقاً یک‌بار برای هر قطعه (نتیجه همان است که ارسال می‌شود)
        conversions = []
        for part in parts:
            converted = self._conversion(part)
            conversions.append(converted)
        if all(c is None for c in conversions):
            return  # کل آلبوم تبدیل‌پذیر نیست؛ injector edit مسیر خودش را می‌رود
        try:
            async with self._lock_for(key[0]):
                sent_list = await self._resend_album(parts, conversions)
                if not sent_list:
                    return
                if not _has_custom_entity(
                        [getattr(s, 'entities', None) for s in sent_list]):
                    # هیچ نسخه‌ای entity نگرفت → پیام‌های اصلی حفظ می‌شوند
                    self._log_block(parts[0], converted=True, deleted=False,
                                    resent=False,
                                    reason='album resent without entity; originals kept')
                    return
                for sent in sent_list:
                    self._mark_recent(key[0], getattr(sent, 'id', 0) or 0)
                deleted = 0
                for part in parts:
                    if await self._delete_original(part):
                        deleted += 1
                self.stats['resent'] += 1
                self.stats['deleted'] += deleted
                self._log_block(
                    parts[0], converted=True, deleted=deleted == len(parts),
                    resent=True,
                    reason=None if deleted == len(parts)
                    else f'{len(parts) - deleted} delete(s) failed; originals kept')
        except Exception as exc:  # noqa: BLE001 - پیام اصلی باید بماند
            self.stats['failed'] += 1
            self._log_block(parts[0], converted=True, deleted=False, resent=False,
                            reason=f'album resend failed ({type(exc).__name__})')

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
        for buffer in list(getattr(manager, '_albums', {}).values()):
            task = buffer.get('task')
            if task is not None:
                task.cancel()
        del client._premium_resend_manager
