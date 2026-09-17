"""سلف‌بات - نسخه کامل: دیتابیس، حذف، اسپم، کنسل، بلاک، سکوت، حالت دشمن، کپی محتوا"""
import os
import re
import time
import random
import asyncio
import tempfile
from telethon import TelegramClient, events, errors, functions
from telethon.sessions import StringSession
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import DocumentAttributeAudio
from config import (
    API_ID,
    API_HASH,
    BASE_DIR,
    DATA_DIR,
    INLINE_USERNAME,
    UPDATE_INTERVAL,
    CLOCK_FONTS,
    SPAM_DELAY,
    DELETE_DELAY,
    DELETE_CHUNK_SIZE,
    MAX_SPAM_COUNT,
    MAX_DELETE_COUNT,
    AVALAI_API_KEY,
    AVALAI_BASE_URL,
    AVALAI_STT_MODEL,
    AVALAI_REQUEST_TIMEOUT,
    AVALAI_AUDIO_MAX_BYTES,
    AVALAI_TTS_MAX_CHARS,
    UPLOAD_DIR,
    get_tehran_time,
    get_tehran_datetime
)
import db
from database import models as tabchi_models
from services.access_service import can_run
from handlers.crypto import execute_crypto_text
from handlers.ai import execute_ai_text
from handlers.translate import execute_translate_text
from services.ai_assistant import split_telegram_text
import ui
from services.feature_flags import enabled, CRYPTO_DISABLED, TRANSLATE_DISABLED
from avalai_audio import AvalAIError, transcribe_audio
from tts.avalai_tts import TTSError, text_to_speech
from services.deleted_handler import register_deleted_message_handlers
from services.font_formatter import register_message_font_handler
from services.premium_emoji_converter import (
    install_premium_emoji_converter,
    install_premium_emoji_outgoing_injector,
    uninstall_premium_emoji_converter,
    uninstall_premium_emoji_outgoing_injector,
)
from services.premium_resend import (
    install_premium_resend,
    uninstall_premium_resend,
)
from services import away as away_service
from services.state_closer import close_all_pending, log_state
from services.custom_emoji_service import (
    EmojiError,
    detailed_result_text,
    extract_custom_emojis,
    parse_document_id,
    resolve_custom_emoji,
    send_custom_emoji,
)
from services.copy_protected import (
    begin_destination_capture,
    cancel_destination_capture,
    parse_message_link,
    register_copy_protected_handlers,
    resolve_peer,
)
from tts.voices import (
    DEFAULT_VOICE,
    VOICE_MENU_TEXT,
    VOICE_OPTIONS,
    get_user_voice,
    get_voice_label,
    resolve_voice_choice,
    set_user_voice,
)

# ========================================
# الگوی دستورات
# ========================================
PATTERN_PANEL = re.compile(r'^\.پنل$', re.IGNORECASE)
PATTERN_CLOSE = re.compile(r'^\.بستن$', re.IGNORECASE)
PATTERN_AWAY = re.compile(r'^\.away(?:\s+(.+))?$', re.IGNORECASE | re.DOTALL)
PATTERN_INFO = re.compile(r'^\.info$', re.IGNORECASE)
PATTERN_PING = re.compile(r'^\.ping$', re.IGNORECASE)
PATTERN_SPAM = re.compile(r'^\.اسپم\s+(\d+)\s+(.+)$', re.IGNORECASE)
PATTERN_CANCEL = re.compile(r'^\.کنسل$', re.IGNORECASE)
PATTERN_DELETE = re.compile(r'^\.حذف(?:\s+(\d+))?$', re.IGNORECASE)
PATTERN_BLOCK = re.compile(r'^\.بلاک$', re.IGNORECASE)
PATTERN_MUTE = re.compile(r'^\.سکوت$', re.IGNORECASE)
PATTERN_ENEMY = re.compile(r'^\.دشمن$', re.IGNORECASE)
PATTERN_STT = re.compile(r'^\s*\.تبد[یي]ل\s+صوت\s+به\s+متن\s*$', re.IGNORECASE)
PATTERN_TTS = re.compile(r'^\s*\.تبد[یي]ل\s+متن\s+به\s+صوت(?:\s+(.+?))?\s*$', re.IGNORECASE | re.DOTALL)
PATTERN_COPY = re.compile(r'^\s*\.کپی\s+محتوا(?:\s+(.+?))?\s*$', re.IGNORECASE | re.DOTALL)
PATTERN_CRYPTO = re.compile(r'^\s*\.ارز(?:\s+.*)?$', re.IGNORECASE | re.DOTALL)
PATTERN_TRANSLATE = re.compile(r'^\s*\.ترجمه(?:\s+.*)?$', re.IGNORECASE | re.DOTALL)
PATTERN_AI = re.compile(r'^\s*\.(?:ai|هوش(?:\s+مصنوعی)?)(?:\s+.*)?$', re.IGNORECASE | re.DOTALL)

# ========================================
# تسک‌های فعال
# ========================================
active_spam_tasks = {}
active_cleanup_tasks = {}

# ========================================
# 📄 لیست حالت دشمن (فایل fosh_list.txt)
# ========================================
FOSH_FILE = os.path.join(DATA_DIR, 'fosh_list.txt')
_fosh_cache = {'mtime': 0, 'lines': []}


def load_fosh_list():
    """خواندن لیست با کش بر اساس زمان تغییر فایل"""
    try:
        mt = os.path.getmtime(FOSH_FILE)
    except OSError:
        return []
    if mt != _fosh_cache['mtime']:
        try:
            with open(FOSH_FILE, 'r', encoding='utf-8') as f:
                lines = [line.strip() for line in f if line.strip()]
            _fosh_cache['mtime'] = mt
            _fosh_cache['lines'] = lines
        except Exception:
            return _fosh_cache['lines']
    return _fosh_cache['lines']


async def _temp_message(client, chat_id, text, delay=3):
    try:
        msg = await client.send_message(chat_id, text, parse_mode='md')
        await asyncio.sleep(delay)
        await msg.delete()
    except Exception:
        try:
            msg = await client.send_message(chat_id, text)
            await asyncio.sleep(delay)
            await msg.delete()
        except Exception:
            pass


def _is_audio_message(message):
    """True for Telegram voice notes and regular audio documents."""
    if not message or not getattr(message, 'media', None):
        return False
    try:
        if getattr(message, 'voice', None) or getattr(message, 'audio', None):
            return True
    except Exception:
        pass
    document = getattr(message, 'document', None)
    if document is not None:
        for attr in getattr(document, 'attributes', []) or []:
            if isinstance(attr, DocumentAttributeAudio):
                return True
    file_info = getattr(message, 'file', None)
    mime_type = (getattr(file_info, 'mime_type', '') or '').lower()
    return mime_type.startswith('audio/')


