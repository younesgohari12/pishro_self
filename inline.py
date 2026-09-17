"""ربات اینلاین پنل سلف - فقط برای اکانتی که سلف روی آن نصب است"""
import os
import asyncio
import aiohttp
import config
from telethon import TelegramClient, events
from config import (
    API_ID,
    API_HASH,
    INLINE_BOT_TOKEN,
    INLINE_USERNAME,
    BOT_TOKEN,
    CLOCK_FONTS,
    SESSIONS_DIR,
    get_tehran_time
)
import db
import ui
from services.feature_flags import filter_buttons, disabled_callback_message
from services import away as away_service
from database import models as tabchi_models
from handlers.save_message import (
    begin_destination_capture,
    cancel_destination_capture,
    render_destination_prompt,
    render_save_menu,
    render_save_stats,
)
from handlers.font import (
    render_font_menu,
    render_font_preview,
    render_font_selector,
)
from services.font_formatter import FONT_STYLES, normalize_font_style
from services.deleted_handler import purge_owner_cache_files
from services.logging_service import log_user_action
from services.crypto_api import crypto_api, CryptoNotFound, CryptoAPIUnavailable
from services.price_converter import format_number, format_toman


def has_self_session(uid):
    """فقط اکانتی که سلف روی آن نصب است به پنل دسترسی دارد"""
    return os.path.exists(os.path.join(SESSIONS_DIR, f"user_{uid}.txt"))


# ========================================
# 🧰 راهنمای قابلیت‌ها
# ========================================
FEATURES = {
    'delete': {
        'title': '🗑 پاک‌سازی پیام',
        'text': (
            "🗑 **پاک‌سازی پیام‌ها**\n\n"
            "**کاربرد:** حذف دسته‌ای پیام‌های اخیر یک چت؛ هم پیام‌های خودت و هم طرف مقابل.\n\n"
            "**نحوه استفاده:**\n"
            "• `.حذف 10` → حذف 10 پیام اخیر چت\n"
            "• `.حذف 50` → حذف 50 پیام اخیر چت\n"
            "• `.حذف` → پیش‌فرض 10 پیام\n\n"
            "**لغو عملیات:** `.کنسل`"
        ),
    },
    'spam': {
        'title': '📨 اسپم پیام',
        'text': (
            "📨 **اسپم پیام**\n\n"
            "**کاربرد:** ارسال پشت‌سرهم یک متن در چت فعلی.\n\n"
            "**نحوه استفاده:**\n"
            "• `.اسپم 100 سلام` → 100 بار پیام «سلام»\n\n"
            "**لغو عملیات:** `.کنسل`"
        ),
    },
    'cancel': {
        'title': '⛔️ کنسل عملیات',
        'text': (
            "⛔️ **کنسل عملیات**\n\n"
            "**کاربرد:** توقف فوری هر عملیات فعال در چت فعلی.\n\n"
            "**موارد قابل لغو با `.کنسل`:**\n"
            "• اسپم فعال\n"
            "• پاک‌سازی پیام فعال\n"
            "• حالت سکوت\n"
            "• حالت دشمن\n\n"
            "**نحوه استفاده:**\n"
            "• `.کنسل`"
        ),
    },
    'fonts': {
        'title': '🎨 فونت ساعت',
        'text': (
            "🎨 **فونت ساعت**\n\n"
            "**کاربرد:** تغییر استایل نمایش ساعت در بیو و نام خانوادگی.\n\n"
            "**نحوه استفاده:**\n"
            "• `.پنل` → «سایر قابلیت‌ها» → «ساعت و فونت» → «فونت بیو» یا «فونت نام خانوادگی»"
        ),
    },
    'info': {
        'title': '📊 اطلاعات حساب',
        'text': (
            "📊 **اطلاعات حساب**\n\n"
            "**کاربرد:** مشاهده وضعیت حساب و سرویس‌های فعال.\n\n"
            "**نحوه استفاده:**\n"
            "• `.info`"
        ),
    },
    'ping': {
        'title': '📡 پینگ اتصال',
        'text': (
            "📡 **پینگ اتصال**\n\n"
            "**کاربرد:** بررسی پایداری و تأخیر اتصال سلف‌بات.\n\n"
            "**نحوه استفاده:**\n"
            "• `.ping`"
        ),
    },
    'block': {
        'title': '🚫 بلاک کاربر',
        'text': (
            "🚫 **بلاک کاربر**\n\n"
            "**کاربرد:** مسدود کردن کاربر مقابل.\n\n"
            "**نحوه استفاده:**\n"
            "• در چت خصوصی: `.بلاک`\n"
            "• در گروه: روی پیام کاربر ریپلی بزن و `.بلاک` را بفرست"
        ),
    },
    'mute': {
        'title': '🔇 سکوت چت',
        'text': (
            "🔇 **سکوت چت**\n\n"
            "**کاربرد:** هر پیامی کاربر مقابل بفرستد، فوراً پاک می‌شود.\n\n"
            "**نحوه استفاده:**\n"
            "• `.سکوت` → فعال/غیرفعال در چت فعلی\n"
            "• `.کنسل` → غیرفعال‌سازی سریع در چت فعلی"
        ),
    },
    'enemy': {
        'title': '👹 حالت دشمن',
        'text': (
            "👹 **حالت دشمن**\n\n"
            "با فعال‌کردن این حالت، در چت فعلی به پیام‌های دریافتی پاسخ خودکار داده می‌شود.\n\n"
            "• فعال یا غیرفعال کردن: `.دشمن`\n"
            "• توقف سریع: `.کنسل`\n\n"
            "این تنظیم فقط روی همان چتی اعمال می‌شود که دستور را در آن می‌فرستید."
        ),
    },
    'copy': {
        'title': '📥 کپی محتوا',
        'text': (
            "📥 **کپی محتوای کانال‌های قفل**\n\n"
            "**کاربرد:** کپی پیام از کانال‌ها/گروه‌هایی که فوروارد در آن‌ها ممنوع است "
            "و ارسال آن به مقصد دلخواه (حتی Saved Messages).\n\n"
            "**نحوه استفاده:**\n"
            "• `.کپی محتوا https://t.me/username/123`\n"
            "• `.کپی محتوا https://t.me/c/4421725618/3`\n\n"
            "**مقاصد قابل انتخاب:**\n"
            "• `me` → Saved Messages\n"
            "• `@username` → کاربر/گروه/کانال\n"
            "• آیدی عددی\n\n"
            "**نکته:** باید عضو کانال/گروه مبدأ باشی."
        ),
    },
    'stt': {
        'title': '🎙 صوت به متن',
        'text': (
            "🎙 **تبدیل صوت به متن با AvalAI**\n\n"
            "**کاربرد:** تبدیل ویس یا فایل صوتی به متن.\n\n"
            "**نحوه استفاده:**\n"
            "• روی ویس/صوت ریپلای بزن\n"
            "• سپس `.تبدیل صوت به متن` را بفرست\n\n"
            "**محدودیت:** فایل صوتی حداکثر حدود 25MB."
        ),
    },
    'tts': {
        'title': '🔊 متن به صوت',
        'text': (
            "🔊 **تبدیل متن به صوت با AvalAI**\n\n"
            "**روش اول:** روی پیام متنی ریپلای بزن و `.تبدیل متن به صوت` را بفرست.\n\n"
            "**روش دوم:** متن را مستقیم بعد از دستور بنویس:\n"
            "• `.تبدیل متن به صوت سلام، حالت چطوره؟`\n\n"
            "بعد از دستور، لیست انتخاب صدا نمایش داده می‌شود. فقط یکی از این گزینه‌ها را بفرست:\n"
            " `مرد` / `زن` / `جوان` / `پیر` / `رسمی` / `خودمونی`\n\n"
            "خروجی ابتدا به شکل ویس ارسال می‌شود و اگر تلگرام نپذیرد، به شکل فایل صوتی ارسال خواهد شد."
        ),
    },
    'translate': {
        'title': '🌐 ترجمه',
        'text': (
            "🌐 **ترجمه چندزبانه در سلف**\n\n"
            "نمونه‌ها:\n"
            "• `.ترجمه فارسی به انگلیسی سلام`\n"
            "• `.ترجمه انگلیسی به فارسی hello`\n"
            "• روی متن ریپلای کن و بنویس `.ترجمه فارسی` → تشخیص خودکار زبان مبدا"
        ),
    },
    'ai': {
        'title': '🤖 هوش مصنوعی',
        'text': (
            "🤖 **دستیار هوش مصنوعی AvalAI**\n\n"
            "نمونه‌ها:\n"
            "• `.ai سلام خوبی؟`\n"
            "• `.هوش یک ایده برای پروژه بده`\n"
            "• `.هوش مصنوعی آخرین نسخه Python چیست؟`\n\n"
            "حافظه 20 پیام آخر نگهداری می‌شود و سؤال‌های زمان‌حساس در صورت تنظیم Search API به‌صورت خودکار جستجو می‌شوند."
        ),
    },
}


def premium_converter_effective(uid):
    """وضعیت مؤثر مبدل برای یک حساب (همان منطق Production-Safe موتور).

    1) PREMIUM_EMOJI_ENABLED (کلید اصلی) خاموش باشد → همیشه خاموش.
    2) انتخاب صریح پنل (True/False از دیتابیس) بر پیش‌فرض اولویت دارد.
    3) None یعنی کاربر هنوز دکمه را لمس نکرده → پیش‌فرض PREMIUM_EMOJI_CONVERTER_ENABLED.
    """
    if not bool(getattr(config, 'PREMIUM_EMOJI_ENABLED', True)):
        return False
    flag = db.get_user_settings(uid).get('premium_emoji_converter')
    if flag is None:
        return bool(getattr(config, 'PREMIUM_EMOJI_CONVERTER_ENABLED', True))
    return bool(flag)


def premium_resend_effective(uid):
    """وضعیت مؤثر Resend هوشمند (Copy/Delete/Resend) برای یک حساب.

    1) PREMIUM_EMOJI_RESEND_MODE (کلید سخت config) خاموش باشد → همیشه خاموش.
    2) کانورتر هم باید مؤثراً فعال باشد (Resend بدون تبدیل معنا ندارد).
    3) انتخاب صریح پنل (True/False از دیتابیس) اولویت دارد؛ None → پیش‌فرض روشن.
    """
    if not bool(getattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)):
        return False
    if not premium_converter_effective(uid):
        return False
    flag = db.get_user_settings(uid).get('premium_emoji_resend')
    if flag is None:
        return True  # Production-Safe: پیش‌فرض روشن مگر کاربر خاموش کند
    return bool(flag)