def _audio_suffix(message):
    file_info = getattr(message, 'file', None)
    ext = getattr(file_info, 'ext', None)
    if ext:
        return ext if ext.startswith('.') else f'.{ext}'
    mime_type = (getattr(file_info, 'mime_type', '') or '').lower()
    return {
        'audio/ogg': '.ogg',
        'audio/opus': '.ogg',
        'audio/mpeg': '.mp3',
        'audio/mp3': '.mp3',
        'audio/mp4': '.m4a',
        'audio/x-m4a': '.m4a',
        'audio/wav': '.wav',
        'audio/x-wav': '.wav',
        'audio/flac': '.flac',
        'audio/webm': '.webm',
    }.get(mime_type, '.ogg')


def _split_plain_text(text, limit=3800):
    text = (text or '').strip()
    if not text:
        return []
    chunks = []
    while len(text) > limit:
        cut = text.rfind('\n', 0, limit)
        if cut < limit // 2:
            cut = text.rfind(' ', 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        chunks.append(text)
    return chunks


async def _cancel_task(store, key):
    task = store.get(key)
    if task is None:
        return False
    if not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
    if store.get(key) is task:
        del store[key]
    return True


async def spam_task(client, key, chat_id, count, text):
    current = asyncio.current_task()
    try:
        for _ in range(count):
            if not can_run(key[0]):
                break
            sent = False
            for _retry in range(5):
                if not can_run(key[0]):
                    break
                try:
                    await client.send_message(chat_id, text)
                    sent = True
                    break
                except errors.FloodWaitError as e:
                    print(f"⏳ {key[0]}: FloodWait در اسپم {e.seconds}s")
                    await asyncio.sleep(e.seconds)
                except Exception as e:
                    print(f"⚠️ خطا در اسپم: {e}")
                    break
            if not sent:
                break
            await asyncio.sleep(SPAM_DELAY)
        if active_spam_tasks.get(key) is current:
            await _temp_message(
                client,
                chat_id,
                f"✅ **اسپم با موفقیت به پایان رسید**\n📊 تعداد: `{count}` پیام"
            )
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"⚠️ خطا در spam_task: {e}")
    finally:
        if active_spam_tasks.get(key) is current:
            del active_spam_tasks[key]


async def delete_task(client, key, chat_id, message_ids):
    current = asyncio.current_task()
    try:
        for i in range(0, len(message_ids), DELETE_CHUNK_SIZE):
            if not can_run(key[0]):
                return
            chunk = message_ids[i:i + DELETE_CHUNK_SIZE]
            while True:
                if not can_run(key[0]):
                    return
                try:
                    await client.delete_messages(chat_id, chunk, revoke=True)
                    break
                except errors.FloodWaitError as e:
                    print(f"⏳ {key[0]}: FloodWait در حذف {e.seconds}s")
                    await asyncio.sleep(e.seconds)
                except Exception:
                    try:
                        await client.delete_messages(chat_id, chunk, revoke=False)
                        break
                    except errors.FloodWaitError as e2:
                        await asyncio.sleep(e2.seconds)
                    except Exception as e2:
                        print(f"⚠️ خطا در حذف پیام: {e2}")
                        return
            await asyncio.sleep(DELETE_DELAY)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"⚠️ خطا در delete_task: {e}")
    finally:
        if active_cleanup_tasks.get(key) is current:
            del active_cleanup_tasks[key]


def _get_message_type_label(message) -> str:
    """کمکی برای تعیین نوع پیام."""
    if getattr(message, 'photo', None): return 'عکس'
    if getattr(message, 'gif', None): return 'گیف'
    if getattr(message, 'sticker', None): return 'استیکر'
    if getattr(message, 'voice', None): return 'ویس'
    if getattr(message, 'audio', None): return 'صوت'
    if getattr(message, 'video', None): return 'ویدیو'
    if getattr(message, 'poll', None): return 'نظرسنجی'
    if getattr(message, 'contact', None): return 'مخاطب'
    if getattr(message, 'geo', None) or getattr(message, 'venue', None): return 'لوکیشن'
    if getattr(message, 'document', None): return 'فایل'
    if getattr(message, 'media', None): return 'مدیا'
    return 'متن'


async def run_self(session_path, session_string):
    """Connect saved credentials without requiring pre-existing DB records."""
    from services.session_restore import restore_account
    from services.logging_service import get_logger
    import self_manager

    sid = os.path.basename(session_path)
    client = None
    uid = None
    try:
        # Never call start()/sign_in(): restoration must not request a new login.
        client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
        await client.connect()
        if not await client.is_user_authorized():
            print(f"❌ سشن {sid} از نظر تلگرام معتبر نیست؛ فایل آن حفظ شد")
            return
        me = await client.get_me()
        uid = restore_account(sid, me)
        from services.usage_service import enroll_verified
        enroll_verified(uid)
        await _run_connected_self(client, me, uid, sid)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # Malformed credentials may appear in exception messages: log type only.
        get_logger('self').warning('Session startup/runtime failed name=%s error=%s',
                                   sid, type(exc).__name__)
        from services import telegram_logger as tlog
        tlog.send_error(
            '❌ Session Error',
            {
                'Account': sid,
                'Error': type(exc).__name__,  # هرگز پیام استثنا (احتمال راز)
            },
            where='self.py')
        print(f"❌ بازیابی سشن {sid} ناموفق بود ({type(exc).__name__})؛ فایل حفظ شد")
    finally:
        if uid is not None:
            self_manager.unregister_client(uid, client)
        if client is not None:
            uninstall_premium_emoji_outgoing_injector(client)
            uninstall_premium_resend(client)
            uninstall_premium_emoji_converter(client)
            try:
                await client.disconnect()
            except Exception:
                pass