def build_away_menu(uid):
    """صفحه پنل پیام عدم حضور — وضعیت، متن، لیست چت‌های پاسخ داده شده."""
    settings = away_service.get_settings(uid)
    enabled = settings['away_enabled']
    preview = settings['away_text']
    if len(preview) > 120:
        preview = preview[:120] + '…'
    sent_chats = list((settings.get('away_sent_chats') or {}).keys())
    notified = len(sent_chats)
    preview_chats = ', '.join(sent_chats[:8]) + ('…' if notified > 8 else '')
    text = (
        "💤 **پیام عدم حضور**\n\n"
        f"وضعیت: {'🟢 روشن' if enabled else '🔴 خاموش'}\n"
        f"متن فعلی:\n«{preview}»\n\n"
        f"لیست چت‌های پاسخ داده شده: **{notified}**\n"
        f"«{preview_chats or '—'}»\n\n"
        "• این پاسخ فقط در **چت خصوصی** و برای هر چت **یک بار** ارسال می‌شود.\n"
        "• اکانت آفلاین می‌ماند: بدون typing، بدون read، بدون status.\n"
        "• لیست فقط با خاموش/روشن کردن، «پاک کردن لیست» یا شروع مجدد سلف ریست "
        "می‌شود؛ پیام‌های خروجی هرگز لیست را ریست نمی‌کنند."
    )
    buttons = [
        [ui.inline_button(
            '🔴 خاموش کردن' if enabled else '🟢 روشن کردن',
            b"away_toggle", "danger" if enabled else "success")],
        [ui.inline_button("✏️ تغییر متن", b"away_text_change", "primary")],
        [ui.inline_button("🧹 پاک کردن لیست", b"away_reset", "secondary")],
        [ui.inline_button("↩️ بازگشت", b"back_main", "secondary")],
    ]
    return text, buttons


def build_main_menu(uid, main_bot_username):
    """پنل اصلی یکپارچه برای دستور .پنل."""
    username = (main_bot_username or '').lstrip('@')
    text = "🎛 **پنل مدیریت**\n\nقابلیت موردنظر را انتخاب کن:"
    buttons = [
        [ui.inline_button("🎙 تبدیل متن به صوت", b"feat_tts", "success")],
    ]
    if username:
        buttons.append([ui.url_button("🤖 تبچی", f"https://t.me/{username}?start=tabchi", "primary")])
    else:
        buttons.append([ui.inline_button("🤖 تبچی (ربات اصلی در دسترس نیست)", b"no_tabchi_link", "secondary")])
    buttons += [
        [ui.inline_button("✨ ایموجی ویژه", b"cem_menu", "primary")],
        [ui.inline_button(
            f"🎨 ایموجی ویژه: {'🟢 روشن' if premium_converter_effective(uid) else '🔴 خاموش'}",
            b"peconv_toggle", "success" if premium_converter_effective(uid) else "danger")],
        [ui.inline_button(
            f"🔁 ارسال دوباره ایموجی ویژه: {'🟢 روشن' if premium_resend_effective(uid) else '🔴 خاموش'}",
            b"peresend_toggle", "success" if premium_resend_effective(uid) else "danger")],
        [ui.inline_button(
            f"💤 پیام عدم حضور: {'🟢 روشن' if away_service.get_settings(uid)['away_enabled'] else '🔴 خاموش'}",
            b"away_menu", "success" if away_service.get_settings(uid)['away_enabled'] else "danger")],
        [ui.inline_button("💰 ارز دیجیتال", b"icrypto_menu", "success")],
        [
            ui.inline_button("🌐 ترجمه", b"feat_translate", "primary"),
            ui.inline_button("🤖 هوش مصنوعی", b"feat_ai", "success"),
        ],
        [ui.inline_button("💾 سیو پیام", b"save_menu", "primary")],
        [ui.inline_button("🔤 فونت", b"msgfont_menu", "primary")],
        [ui.inline_button("📊 آمار", b"panel_stats", "primary")],
        [ui.inline_button("⚙️ سایر قابلیت‌ها", b"other_features", "secondary")],
        [ui.inline_button("❌ بستن عملیات", b"close", "danger")],
    ]
    return text, filter_buttons(buttons)



def build_inline_crypto_menu():
    text = (
        "💰 **ارز دیجیتال**\n\n"
        "قیمت لحظه‌ای و تبدیل هزاران ارز با دستور `.ارز`."
    )
    buttons = [
        [ui.inline_button("📊 قیمت ارز", b"icrypto_price", "primary")],
        [ui.inline_button("🔄 تبدیل ارز", b"icrypto_convert", "success")],
        [ui.inline_button("🔥 ارزهای محبوب", b"icrypto_popular", "primary")],
        [ui.inline_button('⚙️ تنظیمات', b'icrypto_settings', 'secondary')],
        [ui.inline_button("↩️ بازگشت", b"back_main", "secondary")],
    ]
    return text, buttons


def build_inline_crypto_settings(uid):
    settings = tabchi_models.get_crypto_settings(uid)
    favorites = ', '.join(settings['favorite_coins'])
    text = (
        "⚙️ **تنظیمات ارز دیجیتال**\n\n"
        f"⭐ ارزهای محبوب: `{favorites}`\n\n"
        "برای تغییر لیست محبوب‌ها از ربات اصلی → 💰 ارز دیجیتال → ⚙️ تنظیمات استفاده کن."
    )
    return text, [[ui.inline_button("↩️ ارز دیجیتال", b"icrypto_menu", "secondary")]]


async def build_inline_crypto_popular(uid):
    favorites = tabchi_models.get_crypto_settings(uid)['favorite_coins'][:10]
    lines = ["🔥 **ارزهای محبوب**", ""]
    for symbol in favorites:
        try:
            q = await crypto_api.quote(symbol)
            change = '' if q.change_24h is None else f" ({q.change_24h:+.2f}%)"
            lines.append(f"• **{q.coin.symbol.upper()}** — ${format_number(q.price_usd)}{change}")
            lines.append(f"  🇮🇷 {format_toman(q.price_toman)} تومان")
        except (CryptoNotFound, CryptoAPIUnavailable):
            lines.append(f"• **{symbol}** — ⚠️ در دسترس نیست")
    return '\n'.join(lines), [[ui.inline_button("↩️ ارز دیجیتال", b"icrypto_menu", "secondary")]]


def build_self_emoji_menu():
    text = (
        "✨ **ایموجی ویژه**\n\n"
        "استخراج، تست واقعی Entity و مدیریت ایموجی‌های ذخیره‌شده."
    )
    buttons = [
        [ui.inline_button("✨ استخراج ایموجی", b"cem_extract", "primary")],
        [ui.inline_button("🧪 تست ایموجی", b"cem_test", "success")],
        [ui.inline_button("📦 ایموجی‌های ذخیره شده", b"cem_list_0", "primary")],
        [ui.inline_button("↩️ بازگشت", b"back_main", "secondary")],
    ]
    return text, buttons