async def _run_connected_self(client, me, uid, sid):
    # 🎨 Premium Emoji Converter (Unified Pipeline — مسیر اصلی تبدیل قبل از
    # ارسال): تمام خروجی‌های این اکانت (پاسخ، AI، ترجمه، کریپتو، تبچی،
    # دستورات، زمان‌بند، کپشن) از یک گذرگاه مرکزی عبور می‌کنند و از همان ابتدا
    # با MessageEntityCustomEmoji ارسال می‌شوند. سیستم Prefix Emoji حذف شده؛
    # هیچ پیامی ایموجی اضافه نمی‌گیرد. تنظیم روشن/خاموش هر حساب از پنل سلف
    # (دکمه peconv_toggle) خوانده می‌شود با کش کوتاه ۲ ثانیه‌ای تا هیچ ارسال
    # منتظر دیتابیس نماند. اگر خواندن دیتابیس (مثلاً قفل SQLite) موقتاً شکست
    # بخورد، آخرین مقدار معتبر استفاده می‌شود تا کانورتر بی‌دلیل خاموش نشود
    # (RC-D).
    def _converter_flag(_uid=uid, _cache={'value': None, 'at': 0.0}):
        now = time.monotonic()
        if now - _cache['at'] > 2.0:
            try:
                _cache['value'] = db.get_user_settings(_uid).get('premium_emoji_converter')
                _cache['at'] = now
            except Exception:
                if _cache['at'] == 0.0:
                    _cache['at'] = now  # اولین خواندن هم شکست خورد → پیش‌فرض config
                # در غیر این صورت آخرین مقدار معتبر حفظ می‌شود؛ None برنمی‌گردد
        return _cache['value']

    engine = install_premium_emoji_converter(client, account=me, is_enabled=_converter_flag)
    # 🔁 Premium Resend Mode — ارسال مجدد هوشمند (Copy/Delete/Resend).
    # بعد از هر پیام خروجی که ایموجی قابل‌نگاشت دارد ولی entity در نسخه
    # نهایی تلگرام نیست، پیام دقیقاً کپی، با Custom Emoji دوباره ارسال و
    # پیام اصلی حذف می‌شود. انتخاب پنل (premium_emoji_resend) اولویت دارد.
    def _resend_flag(_uid=uid, _cache={'value': None, 'at': 0.0}):
        now = time.monotonic()
        if now - _cache['at'] > 2.0:
            try:
                _cache['value'] = db.get_user_settings(_uid).get('premium_emoji_resend')
                _cache['at'] = now
            except Exception:
                if _cache['at'] == 0.0:
                    _cache['at'] = now
        return _cache['value']

    install_premium_resend(client, engine, is_enabled=_resend_flag, owner_id=uid)
    # 🔧 Post-Send Fix — فقط FALLBACK: پیام‌های خروجی از دستگاه‌های دیگر
    # (گوشی/اپ رسمی) که از wrapper های بالا عبور نکرده‌اند. قبل از فونت‌هندلر
    # ثبت می‌شود تا اولویت پردازش با آن باشد.
    install_premium_emoji_outgoing_injector(client, engine)
    tabchi_models.init_custom_emojis_db()

    import self_manager
    self_manager.register_client(uid, client)

    print(f"✅ سشن متصل شد: {me.id} — سرویس: {'فعال' if can_run(uid) else 'منتظر فعال‌سازی یا اعتبار'}")

    base_first = me.first_name or 'User'
    base_last = me.last_name or ''
    base_bio = ''

    try:
        full = await client(GetFullUserRequest(me))
        if hasattr(full, 'full_user') and hasattr(full.full_user, 'about'):
            base_bio = full.full_user.about or ''
    except Exception as e:
        print(f"⚠️ خطا در گرفتن بیو: {e}")

    db.update_user_settings(uid, {
        'base_first_name': base_first,
        'base_last_name': base_last,
        'base_bio': base_bio,
    })

    def is_self_on():
        """آیا سلف برای این اکانت روشن است؟"""
        return can_run(uid)

    # ثبت Anti Delete / Message Saver برای همین حساب
    register_deleted_message_handlers(client, uid)
    register_message_font_handler(client, uid)
    register_copy_protected_handlers(client, uid)
    # 💤 Away Message — پاسخ خودکار خصوصی + ریست با فعالیت مالک
    away_service.register_away_handlers(client, uid)

    # ========================================
    # 📥 پیام‌های ورودی (سکوت + حالت دشمن)
    # ========================================
    @client.on(events.NewMessage(incoming=True))
    async def incoming_handler(event):
        try:
            if not is_self_on():
                return
            s = db.get_user_settings(uid)
            chat_id = event.chat_id
            if chat_id in s['muted_chats']:
                try:
                    await event.delete()
                except Exception:
                    try:
                        await client.delete_messages(chat_id, event.id, revoke=False)
                    except Exception:
                        pass
                return
            if chat_id in s['enemy_chats']:
                lines = load_fosh_list()
                if lines:
                    try:
                        await event.reply(random.choice(lines))
                    except errors.FloodWaitError as e:
                        await asyncio.sleep(e.seconds)
                    except Exception as e:
                        print(f"⚠️ خطا حالت دشمن: {e}")
                return
        except Exception as e:
            print(f"⚠️ incoming_handler: {e}")

    # ========================================
    # 📌 دستور .پنل (همیشه فعال، حتی وقتی سلف خاموش است)
    # ========================================
    @client.on(events.NewMessage(outgoing=True, pattern=PATTERN_PANEL))
    async def panel_cmd(event):
        try:
            tabchi_models.clear_custom_emoji_flow(uid, 'self')
            await event.delete()
            results = await client.inline_query(INLINE_USERNAME, "")
            if results:
                await results[0].click(event.chat_id)
        except errors.BotResponseTimeoutError:
            await _temp_message(client, event.chat_id, "⚠️ **ربات پنل پاسخ نداد.**")
        except Exception as e:
            await _temp_message(client, event.chat_id, f"❌ خطا: `{e}`")

    # ========================================
    # 📌 دستور .بستن — بستن همه عملیات‌های در انتظار (همیشه فعال)
    #    پنل، wizard، ورودی متن/فایل، انتخاب صدا، تأییدها و تسک‌های نیمه‌کاره
    # ========================================
    @client.on(events.NewMessage(outgoing=True, pattern=PATTERN_CLOSE))
    async def close_pending_cmd(event):
        try:
            await event.delete()
            result = await close_all_pending(
                client, uid, event.chat_id,
                spam_tasks=active_spam_tasks,
                cleanup_tasks=active_cleanup_tasks,
            )
            log_state(event.chat_id, result)
            await _temp_message(client, event.chat_id, "✅ عملیات بسته شد")
        except Exception as e:
            print(f"⚠️ خطا در بستن عملیات‌ها: {e}")

    # ========================================
    # 💤 دستور .away — کنترل پیام خودکار آفلاین (فقط چت خصوصی)
    #    .away / .away on|off / .away text <متن> / .away reset
    # ========================================
    @client.on(events.NewMessage(outgoing=True, pattern=PATTERN_AWAY))
    async def away_cmd(event):
        try:
            args = (event.pattern_match.group(1) or '').strip()
            await event.delete()
            if not is_self_on():
                return
            settings = away_service.get_settings(uid)
            if not args:
                count = len(settings['away_sent_users'])
                state = '🟢 روشن' if settings['away_enabled'] else '🔴 خاموش'
                await client.send_message(
                    event.chat_id,
                    '💤 **Away Message**\n\n'
                    f'وضعیت: {state}\n'
                    f'متن: «{settings["away_text"]}»\n'
                    f'پیام‌گرفته‌ها (تا ریست بعدی): {count}\n\n'
                    'دستورها:\n'
                    '• `.away on` / `.away off`\n'
                    '• `.away text <متن جدید>`\n'
                    '• `.away reset` — ریست لیست ارسال‌شده‌ها',
                    parse_mode='md',
                )
                return
            action, _, rest = args.partition(' ')
            action = action.lower()
            if action in ('on', 'روشن'):
                away_service.set_enabled(uid, True)
                await client.send_message(event.chat_id,
                                          '✅ Away روشن شد؛ پاسخ خودکار فقط در چت خصوصی و برای هر کاربر یک بار ارسال می‌شود.',
                                          parse_mode=None)
            elif action in ('off', 'خاموش'):
                away_service.set_enabled(uid, False)
                await client.send_message(event.chat_id,
                                          '⛔️ Away خاموش شد.',
                                          parse_mode=None)
            elif action in ('text', 'متن'):
                if not rest.strip():
                    await client.send_message(
                        event.chat_id,
                        '❌ متن جدید را بعد از دستور بنویسید:\n`.away text سلام، بعداً جواب می‌دهم.`',
                        parse_mode=None)
                    return
                try:
                    saved = away_service.set_text(uid, rest.strip())
                except ValueError as exc:
                    await client.send_message(event.chat_id, f'❌ {exc}',
                                              parse_mode=None)
                    return
                await client.send_message(event.chat_id,
                                          f'✅ متن Away ذخیره شد.\n\n💤 {saved}',
                                          parse_mode=None)
            elif action in ('reset', 'ریست'):
                count = away_service.reset_sent_users(uid)
                await client.send_message(
                    event.chat_id,
                    f'🧹 لیست Away ریست شد ({count} کاربر)؛ برای همه دوباره یک بار پیام می‌رود.',
                    parse_mode=None)
            else:
                await client.send_message(
                    event.chat_id,
                    '❌ دستور نامعتبر است.\nنمونه: `.away on` | `.away off` | `.away text <متن>` | `.away reset`',
                    parse_mode=None)
        except Exception as e:
            print(f"⚠️ خطا در away: {e}")

    # ========================================
    # 💰 قیمت و تبدیل لحظه‌ای ارز دیجیتال
    # ========================================
    @client.on(events.NewMessage(outgoing=True, pattern=PATTERN_CRYPTO))
    async def crypto_cmd(event):
        if not is_self_on():
            return
        if not enabled('self_crypto'):
            await client.send_message(event.chat_id, CRYPTO_DISABLED, parse_mode=None)
            return
        try:
            raw = (getattr(event, 'raw_text', '') or '').strip()
            await event.delete()
            if raw == '.ارز':
                text = (
                    '💰 ارز دیجیتال\n\n'
                    'نمونه‌ها:\n'
                    '.ارز بیت کوین\n'
                    '.ارز 10 تتر\n'
                    '.ارز 100 داگز بیت کوین\n'
                    '.ارز 100 تون به تتر'
                )
            else:
                text = await execute_crypto_text(raw, self_command=True, user_id=uid)
            await client.send_message(event.chat_id, text, parse_mode=None)
        except Exception:
            await client.send_message(
                event.chat_id,
                '⚠️ دریافت قیمت موقتاً امکان‌پذیر نیست.',
                parse_mode=None,
            )

    # ========================================
    # 🌐 ترجمه چندزبانه مخصوص سلف
    # ========================================
    @client.on(events.NewMessage(outgoing=True, pattern=PATTERN_TRANSLATE))
    async def translate_cmd(event):
        if not is_self_on():
            return
        if not enabled('self_translate'):
            await client.send_message(event.chat_id, TRANSLATE_DISABLED, parse_mode=None)
            return
        try:
            raw = (getattr(event, 'raw_text', '') or '').strip()
            replied = await event.get_reply_message() if getattr(event, 'is_reply', False) else None
            reply_text = (getattr(replied, 'raw_text', None) or getattr(replied, 'message', '') or '') if replied else None
            response = await execute_translate_text(uid, raw, reply_text=reply_text)
            await event.delete()
            for chunk in split_telegram_text(response):
                await client.send_message(event.chat_id, chunk, parse_mode=None,
                                          reply_to=getattr(replied, 'id', None))
        except Exception:
            await client.send_message(
                event.chat_id,
                '⚠️ ترجمه موقتاً در دسترس نیست.',
                parse_mode=None,
            )

    # ========================================
    # 🤖 دستیار هوش مصنوعی AvalAI
    # ========================================
    @client.on(events.NewMessage(outgoing=True, pattern=PATTERN_AI))
    async def ai_cmd(event):
        if not is_self_on():
            return
        try:
            raw = (getattr(event, 'raw_text', '') or '').strip()
            await event.delete()
            response = await execute_ai_text(uid, raw)
            for chunk in split_telegram_text(response):
                await client.send_message(event.chat_id, chunk, parse_mode=None)
        except Exception:
            await client.send_message(
                event.chat_id,
                '⚠️ هوش مصنوعی موقتاً در دسترس نیست.',
                parse_mode=None,
            )

    # ========================================
    # ✨ Custom Emoji Manager input flow
    # ========================================
    @client.on(events.NewMessage(outgoing=True))
    async def custom_emoji_manager_input(event):
        flow = tabchi_models.get_custom_emoji_flow(uid, 'self')
        if not flow:
            return

        raw_text = (getattr(event, 'raw_text', '') or '').strip()
        # Never consume normal self commands while an old manager flow exists.
        if raw_text.startswith('.'):
            return

        step = flow.get('step')
        if step == 'extract':
            items = extract_custom_emojis(getattr(event, 'message', None))
            if not items:
                await client.send_message(
                    event.chat_id,
                    '❌ در این پیام MessageEntityCustomEmoji پیدا نشد.\n'
                    'یک پیام دارای Custom Emoji واقعی ارسال کنید.',
                    parse_mode=None,
                )
                raise events.StopPropagation

            source_message_id = int(
                getattr(event, 'id', 0)
                or getattr(getattr(event, 'message', None), 'id', 0)
                or 0
            )
            if source_message_id <= 0:
                await client.send_message(event.chat_id, '❌ شناسه پیام معتبر نیست.', parse_mode=None)
                raise events.StopPropagation

            try:
                saved = tabchi_models.save_custom_emojis(uid, items, source_message_id)
            except Exception:
                await client.send_message(
                    event.chat_id,
                    '❌ استخراج انجام شد اما ذخیره در دیتابیس ناموفق بود.',
                    parse_mode=None,
                )
                raise events.StopPropagation

            tabchi_models.clear_custom_emoji_flow(uid, 'self')
            report = detailed_result_text(items)
            await client.send_message(
                event.chat_id,
                f'{report}\n\n💾 {saved} ایموجی جدید ذخیره شد.',
                parse_mode=None,
            )
            raise events.StopPropagation

        if step == 'test':
            try:
                document_id = parse_document_id(raw_text)
                payload = await resolve_custom_emoji(client, document_id)
                # The numeric input is only a command/input; test output itself
                # is always a real MessageEntityCustomEmoji.
                try:
                    await event.delete()
                except Exception:
                    pass
                await send_custom_emoji(payload, client=client, peer=event.chat_id, timeout=20)
                tabchi_models.clear_custom_emoji_flow(uid, 'self')
            except EmojiError as exc:
                await client.send_message(event.chat_id, str(exc), parse_mode=None)
            except errors.FloodWaitError as exc:
                await client.send_message(
                    event.chat_id,
                    f'⏳ تلگرام تست را محدود کرده است؛ {exc.seconds} ثانیه بعد دوباره امتحان کنید.',
                    parse_mode=None,
                )
            except asyncio.TimeoutError:
                await client.send_message(
                    event.chat_id,
                    '❌ پاسخ تلگرام برای تست Custom Emoji به‌موقع دریافت نشد.',
                    parse_mode=None,
                )
            except Exception:
                await client.send_message(
                    event.chat_id,
                    '❌ این Document ID معتبر نیست یا دسترسی به Custom Emoji وجود ندارد.',
                    parse_mode=None,
                )
            raise events.StopPropagation

    # ========================================
    # 📌 دستور .info
    # ========================================
    @client.on(events.NewMessage(outgoing=True, pattern=PATTERN_INFO))
    async def info_cmd(event):
        if not is_self_on():
            return
        try:
            await event.delete()
            me2 = await client.get_me()
            s = db.get_user_settings(uid)
            bio_status = "🟢 فعال" if s['bio_clock'] else "⚪️ غیرفعال"
            ln_status = "🟢 فعال" if s['lastname_clock'] else "⚪️ غیرفعال"
            msg = await client.send_message(
                event.chat_id,
                f"📊 **وضعیت حساب و سرویس‌ها**\n\n"
                f"👤 حساب: `{me2.first_name or '-'} {me2.last_name or ''}`\n"
                f"🆔 شناسه: `{me2.id}`\n"
                f"🏷 یوزرنیم: `@{me2.username or 'ندارد'}`\n\n"
                f"⏰ **ساعت هوشمند:**\n"
                f"• بیو: {bio_status} — `{CLOCK_FONTS[s['bio_font']][0]}`\n"
                f"• نام خانوادگی: {ln_status} — `{CLOCK_FONTS[s['lastname_font']][0]}`\n\n"
                f"🕐 زمان سرور: `{get_tehran_time('%H:%M:%S')}`",
                parse_mode='md'
            )
            await asyncio.sleep(15)
            await msg.delete()
        except Exception as e:
            print(f"⚠️ info: {e}")

    # ========================================
    # 📌 دستور .ping
    # ========================================
    @client.on(events.NewMessage(outgoing=True, pattern=PATTERN_PING))
    async def ping_cmd(event):
        if not is_self_on():
            return
        try:
            await event.delete()
            msg = await client.send_message(event.chat_id, "📡 در حال بررسی پایداری اتصال...", parse_mode='md')
            start = asyncio.get_event_loop().time()
            await client.get_me()
            ping = (asyncio.get_event_loop().time() - start) * 1000
            if ping < 100:
                status, emoji = "🟢 عالی", "🚀"
            elif ping < 300:
                status, emoji = "🟡 متوسط", "⚡"
            else:
                status, emoji = "🔴 ضعیف", "⚠️"
            await msg.edit(
                f"📡 **نتیجه پایداری اتصال**\n\n🔹 تأخیر: `{ping:.2f}` ms\n🔹 وضعیت: {status} {emoji}",
                parse_mode='md'
            )
            await asyncio.sleep(10)
            await msg.delete()
        except Exception as e:
            print(f"⚠️ ping: {e}")

    # ========================================
    # 🚫 دستور .بلاک
    # ========================================
    @client.on(events.NewMessage(outgoing=True, pattern=PATTERN_BLOCK))
    async def block_cmd(event):
        if not is_self_on():
            return
        try:
            await event.delete()
            target = None
            if event.is_private:
                target = event.chat_id
            elif event.reply_to and event.reply_to.msg_id:
                try:
                    replied = await event.get_reply_message()
                    if replied and replied.sender_id:
                        target = replied.sender_id
                except Exception:
                    pass
            else:
                try:
                    ent = await event.get_chat()
                    if getattr(ent, 'is_user', False):
                        target = ent.id
                except Exception:
                    pass
            if not target:
                await _temp_message(
                    client,
                    event.chat_id,
                    "⚠️ **برای بلاک در گروه، روی پیام کاربر ریپلی بزن و بعد `.بلاک` را بفرست.**"
                )
                return
            await client(functions.contacts.BlockRequest(id=target))
            await _temp_message(client, event.chat_id, f"🚫 **کاربر `{target}` مسدود شد.**")
        except Exception as e:
            await _temp_message(client, event.chat_id, f"❌ خطا در بلاک: `{e}`")

    # ========================================
    # 🔇 دستور .سکوت
    # ========================================
    @client.on(events.NewMessage(outgoing=True, pattern=PATTERN_MUTE))
    async def mute_cmd(event):
        if not is_self_on():
            return
        try:
            chat_id = event.chat_id
            await event.delete()
            s = db.get_user_settings(uid)
            chats = list(s['muted_chats'])
            if chat_id in chats:
                chats.remove(chat_id)
                db.update_user_settings(uid, {'muted_chats': chats})
                await _temp_message(client, chat_id, "🔊 **حالت سکوت در این چت غیرفعال شد.**")
            else:
                chats.append(chat_id)
                db.update_user_settings(uid, {'muted_chats': chats})
                await _temp_message(
                    client,
                    chat_id,
                    "🔇 **حالت سکوت فعال شد.**\nهر پیام کاربر مقابل فوراً پاک می‌شود.\n\n💡 برای لغو: `.سکوت` یا `.کنسل`"
                )
        except Exception as e:
            print(f"⚠️ mute: {e}")

    # ========================================
    # 👹 دستور .دشمن
    # ========================================
    @client.on(events.NewMessage(outgoing=True, pattern=PATTERN_ENEMY))
    async def enemy_cmd(event):
        if not is_self_on():
            return
        try:
            chat_id = event.chat_id
            await event.delete()
            s = db.get_user_settings(uid)
            chats = list(s['enemy_chats'])
            if chat_id in chats:
                chats.remove(chat_id)
                db.update_user_settings(uid, {'enemy_chats': chats})
                await _temp_message(client, chat_id, "🕊 **حالت دشمن در این چت غیرفعال شد.**")
            else:
                chats.append(chat_id)
                db.update_user_settings(uid, {'enemy_chats': chats})
                lines = load_fosh_list()
                if not lines:
                    await _temp_message(
                        client,
                        chat_id,
                        "👹 **حالت دشمن فعال است**، اما در حال حاضر پاسخی برای ارسال آماده نیست.\n"
                        "برای توقف در همین چت، `.دشمن` یا `.کنسل` را بفرستید."
                    )
                else:
                    await _temp_message(
                        client,
                        chat_id,
                        "👹 **حالت دشمن فعال شد.**\nهر پیام کاربر مقابل با یک متن رندوم ریپلای می‌شود.\n\n💡 برای لغو: `.دشمن` یا `.کنسل`"
                    )
        except Exception as e:
            print(f"⚠️ enemy: {e}")

    # ========================================
    # 🎙 دستور .تبدیل صوت به متن
    # ========================================
    @client.on(events.NewMessage(outgoing=True, pattern=PATTERN_STT))
    async def speech_to_text_cmd(event):
        if not is_self_on():
            return
        temp_path = None
        progress = None
        try:
            replied = await event.get_reply_message()
            if not replied or not _is_audio_message(replied):
                await event.delete()
                await _temp_message(
                    client,
                    event.chat_id,
                    "🎙 برای تبدیل، روی یک ویس یا فایل صوتی ریپلای کن و `.تبدیل صوت به متن` را بفرست."
                )
                return
            file_info = getattr(replied, 'file', None)
            known_size = getattr(file_info, 'size', 0) or 0
            if known_size > AVALAI_AUDIO_MAX_BYTES:
                await event.delete()
                await _temp_message(
                    client,
                    event.chat_id,
                    "❌ حجم فایل صوتی بیشتر از محدودیت 25MB سرویس AvalAI است."
                )
                return
            await event.delete()
            progress = await client.send_message(
                event.chat_id,
                "🎙 در حال تبدیل صوت به متن...",
                reply_to=replied.id,
                parse_mode=None,
            )
            tmp = tempfile.NamedTemporaryFile(
                prefix='avalai_stt_',
                suffix=_audio_suffix(replied),
                dir=UPLOAD_DIR,
                delete=False,
            )
            temp_path = tmp.name
            tmp.close()
            downloaded = await client.download_media(replied, file=temp_path)
            if not downloaded or not os.path.isfile(temp_path):
                raise RuntimeError("دانلود فایل صوتی از تلگرام انجام نشد.")
            if os.path.getsize(temp_path) > AVALAI_AUDIO_MAX_BYTES:
                raise AvalAIError("حجم فایل صوتی بیشتر از محدودیت 25MB سرویس AvalAI است.")
            mime_type = getattr(file_info, 'mime_type', None)
            transcript = await transcribe_audio(
                temp_path,
                api_key=AVALAI_API_KEY,
                base_url=AVALAI_BASE_URL,
                model=AVALAI_STT_MODEL,
                timeout_seconds=AVALAI_REQUEST_TIMEOUT,
                mime_type=mime_type,
            )
            chunks = _split_plain_text(transcript)
            if not chunks:
                raise AvalAIError("متن قابل استفاده‌ای از فایل صوتی استخراج نشد.")
            await progress.edit(
                "📝 متن استخراج‌شده:\n\n" + chunks[0],
                parse_mode=None,
            )
            for chunk in chunks[1:]:
                await client.send_message(
                    event.chat_id,
                    chunk,
                    reply_to=replied.id,
                    parse_mode=None,
                )
        except AvalAIError as e:
            message = f"❌ تبدیل صوت به متن انجام نشد:\n{e}"
            if progress:
                try:
                    await progress.edit(message, parse_mode=None)
                except Exception:
                    await client.send_message(event.chat_id, message, parse_mode=None)
            else:
                await _temp_message(client, event.chat_id, message, delay=6)
        except Exception as e:
            print(f"⚠️ speech_to_text: {e}")
            message = "❌ در تبدیل صوت به متن خطایی رخ داد. دوباره تلاش کن."
            if progress:
                try:
                    await progress.edit(message, parse_mode=None)
                except Exception:
                    pass
        finally:
            if temp_path:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    # ========================================
    # 🎙 انتخاب صدا برای درخواست TTS در انتظار
    # ========================================
    @client.on(events.NewMessage(outgoing=True))
    async def tts_voice_choice(event):
        if not is_self_on():
            return
        voice_key = resolve_voice_choice(getattr(event, 'raw_text', ''))
        if not voice_key:
            return
        pending = getattr(client, "_tts_pending", {})
        item = pending.get(event.chat_id)
        if not item:
            return
        pending.pop(event.chat_id, None)
        client._tts_pending = pending
        temp_path = None
        status_msg = None
        try:
            await event.delete()
            set_user_voice(uid, voice_key)
            selected_label = get_voice_label(voice_key)
            selector_message_id = item.get("selector_message_id")
            if selector_message_id:
                try:
                    status_msg = await client.get_messages(event.chat_id, ids=selector_message_id)
                    if status_msg:
                        await status_msg.edit(
                            f"✅ صدا انتخاب شد: {selected_label}\n\n🔊 در حال ساخت صوت...",
                            parse_mode=None,
                        )
                except Exception:
                    status_msg = None
            if not status_msg:
                status_msg = await client.send_message(
                    event.chat_id,
                    f"✅ صدا انتخاب شد: {selected_label}\n\n🔊 در حال ساخت صوت...",
                    parse_mode=None,
                )
            temp_path = await text_to_speech(item["text"], uid)
            try:
                await client.send_file(
                    event.chat_id,
                    temp_path,
                    voice_note=True,
                    reply_to=item.get("reply_to"),
                )
            except Exception as voice_error:
                print(f"⚠️ ارسال به شکل ویس ناموفق بود، ارسال به شکل فایل صوتی: {voice_error}")
                await client.send_file(
                    event.chat_id,
                    temp_path,
                    voice_note=False,
                    reply_to=item.get("reply_to"),
                )
            if status_msg:
                try:
                    await status_msg.delete()
                except Exception:
                    pass
        except TTSError as e:
            message = f"❌ خطای ساخت صوت:\n{e}"
            if status_msg:
                try:
                    await status_msg.edit(message, parse_mode=None)
                except Exception:
                    await _temp_message(client, event.chat_id, message, delay=6)
            else:
                await _temp_message(client, event.chat_id, message, delay=6)
        except Exception as e:
            print(f"⚠️ tts_voice_choice: {e}")
            message = "❌ در تبدیل متن به صوت خطایی رخ داد. دوباره تلاش کن."
            if status_msg:
                try:
                    await status_msg.edit(message, parse_mode=None)
                except Exception:
                    pass
            else:
                await _temp_message(client, event.chat_id, message, delay=6)
        finally:
            if temp_path:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    # ========================================
    # 🔊 دستور .تبدیل متن به صوت
    # ========================================
    @client.on(events.NewMessage(outgoing=True, pattern=PATTERN_TTS))
    async def text_to_speech_cmd(event):
        if not is_self_on():
            return
        try:
            inline_text = event.pattern_match.group(1)
            text = (inline_text or '').strip()
            replied = None
            if not text:
                replied = await event.get_reply_message()
                if replied:
                    text = (getattr(replied, 'raw_text', '') or '').strip()
            if not text:
                await event.delete()
                await _temp_message(
                    client,
                    event.chat_id,
                    "🔊 روش استفاده:\n"
                    "• روی یک پیام متنی ریپلای کن و `.تبدیل متن به صوت` را بفرست.\n"
                    "• یا بنویس: `.تبدیل متن به صوت متن دلخواه`"
                )
                return
            if len(text) > AVALAI_TTS_MAX_CHARS:
                await event.delete()
                await _temp_message(
                    client,
                    event.chat_id,
                    f"❌ متن برای یک درخواست خیلی طولانی است؛ حداکثر `{AVALAI_TTS_MAX_CHARS}` کاراکتر بفرست."
                )
                return
            await event.delete()
            current_voice = get_user_voice(uid)
            if current_voice not in VOICE_OPTIONS:
                current_voice = DEFAULT_VOICE
            pending = getattr(client, "_tts_pending", {})
            old_item = pending.pop(event.chat_id, None)
            if old_item and old_item.get("selector_message_id"):
                try:
                    await client.delete_messages(event.chat_id, old_item["selector_message_id"])
                except Exception:
                    pass
            selector = await client.send_message(
                event.chat_id,
                f"🎙 صدای فعلی: {get_voice_label(current_voice)}\n\n{VOICE_MENU_TEXT}",
                reply_to=replied.id if replied else None,
                parse_mode=None,
            )
            pending[event.chat_id] = {
                "text": text,
                "reply_to": replied.id if replied else None,
                "selector_message_id": selector.id,
            }
            client._tts_pending = pending
        except Exception as e:
            print(f"⚠️ text_to_speech: {e}")
            await _temp_message(
                client,
                event.chat_id,
                "❌ در آماده‌سازی تبدیل متن به صوت خطایی رخ داد. دوباره تلاش کن.",
                delay=6,
            )

    # ========================================
    # 📨 دستور .اسپم
    # ========================================
    @client.on(events.NewMessage(outgoing=True, pattern=PATTERN_SPAM))
    async def spam_cmd(event):
        if not is_self_on():
            return
        try:
            count = int(event.pattern_match.group(1))
            text = event.pattern_match.group(2).strip()
            chat_id = event.chat_id
            key = (uid, chat_id)
            if key in active_spam_tasks or key in active_cleanup_tasks:
                await event.delete()
                await _temp_message(client, chat_id, "⚠️ **یک عملیات فعال در این چت وجود دارد.**\nبرای لغو: `.کنسل`")
                return
            if count < 1 or count > MAX_SPAM_COUNT:
                await event.delete()
                await _temp_message(client, chat_id, f"❌ تعداد باید بین `1` تا `{MAX_SPAM_COUNT}` باشد.")
                return
            if not text:
                await event.delete()
                await _temp_message(client, chat_id, "❌ متن اسپم نمی‌تواند خالی باشد.")
                return
            await event.delete()
            task = asyncio.create_task(spam_task(client, key, chat_id, count, text))
            active_spam_tasks[key] = task
            try:
                start_msg = await client.send_message(
                    chat_id,
                    f"🚀 **اسپم آغاز شد**\n\n📊 تعداد: `{count}` پیام\n📝 متن: `{text[:50]}`\n\n⛔️ برای لغو: `.کنسل`",
                    parse_mode='md'
                )
                await asyncio.sleep(3)
                await start_msg.delete()
            except Exception:
                pass
        except ValueError:
            await event.delete()
            await _temp_message(client, event.chat_id, "❌ تعداد باید عدد باشد.")
        except Exception as e:
            print(f"⚠️ خطا در اسپم: {e}")

    # ========================================
    # 🗑 دستور .حذف
    # ========================================
    @client.on(events.NewMessage(outgoing=True, pattern=PATTERN_DELETE))
    async def delete_cmd(event):
        if not is_self_on():
            return
        try:
            chat_id = event.chat_id
            key = (uid, chat_id)
            if key in active_spam_tasks or key in active_cleanup_tasks:
                await event.delete()
                await _temp_message(client, chat_id, "⚠️ **یک عملیات فعال در این چت وجود دارد.**\nبرای لغو: `.کنسل`")
                return
            count_str = event.pattern_match.group(1)
            count = int(count_str) if count_str else 10
            if count < 1 or count > MAX_DELETE_COUNT:
                await event.delete()
                await _temp_message(client, chat_id, f"❌ تعداد باید بین `1` تا `{MAX_DELETE_COUNT}` باشد.")
                return
            try:
                messages = await client.get_messages(chat_id, limit=count)
                message_ids = [m.id for m in messages]
                if event.id not in message_ids:
                    message_ids.insert(0, event.id)
                    message_ids = message_ids[:count]
            except Exception as e:
                await event.delete()
                await _temp_message(client, chat_id, f"❌ خطا در خواندن پیام‌ها: `{e}`")
                return
            if not message_ids:
                await event.delete()
                return
            task = asyncio.create_task(delete_task(client, key, chat_id, message_ids))
            active_cleanup_tasks[key] = task
        except Exception as e:
            print(f"⚠️ خطا در حذف: {e}")

    # ========================================
    # ❌ دستور .کنسل (لغو همه عملیات‌های فعال چت)
    # ========================================
    @client.on(events.NewMessage(outgoing=True, pattern=PATTERN_CANCEL))
    async def cancel_cmd(event):
        if not is_self_on():
            return
        try:
            chat_id = event.chat_id
            key = (uid, chat_id)
            cancelled_items = []
            if await _cancel_task(active_spam_tasks, key):
                cancelled_items.append("📨 اسپم")
            if await _cancel_task(active_cleanup_tasks, key):
                cancelled_items.append("🗑 پاک‌سازی پیام")
            pending_tts = getattr(client, "_tts_pending", {})
            pending_item = pending_tts.pop(chat_id, None)
            client._tts_pending = pending_tts
            if pending_item:
                cancelled_items.append("🔊 تبدیل متن به صوت")
                selector_message_id = pending_item.get("selector_message_id")
                if selector_message_id:
                    try:
                        await client.delete_messages(chat_id, selector_message_id)
                    except Exception:
                        pass
            s = db.get_user_settings(uid)
            patch = {}
            muted = list(s['muted_chats'])
            if chat_id in muted:
                patch['muted_chats'] = [c for c in muted if c != chat_id]
                cancelled_items.append("🔇 حالت سکوت")
            enemy = list(s['enemy_chats'])
            if chat_id in enemy:
                patch['enemy_chats'] = [c for c in enemy if c != chat_id]
                cancelled_items.append("👹 حالت دشمن")
            if patch:
                db.update_user_settings(uid, patch)
            await event.delete()
            if cancelled_items:
                await _temp_message(
                    client,
                    chat_id,
                    "⛔️ **عملیات با موفقیت لغو شد**\n\nموارد متوقف‌شده در این چت:\n" +
                    "\n".join(f"• {x}" for x in cancelled_items)
                )
            else:
                await _temp_message(client, chat_id, "⚪️ **هیچ عملیات فعالی در این چت وجود ندارد.**")
        except Exception as e:
            print(f"⚠️ خطا در کنسل: {e}")

    # ========================================
    # 📥 دستور .کپی محتوا (لینک)
    # ========================================
    @client.on(events.NewMessage(outgoing=True, pattern=PATTERN_COPY))
    async def copy_protected_cmd(event):
        if not is_self_on():
            return
        try:
            link_text = (event.pattern_match.group(1) or '').strip()
            if not link_text:
                await event.delete()
                await _temp_message(
                    client,
                    event.chat_id,
                    "📥 **کپی محتوای کانال‌های قفل**\n\n"
                    "**روش استفاده:**\n"
                    "• `.کپی محتوا https://t.me/username/123`\n"
                    "• `.کپی محتوا https://t.me/c/4421725618/3`\n\n"
                    "بعد از ارسال لینک، از شما مقصد آپلود پرسیده می‌شود.\n"
                    "مقصد می‌تواند:\n"
                    "• `me` → Saved Messages\n"
                    "• `@username` یا آیدی عددی یک کاربر/گروه/کانال",
                )
                return

            link_info = parse_message_link(link_text)
            if not link_info:
                await event.delete()
                await _temp_message(
                    client,
                    event.chat_id,
                    "❌ **لینک معتبر نیست.**\n\n"
                    "فرمت‌های قابل قبول:\n"
                    "• `https://t.me/username/123`\n"
                    "• `https://t.me/c/4421725618/3`",
                )
                return

            try:
                source_entity = await resolve_peer(client, link_info)
                messages = await client.get_messages(
                    source_entity, ids=int(link_info['message_id'])
                )
                target_message = messages[0] if isinstance(messages, list) else messages
                if target_message is None:
                    raise ValueError('پیام پیدا نشد.')
            except ValueError:
                await event.delete()
                await _temp_message(
                    client,
                    event.chat_id,
                    "❌ **به این پیام دسترسی نداری.**\n\n"
                    "باید عضو کانال/گروه باشی تا بتوانی محتوای آن را کپی کنی.",
                )
                return
            except Exception as exc:
                await event.delete()
                await _temp_message(
                    client,
                    event.chat_id,
                    f"❌ **خطا در دسترسی به پیام:**\n`{exc}`",
                )
                return

            try:
                source_title = (
                    getattr(source_entity, 'title', None)
                    or f"{getattr(source_entity, 'first_name', '')} "
                       f"{getattr(source_entity, 'last_name', '') or ''}".strip()
                    or 'نامشخص'
                )
            except Exception:
                source_title = 'نامشخص'

            cancel_destination_capture(uid)
            await event.delete()

            begin_destination_capture(
                uid,
                payload={
                    'link_info': link_info,
                    'source_title': source_title,
                    'message_id': int(link_info['message_id']),
                },
            )

            kind_label = _get_message_type_label(target_message)

            await client.send_message(
                event.chat_id,
                "📥 **پیام مبدأ شناسایی شد**\n\n"
                f"📦 نوع: `{kind_label}`\n"
                f"📤 منبع: `{source_title}`\n\n"
                "🎯 **مقصد آپلود را ارسال کن:**\n"
                "• `me` → Saved Messages\n"
                "• `@username` → کاربر/گروه/کانال\n"
                "• آیدی عددی (مثلاً `123456789`)\n\n"
                "⏳ این درخواست تا ۵ دقیقه معتبر است.\n"
                "✖️ برای لغو کلمه `لغو` را بفرست.",
                parse_mode='md',
            )
        except Exception as e:
            print(f"⚠️ copy_protected: {e}")
            await _temp_message(
                client,
                event.chat_id,
                f"❌ خطا در کپی محتوا: `{e}`",
            )

    # ========================================
    # 🕐 آپدیت‌کننده ساعت
    # ========================================
    async def clock_updater():
        last_bio_time = None
        last_ln_time = None
        last_state = False
        while True:
            try:
                s = db.get_user_settings(uid)
                self_on = can_run(uid)
                bio_enabled = self_on and bool(s['bio_clock'])
                ln_enabled = self_on and bool(s['lastname_clock'])
                any_enabled = bio_enabled or ln_enabled
                if any_enabled:
                    now = get_tehran_datetime()
                    time_str = now.strftime("%H:%M")
                    bio_func = CLOCK_FONTS.get(s['bio_font'], CLOCK_FONTS[1])[1]
                    ln_func = CLOCK_FONTS.get(s['lastname_font'], CLOCK_FONTS[1])[1]
                    try:
                        bio_formatted = bio_func(time_str)
                    except Exception:
                        bio_formatted = time_str
                    try:
                        ln_formatted = ln_func(time_str)
                    except Exception:
                        ln_formatted = time_str
                    bio_changed = bio_enabled and bio_formatted != last_bio_time
                    ln_changed = ln_enabled and ln_formatted != last_ln_time
                    if bio_changed or ln_changed or not last_state:
                        first_name = (s['base_first_name'] or base_first or 'User')[:64]
                        if ln_enabled:
                            last_name = ln_formatted[:64]
                        else:
                            last_name = (s['base_last_name'] or '')[:64]
                        if bio_enabled:
                            b_bio = s['base_bio'] or ''
                            about = f"{b_bio} | ⏰ {bio_formatted}" if b_bio else f"⏰ {bio_formatted}"
                            about = about[:70]
                        else:
                            about = (s['base_bio'] or '')[:70]
                        try:
                            await client(UpdateProfileRequest(
                                first_name=first_name,
                                last_name=last_name,
                                about=about
                            ))
                            if bio_enabled:
                                last_bio_time = bio_formatted
                            if ln_enabled:
                                last_ln_time = ln_formatted
                        except errors.FloodWaitError as e:
                            print(f"⏳ {uid}: Flood Wait {e.seconds}s")
                            await asyncio.sleep(e.seconds)
                            continue
                        except Exception as e:
                            print(f"⚠️ {uid}: خطا آپدیت پروفایل: {e}")
                elif last_state:
                    try:
                        first_name = (s['base_first_name'] or base_first or 'User')[:64]
                        last_name = (s['base_last_name'] or '')[:64]
                        about = (s['base_bio'] or '')[:70]
                        await client(UpdateProfileRequest(
                            first_name=first_name,
                            last_name=last_name,
                            about=about
                        ))
                        print(f"🔄 {uid}: برگشت به مقادیر اصلی")
                        last_bio_time = None
                        last_ln_time = None
                    except errors.FloodWaitError as e:
                        await asyncio.sleep(e.seconds)
                        continue
                    except Exception as e:
                        print(f"⚠️ {uid}: خطا در بازگشت پروفایل: {e}")
                last_state = any_enabled
                await asyncio.sleep(UPDATE_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️ {uid}: خطا clock_updater: {e}")
                await asyncio.sleep(60)

    clock_task = asyncio.create_task(clock_updater())

    try:
        if can_run(uid):
            await client.send_message('me', ui.SELF_STARTED_TEXT, parse_mode='md')
    except Exception:
        pass

    try:
        await client.run_until_disconnected()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"❌ سشن {sid} قطع شد: {e}")
    finally:
        clock_task.cancel()
        try:
            await clock_task
        except (asyncio.CancelledError, Exception):
            pass
        for store in (active_spam_tasks, active_cleanup_tasks):
            for key, task in list(store.items()):
                try:
                    if key[0] == uid:
                        task.cancel()
                        try:
                            await task
                        except (asyncio.CancelledError, Exception):
                            pass
                        if store.get(key) is task:
                            del store[key]
                except Exception:
                    pass
        try:
            import self_manager
            self_manager.unregister_client(uid, client)
        except Exception:
            pass