def build_self_emoji_list(uid, page=0):
    rows, total, page = tabchi_models.custom_emoji_page(int(uid), int(page))
    pages = max(1, (total + 19) // 20)
    text = f"📦 **ایموجی‌های ذخیره شده**\n\nتعداد: **{total}**\n\n"
    buttons = []
    if rows:
        text += '\n'.join(
            f"▫️ {row.get('emoji') or row.get('emoji_text') or '✨'}  `{row['document_id']}`"
            for row in rows
        )
        text += f"\n\nصفحهٔ {page + 1} از {pages}"
        for row in rows:
            buttons.append([
                ui.inline_button(
                    f"🗑 {row['document_id']}",
                    f"cem_del_{row['id']}_{page}",
                    "danger",
                    icon=False,
                )
            ])
    else:
        text += "هنوز ایموجی ذخیره نشده است."
    nav = []
    if page > 0:
        nav.append(ui.inline_button("⬅️ قبلی", f"cem_list_{page - 1}", "secondary"))
    if (page + 1) * 20 < total:
        nav.append(ui.inline_button("بعدی ➡️", f"cem_list_{page + 1}", "secondary"))
    if nav:
        buttons.append(nav)
    buttons.append([ui.inline_button("↩️ ایموجی پرمیوم", b"cem_menu", "secondary")])
    return text, buttons


def build_panel_stats(uid):
    """آمار سطح اول پنل .پنل برای همان کاربر."""
    s = db.get_user_settings(uid)
    try:
        banners = tabchi_models.list_banners(uid)
        blacklist_count = tabchi_models.blacklist_count(uid)
    except Exception:
        banners = []
        blacklist_count = 0
    active = sum(1 for b in banners if b.get('status') == 'active')
    self_status = "🟢 روشن" if s.get('self_enabled') else "🔴 خاموش"
    save_settings = tabchi_models.get_save_settings(uid)
    save_stats = tabchi_models.get_deleted_message_stats(uid)
    save_status = "🟢 فعال" if save_settings.get('status') else "🔴 غیرفعال"
    font_status = "🟢 فعال" if s.get("message_font_enabled", False) else "🔴 خاموش"
    font_style = normalize_font_style(s.get("message_font_style"))
    text = (
        "📊 **آمار**\n\n"
        f"🤖 وضعیت سلف: **{self_status}**\n"
        f"💾 سیو پیام: **{save_status}**\n"
        f"🔤 فونت پیام: **{font_status}** — {FONT_STYLES[font_style]}\n"
        f"🚨 پیام حذف‌شده ذخیره‌شده: **{save_stats.get('total', 0)}**\n"
        f"📢 کل بنرها: **{len(banners)}**\n"
        f"🟢 بنرهای فعال: **{active}**\n"
        f"🚫 بلک لیست: **{blacklist_count}**\n"
        f"💎 موجودی: **{int(s.get('diamonds', 0))}** الماس"
    )
    return text, [[ui.inline_button("↩️ بازگشت", b"back_main", "secondary")]]


def build_other_capabilities_menu(uid):
    """قابلیت‌های تکمیلی سلف."""
    s = db.get_user_settings(uid)
    time_now = get_tehran_time("%H:%M:%S")
    if s['bio_clock'] and s['lastname_clock']:
        clock_status = "🟢 بیو + نام خانوادگی"
    elif s['bio_clock']:
        clock_status = "🟢 فقط بیو"
    elif s['lastname_clock']:
        clock_status = "🟢 فقط نام خانوادگی"
    else:
        clock_status = "⚪️ غیرفعال"
    self_status = "🟢 روشن" if s['self_enabled'] else "🔴 خاموش"
    text = (
        "⚙️ **سایر قابلیت‌ها**\n\n"
        f"🤖 سرویس: **{self_status}**\n"
        f"⏰ ساعت پروفایل: **{clock_status}**\n"
        f"🕐 زمان: `{time_now}`\n\n"
        "قابلیت موردنظر را انتخاب کن:"
    )
    buttons = [
        [
            ui.inline_button("⏰ ساعت و فونت", b"clk_menu", "primary"),
            ui.inline_button("👁 پیش‌نمایش", b"preview", "success"),
        ],
        [
            ui.inline_button("🗑 پاک‌سازی", b"feat_delete", "danger"),
            ui.inline_button("📨 اسپم پیام", b"feat_spam", "primary"),
        ],
        [
            ui.inline_button("🚫 بلاک کاربر", b"feat_block", "danger"),
            ui.inline_button("🔇 سکوت چت", b"feat_mute", "secondary"),
        ],
        [
            ui.inline_button("👹 حالت دشمن", b"feat_enemy", "secondary"),
            ui.inline_button("📥 کپی محتوا", b"feat_copy", "warning"),
        ],
        [
            ui.inline_button("⛔ توقف عملیات", b"feat_cancel", "danger"),
            ui.inline_button("🎙 صوت → متن", b"feat_stt", "success"),
        ],
        [
            ui.inline_button("🔊 متن → صوت", b"feat_tts", "success"),
            ui.inline_button("🎨 راهنمای فونت", b"feat_fonts", "primary"),
        ],
        [
            ui.inline_button("📊 اطلاعات حساب", b"feat_info", "primary"),
            ui.inline_button("📡 تست اتصال", b"feat_ping", "success"),
        ],
        [ui.inline_button("↩️ بازگشت", b"back_main", "secondary")],
    ]
    return text, buttons


def build_feature_detail(uid, key):
    item = FEATURES.get(key)
    if not item:
        return build_other_capabilities_menu(uid)
    buttons = [[ui.inline_button("↩️ سایر قابلیت‌ها", b"other_features", "secondary")]]
    return item['text'], buttons


def build_clock_menu(uid):
    s = db.get_user_settings(uid)
    bio_status = "فعال" if s['bio_clock'] else "غیرفعال"
    ln_status = "فعال" if s['lastname_clock'] else "غیرفعال"
    time_now = get_tehran_time("%H:%M:%S")
    text = (
        "⏰ **ساعت هوشمند پروفایل**\n\n"
        f"🕐 زمان فعلی: `{time_now}`\n\n"
        "نمایش ساعت و فونت هر بخش را از دکمه‌های زیر تنظیم کن."
    )
    buttons = [
        [
            ui.inline_button(
                f"📝 بیو: {bio_status}",
                b"bio_toggle",
                "success" if s['bio_clock'] else "danger"
            ),
            ui.inline_button(
                f"👤 فامیلی: {ln_status}",
                b"ln_toggle",
                "success" if s['lastname_clock'] else "danger"
            ),
        ],
        [
            ui.inline_button(
                f"🎨 فونت بیو: {CLOCK_FONTS[s['bio_font']][0]}",
                b"bio_font_menu",
                "primary"
            ),
            ui.inline_button(
                f"🎨 فونت فامیلی: {CLOCK_FONTS[s['lastname_font']][0]}",
                b"ln_font_menu",
                "primary"
            ),
        ],
        [ui.inline_button("↩️ بازگشت به پنل", b"back_main", "secondary")],
    ]
    return text, buttons


def build_bio_font_menu(uid):
    s = db.get_user_settings(uid)
    current = s['bio_font']
    time_sample = get_tehran_time("%H:%M")
    text = (
        "🎨 **فونت ساعت بیو**\n\n"
        f"🕐 نمونه: `{time_sample}`\n"
        f"✅ فونت فعال: **{CLOCK_FONTS[current][0]}**\n\n"
        "یکی از فونت‌ها را انتخاب کن:"
    )
    buttons = []
    for num, (name, func) in CLOCK_FONTS.items():
        try:
            preview = func(time_sample)
            label = f"{'✓ ' if num == current else ''}{name} • {preview}"
            if len(label) > 60:
                label = f"{'✓ ' if num == current else ''}{name}"
        except Exception:
            label = f"{'✓ ' if num == current else ''}{name}"
        buttons.append([
            ui.inline_button(
                label,
                f"bio_font_{num}",
                "success" if num == current else "primary",
                icon=False
            )
        ])
    buttons.append([ui.inline_button("↩️ بازگشت", b"clk_menu", "secondary")])
    return text, buttons


def build_ln_font_menu(uid):
    s = db.get_user_settings(uid)
    current = s['lastname_font']
    time_sample = get_tehran_time("%H:%M")
    text = (
        "🎨 **فونت ساعت نام خانوادگی**\n\n"
        f"🕐 نمونه: `{time_sample}`\n"
        f"✅ فونت فعال: **{CLOCK_FONTS[current][0]}**\n\n"
        "یکی از فونت‌ها را انتخاب کن:"
    )
    buttons = []
    for num, (name, func) in CLOCK_FONTS.items():
        try:
            preview = func(time_sample)
            label = f"{'✓ ' if num == current else ''}{name} • {preview}"
            if len(label) > 60:
                label = f"{'✓ ' if num == current else ''}{name}"
        except Exception:
            label = f"{'✓ ' if num == current else ''}{name}"
        buttons.append([
            ui.inline_button(
                label,
                f"ln_font_{num}",
                "success" if num == current else "primary",
                icon=False
            )
        ])
    buttons.append([ui.inline_button("↩️ بازگشت", b"clk_menu", "secondary")])
    return text, buttons


def build_preview(uid):
    s = db.get_user_settings(uid)
    time_sample = get_tehran_time("%H:%M:%S")
    _, bio_func = CLOCK_FONTS[s['bio_font']]
    _, ln_func = CLOCK_FONTS[s['lastname_font']]
    try:
        bio_formatted = bio_func(time_sample)
    except Exception:
        bio_formatted = time_sample
    try:
        ln_formatted = ln_func(time_sample)
    except Exception:
        ln_formatted = time_sample
    base_bio = s['base_bio'] or "✨ کاربر تلگرام"
    bio_preview = f"{base_bio} | ⏰ {bio_formatted}" if s['bio_clock'] else base_bio
    if len(bio_preview) > 70:
        bio_preview = bio_preview[:67] + "..."
    ln_preview = ln_formatted if s['lastname_clock'] else (s['base_last_name'] or "—")
    if len(ln_preview) > 64:
        ln_preview = ln_preview[:61] + "..."
    text = (
        "👁 **پیش‌نمایش پروفایل**\n\n"
        f"📝 بیو • `{CLOCK_FONTS[s['bio_font']][0]}`\n"
        f"`{bio_preview}`\n\n"
        f"👤 نام خانوادگی • `{CLOCK_FONTS[s['lastname_font']][0]}`\n"
        f"`{ln_preview}`"
    )
    buttons = [[ui.inline_button("↩️ بازگشت به پنل", b"back_main", "secondary")]]
    return text, buttons


async def _resolve_main_bot_username():
    """Resolve the main bot username once from Telegram Bot API."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getMe"
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                data = await resp.json(content_type=None)
                if data.get('ok'):
                    return (data.get('result') or {}).get('username') or ''
    except Exception as exc:
        print(f"⚠️ دریافت یوزرنیم ربات اصلی: {exc}")
    return ''


async def run_inline():
    """اجرای ربات اینلاین"""
    bot = TelegramClient(
        os.path.join(SESSIONS_DIR, 'inline_bot'),
        API_ID,
        API_HASH
    )
    try:
        await bot.start(bot_token=INLINE_BOT_TOKEN)
        print(f"✅ ربات اینلاین فعال شد: @{INLINE_USERNAME}")
    except Exception as e:
        print(f"❌ خطا ربات اینلاین: {e}")
        return

    main_bot_username = await _resolve_main_bot_username()
    tabchi_models.init_custom_emojis_db()
    if main_bot_username:
        print(f"🔗 لینک تبچی: https://t.me/{main_bot_username}?start=tabchi")

    @bot.on(events.InlineQuery)
    async def inline_handler(event):
        try:
            uid = event.sender_id
            if not has_self_session(uid):
                result = event.builder.article(
                    title="⛔️ بدون دسترسی",
                    description="شما سلف‌بات نصب ندارید",
                    text=(
                        "⛔️ **دسترسی ندارید**\n\n"
                        "کنترل پنل سلف فقط برای اکانتی باز می‌شود که سلف‌بات روی آن نصب باشد.\n\n"
                        "برای نصب، به ربات اصلی برو و «نصب سلف‌بات» را بزن."
                    ),
                    parse_mode='md'
                )
                await event.answer([result], cache_time=0, private=True)
                return
            text, buttons = build_main_menu(uid, main_bot_username)
            result = event.builder.article(
                title="🎛 پنل مدیریت",
                description="هوش مصنوعی، ایموجی پرمیوم، متن به صوت، تبچی، سیو پیام و سایر قابلیت‌ها",
                text=text,
                buttons=buttons,
                parse_mode='md'
            )
            await event.answer([result], cache_time=0, private=True)
        except Exception as e:
            print(f"⚠️ inline query: {e}")

    @bot.on(events.CallbackQuery)
    async def cb_handler(event):
        try:
            d = event.data.decode('utf-8')
            uid = event.sender_id
            log_user_action(uid, 'inline_bot_callback', callback=d[:96])
            if not has_self_session(uid):
                await event.answer(
                    "⛔️ شما سلف‌بات نصب ندارید؛ پنل فقط برای اکانتی است که سلف روی آن فعال است.",
                    alert=True
                )
                return
            disabled = disabled_callback_message(d)
            if disabled:
                await event.answer(disabled, alert=True)
                return
            if d == "back_main":
                tabchi_models.clear_custom_emoji_flow(uid, 'self')
                text, buttons = build_main_menu(uid, main_bot_username)
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "peconv_toggle":
                effective = premium_converter_effective(uid)
                db.update_user_settings(uid, {'premium_emoji_converter': not effective})
                if not bool(getattr(config, 'PREMIUM_EMOJI_ENABLED', True)):
                    await event.answer(
                        '⚠️ کلید اصلی PREMIUM_EMOJI_ENABLED در config خاموش است؛ '
                        'برای فعال‌شدن تبدیل ابتدا آن را روشن کنید.', alert=True)
                else:
                    await event.answer(
                        '🎨 ایموجی ویژه روشن شد؛ ایموجی‌های پیام‌های سلف پرمیوم ارسال می‌شوند.'
                        if not effective else '🎨 ایموجی ویژه خاموش شد.',
                        alert=True)
                text, buttons = build_main_menu(uid, main_bot_username)
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "peresend_toggle":
                effective = premium_resend_effective(uid)
                db.update_user_settings(uid, {'premium_emoji_resend': not effective})
                if not bool(getattr(config, 'PREMIUM_EMOJI_RESEND_MODE', True)):
                    await event.answer(
                        '⚠️ کلید PREMIUM_EMOJI_RESEND_MODE در config خاموش است؛ '
                        'ابتدا آن را در config روشن کنید.', alert=True)
                elif not premium_converter_effective(uid):
                    await event.answer(
                        '⚠️ ابتدا ایموجی ویژه را روشن کنید؛ ارسال دوباره بدون تبدیل معنا ندارد.',
                        alert=True)
                else:
                    await event.answer(
                        '🔁 ارسال دوباره ایموجی ویژه روشن شد؛ پیام‌هایی که Entity خود را ندارند '
                        'حذف و نسخه جدید با Custom Emoji ارسال می‌شود (بدون Edit).'
                        if not effective else '🔁 ارسال دوباره ایموجی ویژه خاموش شد.',
                        alert=True)
                text, buttons = build_main_menu(uid, main_bot_username)
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "away_menu":
                text, buttons = build_away_menu(uid)
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "away_toggle":
                settings = away_service.get_settings(uid)
                away_service.set_enabled(uid, not settings['away_enabled'])
                await event.answer(
                    '💤 پیام عدم حضور روشن شد؛ هر چت خصوصی فقط یک بار پاسخ می‌گیرد و اکانت آفلاین می‌ماند.'
                    if not settings['away_enabled'] else '💤 پیام عدم حضور خاموش شد؛ لیست چت‌ها پاک شد.',
                    alert=True)
                text, buttons = build_away_menu(uid)
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "away_text_change":
                away_service.begin_text_capture(uid)
                await event.edit(
                    "✏️ **متن جدید پیام عدم حضور را در همین چت ارسال کنید.**\n\n"
                    "• متن ساده ارسال شود (بدون فرمت)\n"
                    "• حداکثر ۵۰۰ کاراکتر\n"
                    "• برای لغو، دکمه زیر یا دستور `.بستن`",
                    buttons=[[ui.inline_button("↩️ لغو", b"away_text_cancel", "secondary")]],
                    parse_mode='md',
                )
            elif d == "away_text_cancel":
                away_service.cancel_text_capture(uid)
                await event.answer("تغییر متن لغو شد.", alert=False)
                text, buttons = build_away_menu(uid)
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "away_reset":
                count = away_service.reset_sent_chats(uid)
                await event.answer(
                    f'🧹 لیست چت‌های پاسخ داده شده پاک شد ({count} چت)؛ برای همه دوباره یک بار پیام می‌رود.',
                    alert=True)
                text, buttons = build_away_menu(uid)
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "icrypto_menu":
                text, buttons = build_inline_crypto_menu()
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "icrypto_price":
                await event.edit(
                    "📊 **قیمت ارز**\n\nنمونه‌ها:\n`.ارز بیت کوین`\n`.ارز bitcoin`\n`.ارز BTC`",
                    buttons=[[ui.inline_button("↩️ ارز دیجیتال", b"icrypto_menu", "secondary")]],
                    parse_mode='md',
                )
            elif d == "icrypto_convert":
                await event.edit(
                    "🔄 **تبدیل ارز**\n\nنمونه‌ها:\n`.ارز 10 تتر`\n`.ارز 100 داگز بیت کوین`\n`.ارز 100 تون به تتر`",
                    buttons=[[ui.inline_button("↩️ ارز دیجیتال", b"icrypto_menu", "secondary")]],
                    parse_mode='md',
                )
            elif d == "icrypto_popular":
                text, buttons = await build_inline_crypto_popular(uid)
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "icrypto_settings":
                text, buttons = build_inline_crypto_settings(uid)
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "cem_menu":
                tabchi_models.clear_custom_emoji_flow(uid, 'self')
                text, buttons = build_self_emoji_menu()
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "cem_extract":
                tabchi_models.set_custom_emoji_flow(uid, 'self', 'extract')
                await event.edit(
                    "✨ **پیام دارای ایموجی ویژه را ارسال کنید.**\n\n"
                    "متن، عکس+کپشن، ویدیو+کپشن و فایل+کپشن پشتیبانی می‌شود.",
                    buttons=[[ui.inline_button("↩️ لغو", b"cem_menu", "secondary")]],
                    parse_mode='md',
                )
            elif d == "cem_test":
                tabchi_models.set_custom_emoji_flow(uid, 'self', 'test')
                await event.edit(
                    "🧪 **Document ID را ارسال کنید.**\n\n"
                    "تست با `MessageEntityCustomEmoji` واقعی انجام می‌شود؛ عدد به‌تنهایی به عنوان خروجی ارسال نمی‌شود.",
                    buttons=[[ui.inline_button("↩️ لغو", b"cem_menu", "secondary")]],
                    parse_mode='md',
                )
            elif d.startswith("cem_list_"):
                tabchi_models.clear_custom_emoji_flow(uid, 'self')
                try:
                    page = int(d.rsplit('_', 1)[1])
                except Exception:
                    page = 0
                text, buttons = build_self_emoji_list(uid, page)
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d.startswith("cem_del_"):
                tabchi_models.clear_custom_emoji_flow(uid, 'self')
                try:
                    _, _, entry_id, page = d.split('_', 3)
                    deleted = tabchi_models.delete_custom_emoji(int(entry_id), int(uid))
                except Exception:
                    deleted, page = False, 0
                await event.answer("حذف شد." if deleted else "ایموجی پیدا نشد.", alert=False)
                text, buttons = build_self_emoji_list(uid, int(page))
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "other_features":
                text, buttons = build_other_capabilities_menu(uid)
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "save_menu":
                text, buttons = render_save_menu(uid)
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "save_enable":
                settings = tabchi_models.get_save_settings(uid)
                if not settings.get('destination'):
                    await event.answer("اول محل ذخیره را تعیین کن.", alert=True)
                    text, buttons = render_save_menu(uid)
                    await event.edit(text, buttons=buttons, parse_mode='md')
                else:
                    tabchi_models.set_save_status(uid, True)
                    await event.answer("سیو پیام فعال شد.", alert=True)
                    text, buttons = render_save_menu(uid)
                    await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "save_disable":
                tabchi_models.set_save_status(uid, False)
                cancel_destination_capture(uid)
                await asyncio.to_thread(purge_owner_cache_files, uid)
                await event.answer("سیو پیام خاموش شد و کش فعال پاک شد.", alert=True)
                text, buttons = render_save_menu(uid)
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "save_destination":
                begin_destination_capture(uid)
                text, buttons = render_destination_prompt()
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "save_destination_cancel":
                cancel_destination_capture(uid)
                await event.answer("تعیین مقصد لغو شد.", alert=False)
                text, buttons = render_save_menu(uid)
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "save_stats":
                text, buttons = render_save_stats(uid)
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "msgfont_menu":
                text, buttons = render_font_menu(uid)
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "msgfont_select":
                text, buttons = render_font_selector(uid)
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "msgfont_preview":
                text, buttons, entities = render_font_preview(uid)
                await event.edit(
                    text, buttons=buttons, parse_mode=None,
                    formatting_entities=entities
                )
            elif d == "msgfont_enable":
                s = db.get_user_settings(uid)
                style = normalize_font_style(s.get("message_font_style"))
                db.update_user_settings(uid, {
                    "message_font_enabled": True,
                    "message_font_style": style,
                })
                await event.answer(f"فونت فعال شد: {FONT_STYLES[style]}", alert=True)
                text, buttons = render_font_menu(uid)
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "msgfont_disable":
                db.update_user_settings(uid, {"message_font_enabled": False})
                await event.answer("فونت پیام خاموش شد.", alert=True)
                text, buttons = render_font_menu(uid)
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d.startswith("msgfont_set_"):
                style = normalize_font_style(d[len("msgfont_set_"):])
                db.update_user_settings(uid, {"message_font_style": style})
                await event.answer(f"فونت انتخاب شد: {FONT_STYLES[style]}", alert=True)
                text, buttons = render_font_selector(uid)
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "panel_stats":
                text, buttons = build_panel_stats(uid)
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "no_tabchi_link":
                await event.answer("ربات اصلی هنوز شناسایی نشده؛ چند ثانیه بعد دوباره .پنل را بزن.", alert=True)
            elif d == "feat_copy":
                text, buttons = build_feature_detail(uid, 'copy')
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "feat_delete":
                text, buttons = build_feature_detail(uid, 'delete')
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "feat_spam":
                text, buttons = build_feature_detail(uid, 'spam')
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "feat_cancel":
                text, buttons = build_feature_detail(uid, 'cancel')
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "feat_block":
                text, buttons = build_feature_detail(uid, 'block')
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "feat_mute":
                text, buttons = build_feature_detail(uid, 'mute')
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "feat_enemy":
                text, buttons = build_feature_detail(uid, 'enemy')
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "feat_stt":
                text, buttons = build_feature_detail(uid, 'stt')
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "feat_tts":
                text, buttons = build_feature_detail(uid, 'tts')
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "feat_translate":
                text, buttons = build_feature_detail(uid, 'translate')
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "feat_ai":
                text, buttons = build_feature_detail(uid, 'ai')
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "feat_fonts":
                text, buttons = build_feature_detail(uid, 'fonts')
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "feat_info":
                text, buttons = build_feature_detail(uid, 'info')
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "feat_ping":
                text, buttons = build_feature_detail(uid, 'ping')
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "clk_menu":
                text, buttons = build_clock_menu(uid)
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "bio_toggle":
                s = db.get_user_settings(uid)
                new_value = not s['bio_clock']
                db.update_user_settings(uid, {'bio_clock': new_value})
                status = "فعال شد" if new_value else "غیرفعال شد"
                await event.answer(f"ساعت بیو {status}", alert=True)
                text, buttons = build_clock_menu(uid)
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "ln_toggle":
                s = db.get_user_settings(uid)
                new_value = not s['lastname_clock']
                db.update_user_settings(uid, {'lastname_clock': new_value})
                status = "فعال شد" if new_value else "غیرفعال شد"
                await event.answer(f"ساعت نام خانوادگی {status}", alert=True)
                text, buttons = build_clock_menu(uid)
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "bio_font_menu":
                text, buttons = build_bio_font_menu(uid)
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "ln_font_menu":
                text, buttons = build_ln_font_menu(uid)
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d.startswith("bio_font_"):
                try:
                    num = int(d.split("_")[2])
                    if num in CLOCK_FONTS:
                        db.update_user_settings(uid, {'bio_font': num})
                        await event.answer(f"فونت بیو: {CLOCK_FONTS[num][0]}", alert=True)
                        text, buttons = build_bio_font_menu(uid)
                        await event.edit(text, buttons=buttons, parse_mode='md')
                except Exception:
                    pass
            elif d.startswith("ln_font_"):
                try:
                    num = int(d.split("_")[2])
                    if num in CLOCK_FONTS:
                        db.update_user_settings(uid, {'lastname_font': num})
                        await event.answer(f"فونت نام خانوادگی: {CLOCK_FONTS[num][0]}", alert=True)
                        text, buttons = build_ln_font_menu(uid)
                        await event.edit(text, buttons=buttons, parse_mode='md')
                except Exception:
                    pass
            elif d == "preview":
                text, buttons = build_preview(uid)
                await event.edit(text, buttons=buttons, parse_mode='md')
            elif d == "close":
                await event.edit("✅ پنل بسته شد.")
        except Exception as e:
            print(f"⚠️ callback: {e}")
            try:
                await event.answer(f"❌ خطا: {e}", alert=True)
            except Exception:
                pass

    await bot.run_until_disconnected()