"""ربات اصلی: کیف پول، صورتحساب، رفرال، انتقال، جوین اجباری و پنل مدیریت"""
import os
import time
import asyncio
import secrets

from telethon import TelegramClient, events, errors
from telethon.sessions import MemorySession
from telethon.tl.types import ReplyKeyboardHide

from config import (
    API_ID, API_HASH, BOT_TOKEN, SESSIONS_DIR,
    ADMIN_ID, PRICE_PER_DIAMOND, MIN_DIAMOND, DIAMOND_STEP, TRIAL_DURATION_HOURS, TRIAL_DURATION_HOURS, MAX_DIAMOND,
    BILL_DIAMONDS_PER_HOUR, SELF_START_COST, REF_REWARD, MIN_TRANSFER,
    TRANSFER_FEE_PERCENT,
    get_tehran_time
)

from login_manager import (
    start_login,
    send_code_request,
    handle_code_button,
    complete_login,
    login_states,
    cleanup_state,
    active_state
)

import db
import ui
from services.feature_flags import filter_buttons, disabled_callback_message
from services.broadcast_queue import BroadcastJob, broadcast_queue
import self_manager
from database import models as tabchi_models
from bot.handlers.tabchi import TabchiController
from handlers.trial import TrialController
from handlers.support import SupportController
from handlers.admin import AdminController
from handlers.admin_game import AdminGameController
from handlers.balance import BalanceController
from handlers.game import GameController
from handlers.transfer import TransferController
from handlers.premium_emoji import PremiumEmojiController
from handlers.crypto import CryptoController
from handlers.ai import AIController, execute_ai_text, is_ai_command
from handlers.translate import is_translate_command
from services.ai_assistant import split_telegram_text
from services import admin_manager, trial_manager, transfer_service, referral_service, payment_service, usage_service, identity_service
from services.access_service import can_run
from services.membership_service import check_required_memberships
from services.logging_service import log_user_action

BOT_START_TIME = time.time()

flow_states = {}


def fmt(n):
    return f"{int(n):,}"


def format_duration(hours):
    hours = int(hours)
    d, h = divmod(hours, 24)
    if d:
        return f"{d} روز و {h} ساعت"
    return f"{h} ساعت"


def is_bot_off(uid):
    if admin_manager.is_admin(uid):
        return False
    return not db.get_global()['bot_enabled']


def has_self_session(uid):
    """فقط اکانتی که سلف روی آن نصب است دسترسی دارد"""
    return os.path.exists(os.path.join(SESSIONS_DIR, f"user_{uid}.txt"))


# ========================================
# رندر منوها
# ========================================
def render_main_menu(uid):
    s = db.get_user_settings(uid)
    diamonds = int(s['diamonds'])
    text = (
        "✨ **پنل اصلی**\n\n"
        f"💎 موجودی: **{fmt(diamonds)} الماس**\n"
        f"💵 ارزش موجودی: **{fmt(diamonds * PRICE_PER_DIAMOND)} تومان**\n\n"
        "━━━━━━━━━━━━━━\n"
        "از بخش‌های زیر انتخاب کن:"
    )

    if has_self_session(uid):
        buttons = [[
            ui.inline_button("🛠 مدیریت سلف", b"self_menu", "primary"),
            ui.inline_button("🔗 اتصال / نصب", b"install_self", "primary"),
        ]]
    else:
        buttons = [[ui.inline_button("🚀 نصب و اتصال سلف‌بات", b"install_self", "primary")]]

    buttons += [
        [
            ui.inline_button("💎 کیف پول", b"wallet_menu", "success"),
            ui.inline_button("🔁 انتقال الماس", b"transfer_menu", "primary"),
        ],
        [
            ui.inline_button("👤 حساب من", b"account_menu", "primary"),
            ui.inline_button("🎁 دعوت دوستان", b"ref_menu", "success"),
        ],
        [ui.inline_button("🤖 تبچی", b"tb_menu", "primary")],
        [
            ui.inline_button(f"🎁 {TRIAL_DURATION_HOURS} تست رایگان", b"trial_menu", "success"),
            ui.inline_button("🎧 پشتیبانی", b"support_menu", "primary"),
        ],
        [ui.inline_button("✨ ایموجی پرمیوم", b"em_menu", "primary")],
        [ui.inline_button("💰 ارز دیجیتال", b"crypto_menu", "success")],
        [
            ui.inline_button("🤖 هوش مصنوعی", b"ai_menu", "success"),
        ],
        [ui.inline_button("🆔 تبدیل یوزرنیم به آیدی", b"username_lookup", "primary")],
        [ui.inline_button("⚙️ تنظیمات", b"settings_menu", "secondary")],
    ]
    if admin_manager.is_admin(uid, s.get('username') or ''):
        buttons.append([ui.inline_button("👑 مدیریت", b"adm2_menu", "danger")])
    return text, filter_buttons(buttons)


def render_wallet_menu(uid):
    s = db.get_user_settings(uid)
    text = (
        "💎 **کیف پول**\n\n"
        f"💰 موجودی فعلی: **{fmt(s['diamonds'])} الماس**\n"
        f"🛍 خرید کل: **{fmt(s['total_diamonds_bought'])} الماس**\n"
        f"💳 پرداخت کل: **{fmt(s['total_spent_toman'])} تومان**\n\n"
        "━━━━━━━━━━━━━━\n"
        f"🏷 هر الماس: **{fmt(PRICE_PER_DIAMOND)} تومان**\n"
        "⏱ مصرف سلف: **۱ الماس هر ۳۰ دقیقه**\n"
        f"⚡ هزینه هر بار روشن‌کردن: **{SELF_START_COST} الماس**\n"
        f"📦 حداقل خرید: **{fmt(MIN_DIAMOND)} الماس**"
    )
    buttons = [
        [ui.inline_button("💎 خرید الماس", b"buy_menu", "success")],
        [ui.inline_button("↩️ بازگشت به پنل", b"main_menu", "secondary")],
    ]
    return text, buttons


def render_amount_menu(uid):
    st = flow_states.get(uid, {})
    amount = int(st.get('amount', MIN_DIAMOND))
    total = amount * PRICE_PER_DIAMOND

    text = (
        "🛒 **انتخاب بسته الماس**\n\n"
        f"💎 تعداد: **{fmt(amount)} الماس**\n"
        f"💵 مبلغ نهایی: **{fmt(total)} تومان**\n"
        f"🏷 قیمت واحد: **{fmt(PRICE_PER_DIAMOND)} تومان**\n\n"
        "با + و − مقدار را تغییر بده یا تعداد دلخواه را وارد کن."
    )

    buttons = [
        [
            ui.inline_button("➖ کمتر", b"buy_minus", "danger"),
            ui.inline_button(fmt(amount), b"buy_show", "primary", icon=False),
            ui.inline_button("➕ بیشتر", b"buy_plus", "success"),
        ],
        [ui.inline_button("⌨️ وارد کردن تعداد دلخواه", b"buy_manual", "primary")],
        [ui.inline_button("✅ ادامه و پرداخت", b"buy_confirm", "success")],
        [ui.inline_button("↩️ بازگشت", b"wallet_menu", "secondary")],
    ]
    return text, buttons


def render_payment_menu(uid):
    st = flow_states.get(uid, {})
    amount = int(st.get('amount', MIN_DIAMOND))
    total = amount * PRICE_PER_DIAMOND

    g = db.get_global()
    card = g['card_number'] or 'تنظیم نشده'
    holder = g['card_holder'] or 'تنظیم نشده'

    text = (
        "💳 **پرداخت کارت‌به‌کارت**\n\n"
        f"💎 سفارش: **{fmt(amount)} الماس**\n"
        f"💵 مبلغ: **{fmt(total)} تومان**\n\n"
        "━━━━━━━━━━━━━━\n"
        f"💳 شماره کارت:\n`{card}`\n"
        f"👤 صاحب حساب: **{holder}**\n"
        "━━━━━━━━━━━━━━\n\n"
        "پس از واریز، «واریز کردم» را بزن و تصویر رسید را ارسال کن."
    )

    buttons = [
        [ui.inline_button("✅ واریز کردم", b"buy_paid", "success")],
        [ui.inline_button("↩️ بازگشت", b"buy_menu", "secondary")],
    ]
    return text, buttons


def render_account_menu(uid):
    s = db.get_user_settings(uid)
    username = f"@{s['username']}" if s['username'] else "ندارد"
    text = (
        "👤 **حساب کاربری**\n\n"
        f"📛 نام: **{s['first_name'] or '-'}**\n"
        f"🏷 یوزرنیم: `{username}`\n"
        f"🆔 شناسه: `{uid}`\n\n"
        "━━━━━━━━━━━━━━\n"
        f"💎 موجودی: **{fmt(s['diamonds'])} الماس**\n"
        f"💵 ارزش موجودی: **{fmt(s['diamonds'] * PRICE_PER_DIAMOND)} تومان**\n"
        f"👥 دعوت‌های تأییدشده: **{fmt(s['ref_count'])} نفر**\n"
        f"🎁 درآمد دعوت: **{fmt(s['ref_earned'])} الماس**\n"
        f"📅 تاریخ عضویت: `{s['registered_at'] or '-'}`"
    )
    buttons = [[ui.inline_button("↩️ بازگشت به پنل", b"main_menu", "secondary")]]
    return text, buttons


def render_ref_menu(uid, bot_username):
    s = db.get_user_settings(uid)
    link = f"https://t.me/{bot_username}?start=ref_{uid}"

    text = (
        "🎁 **دعوت دوستان**\n\n"
        f"هر دعوت موفق = **{REF_REWARD} الماس هدیه**\n\n"
        "🔗 لینک اختصاصی تو:\n"
        f"`{link}`\n\n"
        "━━━━━━━━━━━━━━\n"
        f"👥 دعوت‌های تأییدشده: **{fmt(s['ref_count'])} نفر**\n"
        f"💎 درآمد از دعوت: **{fmt(s['ref_earned'])} الماس**\n\n"
        "دعوت زمانی ثبت می‌شود که کاربر وارد ربات شود و عضویت‌های اجباری را تکمیل کند."
    )
    buttons = [[ui.inline_button("↩️ بازگشت به پنل", b"main_menu", "secondary")]]
    return text, buttons


def render_settings_menu(uid):
    s = db.get_user_settings(uid)
    alerts = "🟢 فعال" if s['alerts_enabled'] else "🔴 غیرفعال"

    text = (
        "⚙️ **تنظیمات**\n\n"
        f"🔔 هشدار موجودی: **{alerts}**\n\n"
        "در صورت فعال بودن، نزدیک پایان موجودی در بازه‌های مهم برایت هشدار ارسال می‌شود."
    )

    if s['alerts_enabled']:
        toggle = ui.inline_button("🔕 خاموش کردن هشدار", b"set_alerts", "danger")
    else:
        toggle = ui.inline_button("🔔 روشن کردن هشدار", b"set_alerts", "success")

    buttons = [
        [toggle],
        [ui.inline_button("↩️ بازگشت به پنل", b"main_menu", "secondary")],
    ]
    return text, buttons


def render_self_menu(uid):
    s = db.get_user_settings(uid)

    session_file = os.path.join(SESSIONS_DIR, f"user_{uid}.txt")
    installed = os.path.exists(session_file)
    client = self_manager.get_client_for_user(uid)
    running = client is not None

    self_status = "🟢 روشن" if can_run(uid) else "🔴 خاموش / بدون اعتبار"
    install_status = "✅ متصل" if installed else "❌ متصل نیست"
    run_status = "🟢 اتصال برقرار" if running else "⚪️ متوقف / در حال اتصال"
    trial_on = trial_manager.is_active(uid)
    trial_status = "🎁 فعال (بدون کسر الماس)" if trial_on else "⚪️ غیرفعال"

    text = (
        "🛠 **مدیریت سلف‌بات**\n\n"
        f"⚙️ سرویس: **{self_status}**\n"
        f"🔐 سشن: **{install_status}**\n"
        f"🚀 اجرا: **{run_status}**\n"
        f"🎁 تست رایگان: **{trial_status}**\n\n"
        "━━━━━━━━━━━━━━\n"
        f"⚡ هزینه روشن‌کردن: **{SELF_START_COST} الماس**\n"
        f"⏱ مصرف سرویس: **{BILL_DIAMONDS_PER_HOUR} الماس**\n\n"
        "حذف سلف باعث خروج سشن و نیاز به اتصال دوباره می‌شود."
    )

    if s['self_enabled']:
        toggle_btn = ui.inline_button("⏹ خاموش کردن سلف", b"self_toggle", "danger")
    else:
        toggle_btn = ui.inline_button(
            f"▶️ روشن کردن سلف • {SELF_START_COST} الماس",
            b"self_toggle",
            "success"
        )

    buttons = [
        [toggle_btn],
        [ui.inline_button("🗑 حذف سلف و خروج سشن", b"self_delete", "danger")],
        [ui.inline_button("↩️ بازگشت به پنل", b"main_menu", "secondary")],
    ]
    return text, buttons


def render_admin_menu():
    g = db.get_global()
    enabled = bool(g['bot_enabled'])
    status = "🟢 روشن" if enabled else "🔴 خاموش"
    card = "✅ تنظیم شده" if g['card_number'] else "⚪️ تنظیم نشده"
    force = ", ".join(g['force_channels']) if g['force_channels'] else "غیرفعال"

    text = (
        "🛡 **پنل مدیریت**\n\n"
        f"🤖 وضعیت ربات: **{status}**\n"
        f"💳 اطلاعات پرداخت: **{card}**\n"
        f"🔒 جوین اجباری: `{force}`\n\n"
        "بخش موردنظر را انتخاب کن:"
    )

    toggle_text = "⏹ خاموش کردن ربات" if enabled else "▶️ روشن کردن ربات"
    toggle_style = "danger" if enabled else "success"

    buttons = [
        [
            ui.inline_button("💳 پرداخت", b"adm_pay_info", "primary"),
            ui.inline_button("🔒 جوین اجباری", b"adm_force", "primary"),
        ],
        [
            ui.inline_button("💎 موجودی کاربران", b"adm_bal", "primary"),
            ui.inline_button("📣 پیام همگانی", b"adm_bc", "success"),
        ],
        [ui.inline_button("👥 بخش کاربران", b"adm2_users", "primary")],
        [
            ui.inline_button("🧾 درخواست‌ها", b"adm_pending", "primary"),
            ui.inline_button("📊 آمار ربات", b"adm_stats", "primary"),
        ],
        [
            ui.inline_button("🎮 مدیریت بازی", b"ag_menu", "success"),
            ui.inline_button("📍 گروه شرط‌بندی", b"ag_group", "primary"),
        ],
        [ui.inline_button("✨ ایموجی‌های ذخیره‌شده", b"adm_emoji_0", "primary")],
        [ui.inline_button(toggle_text, b"adm_toggle", toggle_style)],
    ]
    return text, buttons


def render_admin_custom_emojis(page=0):
    rows, total, page = tabchi_models.admin_custom_emoji_page(page)
    pages = max(1, (total + 19) // 20)
    text = f"✨ **Custom Emoji Manager — مدیریت**\n\nتعداد کل: **{total}**\n\n"
    buttons = []
    if rows:
        text += '\n'.join(
            f"▫️ مالک: `{row['owner_id']}` | ID: `{row['document_id']}`"
            for row in rows
        )
        text += f"\n\nصفحهٔ {page + 1} از {pages}"
        for row in rows:
            buttons.append([
                ui.inline_button(
                    f"🗑 {row['document_id']} / {row['owner_id']}",
                    f"adm_emdel_{row['id']}_{page}",
                    "danger",
                    icon=False,
                )
            ])
    else:
        text += "هنوز ایموجی ذخیره‌شده‌ای وجود ندارد."
    nav = []
    if page > 0:
        nav.append(ui.inline_button("⬅️ قبلی", f"adm_emoji_{page - 1}", "secondary"))
    if (page + 1) * 20 < total:
        nav.append(ui.inline_button("بعدی ➡️", f"adm_emoji_{page + 1}", "secondary"))
    if nav:
        buttons.append(nav)
    buttons.append([ui.inline_button("↩️ مدیریت", b"adm_menu", "secondary")])
    return text, buttons


def render_admin_pay_info():
    g = db.get_global()
    text = (
        "💳 **تنظیمات پرداخت**\n\n"
        f"💳 شماره کارت:\n`{g['card_number'] or 'تنظیم نشده'}`\n\n"
        f"👤 نام دریافت‌کننده: **{g['card_holder'] or 'تنظیم نشده'}**"
    )
    buttons = [
        [
            ui.inline_button("💳 تغییر کارت", b"adm_card", "primary"),
            ui.inline_button("👤 تغییر نام", b"adm_holder", "primary"),
        ],
        [ui.inline_button("↩️ بازگشت", b"adm_menu", "secondary")],
    ]
    return text, buttons


def render_admin_pending():
    items = db.list_payments('pending', 5)

    if not items:
        text = "📭 **درخواستی برای بررسی وجود ندارد.**"
        buttons = [[ui.inline_button("↩️ بازگشت", b"adm_menu", "secondary")]]
        return text, buttons

    lines = ["🧾 **درخواست‌های پرداخت**\n"]
    buttons = []

    for p in items:
        lines.append(
            f"`PAY-{p['id']}` • **{fmt(p['diamonds'])} الماس** • "
            f"{fmt(p['amount'])} تومان • `{p['uid']}`"
        )
        buttons.append([
            ui.inline_button(f"✅ PAY-{p['id']}", f"pay_ok_{p['id']}", "success"),
            ui.inline_button(f"❌ PAY-{p['id']}", f"pay_no_{p['id']}", "danger"),
        ])

    buttons.append([ui.inline_button("↩️ بازگشت", b"adm_menu", "secondary")])
    return "\n".join(lines), buttons


def render_admin_stats():
    g = db.get_global()
    st = dict(g['stats'])
    from services.transaction_service import connect
    with connect() as conn:
        new_burned = conn.execute("SELECT COALESCE(-SUM(amount),0) FROM diamond_transactions WHERE description LIKE 'HALF_HOUR_USAGE:%'").fetchone()[0]
    st['billing_burned'] = int(st.get('billing_burned', 0)) + int(new_burned)

    users = db.iter_user_ids()
    wallet_users = 0
    total_diamonds_hold = 0

    for u in users:
        s = db.get_user_settings(u)
        if s['diamonds'] > 0 or s['total_diamonds_bought'] > 0:
            wallet_users += 1
        total_diamonds_hold += s['diamonds']

    pending = db.list_payments('pending')

    uptime = int(time.time() - BOT_START_TIME)
    hours, rem = divmod(uptime, 3600)
    minutes, seconds = divmod(rem, 60)

    try:
        sessions = len(self_manager.running_sessions)
    except Exception:
        sessions = 0

    text = (
        "📊 **آمار ربات**\n\n"
        f"👥 کاربران: **{fmt(len(users))}**\n"
        f"👛 کیف پول فعال: **{fmt(wallet_users)}**\n"
        f"💎 الماس در گردش: **{fmt(total_diamonds_hold)}**\n"
        f"🤖 سشن فعال: **{fmt(sessions)}**\n\n"
        "━━━━━━━━━━━━━━\n"
        f"🛒 الماس فروخته‌شده: **{fmt(st['diamonds_sold'])}**\n"
        f"💰 درآمد تأییدشده: **{fmt(st['revenue_toman'])} تومان**\n"
        f"⚡ هزینه‌های روشن‌کردن: **{fmt(st.get('start_fees', 0))} الماس**\n"
        f"🧾 کارمزد انتقال: **{fmt(st.get('fees_collected', 0))} الماس**\n"
        f"🎁 پاداش دعوت: **{fmt(st.get('ref_rewards', 0))} الماس**\n"
        f"⏱ مصرف سرویس: **{fmt(st.get('billing_burned', 0))} الماس**\n\n"
        f"✅ پرداخت تأیید: **{fmt(st['approved'])}**\n"
        f"❌ پرداخت ردشده: **{fmt(st['rejected'])}**\n"
        f"⏳ در انتظار: **{fmt(len(pending))}**\n"
        f"🕒 آپ‌تایم: **{hours}س {minutes}د {seconds}ث**"
    )

    buttons = [
        [ui.inline_button("🔄 بروزرسانی آمار", b"adm_stats", "primary")],
        [ui.inline_button("↩️ بازگشت", b"adm_menu", "secondary")],
    ]
    return text, buttons

async def run_bot():
    """اجرای ربات اصلی"""
    bot = TelegramClient(
        MemorySession(),
        API_ID,
        API_HASH
    )

    try:
        await bot.start(bot_token=BOT_TOKEN)
        print("✅ ربات اصلی فعال شد")
    except Exception as e:
        print(f"❌ خطا ربات اصلی: {e}")
        return

    me_bot = await bot.get_me()
    expected_bot_id = int(BOT_TOKEN.split(':', 1)[0])
    if int(me_bot.id) != expected_bot_id:
        await bot.disconnect()
        raise RuntimeError(
            f'Main bot identity mismatch: connected={me_bot.id}, expected={expected_bot_id}'
        )
    bot_username = me_bot.username or ''
    tabchi_models.init_tabchi_db()
    tabchi_models.init_membership_support_db()
    tabchi_models.init_game_db()
    tabchi_models.init_custom_emojis_db()
    tabchi_models.init_crypto_db()
    tabchi_models.init_ai_db()
    premium_emoji = PremiumEmojiController(bot)
    crypto = CryptoController(bot)
    ai = AIController(bot, admin_check=lambda user_id: admin_manager.is_admin(user_id))
    tabchi = TabchiController(bot, bot_username, has_self_session)
    trial = TrialController(bot)
    support = SupportController(bot)

    # ========================================
    # کمک‌تابع‌ها
    # ========================================
    async def resolve_user(token):
        token = (token or '').strip()
        if token.startswith('@'):
            token = token[1:]
        if not token:
            return None

        if token.lstrip('-').isdigit():
            return int(token)

        low = token.lower()
        for u in db.iter_user_ids():
            s = db.get_user_settings(u)
            if (s.get('username') or '').lower() == low:
                return u

        try:
            ent = await bot.get_entity(token)
            return getattr(ent, 'id', None)
        except Exception:
            return None

    admin = AdminController(bot, resolve_user=resolve_user)
    admin_game = AdminGameController(bot, admin_manager.has_permission)
    game = GameController(bot)
    balance = BalanceController(bot)
    group_transfer = TransferController(bot)
    game_scheduler_task = asyncio.create_task(game.run_expiration_scheduler())

    async def edit_safe(event, text, buttons=None):
        try:
            await event.edit(text, buttons=buttons, parse_mode='md')
        except Exception:
            pass

    async def check_force_join(uid):
        g = db.get_global()
        chans = g.get('force_channels') or []

        if not chans or uid == ADMIN_ID:
            return True, []
        return await check_required_memberships(bot, int(uid), list(chans))

    async def force_join_view(uid, missing):
        lines = "\n".join(f"• **{item.title}**" for item in missing)
        has_check_error = any(item.reason == 'check_failed' for item in missing)
        text = (
            "🔒 **تکمیل عضویت**\n\n"
            "برای ورود به پنل، ابتدا در کانال‌های زیر عضو شو:\n\n"
            f"{lines}\n\n"
            "بعد از عضویت، دکمه بررسی را بزن."
        )
        if has_check_error:
            text += (
                "\n\n⚠️ اگر عضو هستی ولی تأیید نمی‌شود، ربات باید در کانال "
                "دسترسی بررسی اعضا/ادمین داشته باشد."
            )
        buttons = []
        for item in missing:
            if item.url:
                buttons.append([ui.url_button(f"📢 عضویت در {item.title}", item.url, "primary")])
        buttons.append([ui.inline_button("✅ بررسی عضویت", b"force_check", "success")])
        return text, buttons

    async def process_referral(uid):
        s = db.get_user_settings(uid)
        ref = s.get('referred_by', 0)

        if not ref or ref == uid or s.get('ref_credited'):
            return

        g = db.get_global()
        if g.get('force_channels'):
            joined, _ = await check_force_join(uid)
            if not joined:
                return

        result = referral_service.claim(uid)
        if result is None:
            return
        r = db.get_user_settings(ref)
        # The reward and unique claim are already committed together in SQLite.
        try:
            db.update_user_settings(ref, {
                'ref_count': r['ref_count'] + 1,
                'ref_earned': r['ref_earned'] + REF_REWARD,
                'last_alert_hours': 9999,
            })
            db.update_user_settings(uid, {'ref_credited': True})
            db.add_stats({'ref_rewards': REF_REWARD})
        except Exception:
            pass

        try:
            await bot.send_message(
                ref,
                f"👥 یک زیرمجموعه جدید به مجموعه شما اضافه شد.\n\n"
                f"تعداد کل زیرمجموعه‌ها:\n{fmt(r['ref_count'] + 1)}",
                parse_mode='md'
            )
        except Exception:
            pass

        try:
            await bot.send_message(
                uid,
                f"✅ دعوت شما تایید شد.\n🎁 **{REF_REWARD} الماس** به معرف شما اهدا شد.",
                parse_mode='md'
            )
        except Exception:
            pass

    async def maybe_send_alerts(uid):
        s = db.get_user_settings(uid)

        if not s.get('alerts_enabled', True):
            return

        d = s['diamonds']
        hours = d // BILL_DIAMONDS_PER_HOUR
        last = s.get('last_alert_hours', 9999)
        patch = {}

        if hours > last:
            patch['last_alert_hours'] = hours
            last = hours

        levels = {72, 48, 24} | set(range(1, 25))

        if d <= 0:
            if last != 0:
                patch['last_alert_hours'] = 0
                try:
                    await bot.send_message(
                        uid,
                        "🛑 **موجودی شما تمام شد!**\n\n"
                        "💎 موجودی فعلی: **0 الماس**\n"
                        "برای ادامه استفاده از سرویس سلف، کیف پول را شارژ کن.",
                        parse_mode='md'
                    )
                except Exception:
                    pass
        elif hours < last and hours in levels:
            patch['last_alert_hours'] = hours
            try:
                await bot.send_message(
                    uid,
                    "⚠️ **هشدار موجودی سلف**\n\n"
                    f"💎 موجودی فعلی: **{fmt(d)} الماس**\n"
                    f"⏳ زمان باقی‌مانده: حدود **{format_duration(hours)}**\n"
                    f"📉 هزینه: **۱ الماس هر نیم ساعت**\n\n"
                    "💡 شارژ: پنل اصلی → کیف پول\n"
                    "⚙️ مدیریت هشدارها: پنل اصلی → تنظیمات",
                    parse_mode='md'
                )
            except Exception:
                pass

        if patch:
            db.update_user_settings(uid, patch)

    async def billing_loop():
        # Notifications only. Durable billing runs independently in main.py,
        # including when no Telegram client is connected.
        while True:
            await asyncio.sleep(60)
            for expired_uid in trial_manager.expire_due_trials():
                try:
                    await bot.send_message(expired_uid,
                        '⏰ تست رایگان شما پایان یافت. ادامهٔ سلف از موجودی الماس محاسبه می‌شود.',
                        parse_mode=None)
                except Exception:
                    pass
            from services.transaction_service import connect
            with connect() as conn:
                ids = [row[0] for row in conn.execute('SELECT user_id FROM billing_clocks WHERE enabled=1')]
            for uid in ids:
                try:
                    await maybe_send_alerts(uid)
                except Exception as exc:
                    print(f'⚠️ billing notification failed for {uid}: {type(exc).__name__}')

    billing_task = asyncio.create_task(billing_loop())

    # ========================================
    # /start
    # ========================================
    @bot.on(events.NewMessage(pattern='/start'))
    async def start(event):
        if not event.is_private:
            return
        uid = event.sender_id

        if is_bot_off(uid):
            return

        current_login = login_states.get(uid)
        if current_login and current_login.get('step') == 'FINALIZING':
            await event.reply('اتصال در حال نهایی‌شدن است؛ کمی صبر کنید.')
            return
        raw = event.raw_text or ''
        parts = raw.split()

        if len(parts) > 1 and parts[1].startswith('ref_'):
            try:
                code = int(parts[1][4:])
            except Exception:
                code = 0

            cur = db.get_user_settings(uid)
            if (0 < code <= transfer_service.MAX_USER_ID and code != uid
                    and not cur.get('registered_at') and not cur.get('referred_by')
                    and transfer_service.registered_user(code)):
                db.update_user_settings(uid, {'referred_by': code, 'ref_credited': False})

        sender = event.sender
        db.touch_user(
            uid,
            username=getattr(sender, 'username', '') or '',
            first_name=getattr(sender, 'first_name', '') or ''
        )
        tabchi_models.touch_profile(
            uid,
            username=getattr(sender, 'username', '') or '',
            first_name=getattr(sender, 'first_name', '') or '',
            last_name=getattr(sender, 'last_name', '') or '',
            registered_at=db.get_user_settings(uid).get('registered_at') or None,
        )
        admin_manager.note_identity(uid, getattr(sender, 'username', '') or '')

        is_tabchi_deeplink = len(parts) > 1 and parts[1] == 'tabchi'

        cleanup_state(uid, disconnect=True)
        premium_emoji.cancel(uid)
        crypto.cancel(uid)
        ai.cancel(uid)
        flow_states.pop(uid, None)
        support.states.pop(uid, None)
        tabchi.states.pop(uid, None)
        admin.states.pop(uid, None)
        admin_game.states.pop(uid, None)

        joined, missing = await check_force_join(uid)
        if not joined:
            text, buttons = await force_join_view(uid, missing)
            await bot.send_message(event.chat_id, text, buttons=buttons, parse_mode='md')
            return

        await process_referral(uid)

        if is_tabchi_deeplink:
            # .پنل -> 🤖 تبچی opens management directly; no extra create step.
            await tabchi.send_menu(event.chat_id, uid)
            return

        text, buttons = render_main_menu(uid)
        await bot.send_message(event.chat_id, text, buttons=buttons, parse_mode='md')

    # ========================================
    # /panel فقط مدیر
    # ========================================
    @bot.on(events.NewMessage(pattern='/panel'))
    async def admin_panel(event):
        uid = int(event.sender_id)
        sender = await event.get_sender()
        username = getattr(sender, 'username', '') or ''
        admin_manager.note_identity(uid, username)
        if not admin_manager.is_admin(uid, username):
            return
        premium_emoji.cancel(uid)
        crypto.cancel(uid)
        ai.cancel(uid)
        flow_states.pop(uid, None)
        admin.states.pop(uid, None)
        text, buttons = admin.render_main(uid, username)
        await bot.send_message(event.chat_id, text, buttons=buttons, parse_mode='md')

    # ========================================
    # دستورات گروه اصلی بازی
    # ========================================
    @bot.on(events.NewMessage(func=lambda e: not e.is_private))
    async def game_group_handler(event):
        if event.sender_id is None or is_bot_off(int(event.sender_id)):
            return
        if await group_transfer.handle_message(event):
            return
        if await balance.handle_message(event):
            return
        await game.handle_message(event)

    # ========================================
    # پیام‌های خصوصی
    # ========================================
    @bot.on(events.NewMessage(func=lambda e: e.is_private))
    async def msg_handler(event):
        uid = event.sender_id

        if is_bot_off(uid):
            return

        sender = event.sender
        db.touch_user(
            uid,
            username=getattr(sender, 'username', '') or '',
            first_name=getattr(sender, 'first_name', '') or ''
        )
        tabchi_models.touch_profile(
            uid,
            username=getattr(sender, 'username', '') or '',
            first_name=getattr(sender, 'first_name', '') or '',
            last_name=getattr(sender, 'last_name', '') or '',
            registered_at=db.get_user_settings(uid).get('registered_at') or None,
        )
        admin_manager.note_identity(uid, getattr(sender, 'username', '') or '')

        text = (event.text or '').strip()

        # Owner curation commands work without an active panel input state.
        if await admin.handle_emoji_curation(event):
            return

        # Support/admin input flows get priority over generic bot workflows,
        # including text that happens to begin with '/'.
        if support.has_state(uid):
            if await support.handle_message(event):
                return
        if uid in admin_game.states:
            if await admin_game.handle_message(event):
                return
        if uid in admin.states:
            if await admin.handle_message(event):
                return

        if ai.has_state(uid):
            if await ai.handle_message(event):
                return
        if crypto.has_state(uid):
            if await crypto.handle_message(event):
                return

        # Translation belongs to the self account; stale bot commands only show guidance.
        if is_translate_command(text):
            await event.reply('ترجمه در سلف فعال است. روی متن ریپلای کن و بنویس: .ترجمه فارسی', parse_mode=None)
            return
        if is_ai_command(text):
            response = await execute_ai_text(uid, text)
            for chunk in split_telegram_text(response):
                await event.reply(chunk, parse_mode=None)
            return

        # Emoji extraction accepts entities in captions and text, even text
        # beginning with '/', but leaves bot commands to their own handlers.
        command = text.split(maxsplit=1)[0].split('@')[0] if text else ''
        if command not in {'/start', '/panel'} and premium_emoji.has_state(uid):
            if not login_states.get(uid):
                if await premium_emoji.handle_message(event):
                    return

        if text.startswith('/'):
            return

        # Tabchi owns only users currently inside a Tabchi input flow.
        if tabchi.has_state(uid):
            if await tabchi.handle_message(event):
                return

        # --------------------------------
        # گفتگوی لاگین سلف
        # --------------------------------
        if uid in login_states:
            state = login_states[uid]
            step = state.get('step')
            if not active_state(uid, state, event.chat_id):
                cleanup_state(uid)
                await event.reply('نشست ورود منقضی شده؛ دوباره شروع کنید.')
                return
            if text and 'لغو' in text:
                if step == 'FINALIZING':
                    await event.reply('اتصال در حال نهایی‌شدن است؛ کمی صبر کنید.')
                    return
                cleanup_state(uid, expected=state)
                await event.reply(ui.LOGIN_CANCELLED, buttons=ReplyKeyboardHide())
                return

            if step == 'WAIT_PHONE':
                if text and 'لغو' in text:
                    cleanup_state(uid, disconnect=True)
                    await event.reply(
                        ui.LOGIN_CANCELLED,
                        buttons=ReplyKeyboardHide(),
                        parse_mode='md'
                    )
                    m_text, m_buttons = render_main_menu(uid)
                    await bot.send_message(event.chat_id, m_text, buttons=m_buttons, parse_mode='md')
                    return

                await send_code_request(bot, event.chat_id, uid, contact=event.contact)
                return

            elif step == 'WAIT_2FA':
                if text:
                    password = text
                    state['step'] = 'VERIFYING_2FA'
                    client = state.get('client')

                    if not client:
                        cleanup_state(uid, disconnect=True)
                        await event.reply("⚠️ نشست منقضی شده است. دوباره `/start` بزن.")
                        return

                    try:
                        await asyncio.wait_for(client.sign_in(
                            phone=state['phone'],
                            password=password,
                            phone_code_hash=state['hash']
                        ), timeout=40)
                        await complete_login(bot, event.chat_id, uid, state, edit_event=None)
                    except errors.PasswordHashInvalidError:
                        if login_states.get(uid) is state:
                            state['step'] = 'WAIT_2FA'
                        await event.reply("❌ **رمز دو مرحله‌ای اشتباه است.** دوباره ارسال کن:", parse_mode='md')
                    except Exception:
                        if login_states.get(uid) is state:
                            cleanup_state(uid)
                        await event.reply('ورود ناموفق بود؛ دوباره شروع کنید.')
                return

            elif step in {'WAIT_CODE', 'SENDING_CODE', 'VERIFYING_CODE', 'VERIFYING_2FA', 'FINALIZING'}:
                return

        st = flow_states.get(uid)
        if st and st.get('step') == 'USERNAME_LOOKUP':
            if text == 'لغو':
                flow_states.pop(uid, None)
                menu_text, menu_buttons = render_main_menu(uid)
                await event.reply(menu_text, buttons=menu_buttons, parse_mode='md')
                return
            try:
                entity = await identity_service.resolve_user(bot, text, allow_bots=True)
            except identity_service.IdentityError as exc:
                if flow_states.get(uid) is st:
                    await event.reply(f'❌ {exc}')
                return
            if flow_states.get(uid) is not st:
                return
            # No registration or wallet side effects for public ID lookup.
            username = '@' + entity.username if entity.username else 'بدون یوزرنیم'
            await event.reply(f'🆔 آیدی عددی: {entity.id}\n👤 یوزرنیم: {username}',
                              buttons=[[ui.inline_button('↩️ بازگشت', b'main_menu', 'secondary')]], parse_mode=None)
            return
        if st and st.get('step', '').startswith('TR_') and text == 'لغو':
            flow_states.pop(uid, None)
            m_text, m_buttons = render_main_menu(uid)
            await event.reply(m_text, buttons=m_buttons, parse_mode='md')
            return

        # --------------------------------
        # گفتگوهای مدیر
        # --------------------------------
        if uid == ADMIN_ID and st and st.get('step', '').startswith('A_'):
            step = st['step']

            if step == 'A_CARD':
                card = text.replace(' ', '').replace('-', '')
                if not card.isdigit() or len(card) != 16:
                    await event.reply("❌ شماره کارت باید 16 رقم باشد. دوباره ارسال کن:")
                    return

                pretty = '-'.join([card[i:i+4] for i in range(0, 16, 4)])
                db.set_global({'card_number': pretty})
                flow_states.pop(uid, None)

                await event.reply(f"✅ شماره کارت ذخیره شد:\n`{pretty}`", parse_mode='md')
                a_text, a_buttons = render_admin_pay_info()
                await bot.send_message(event.chat_id, a_text, buttons=a_buttons, parse_mode='md')
                return

            if step == 'A_HOLDER':
                if not text:
                    await event.reply("❌ نام نمی‌تواند خالی باشد.")
                    return

                db.set_global({'card_holder': text})
                flow_states.pop(uid, None)

                await event.reply(f"✅ نام دریافت‌کننده ذخیره شد: **{text}**", parse_mode='md')
                a_text, a_buttons = render_admin_pay_info()
                await bot.send_message(event.chat_id, a_text, buttons=a_buttons, parse_mode='md')
                return

            if step == 'A_FORCE':
                low = text.lower()
                if low in ('off', 'خاموش', 'none'):
                    db.set_global({'force_channels': []})
                    flow_states.pop(uid, None)
                    await event.reply("✅ جوین اجباری **غیرفعال** شد.", parse_mode='md')
                else:
                    items = [x.strip() for x in text.replace('\n', ',').split(',') if x.strip()]
                    if not items:
                        await event.reply("❌ مقدار نامعتبر. مثال: `@channel1, @channel2` یا `off`")
                        return
                    db.set_global({'force_channels': items})
                    flow_states.pop(uid, None)
                    await event.reply(
                        f"✅ جوین اجباری تنظیم شد:\n`{', '.join(items)}`\n\n"
                        "💡 ربات باید در این چنل‌ها ادمین/عضو باشد تا بتواند عضویت را بررسی کند.",
                        parse_mode='md'
                    )

                a_text, a_buttons = render_admin_menu()
                await bot.send_message(event.chat_id, a_text, buttons=a_buttons, parse_mode='md')
                return

            if step == 'A_BAL_USER':
                target = await resolve_user(text)
                if not target:
                    await event.reply("❌ کاربر پیدا نشد. آیدی عددی یا یوزرنیم درست ارسال کن:")
                    return

                st['target'] = target
                st['step'] = 'A_BAL_AMOUNT'

                s = db.get_user_settings(target)
                await event.reply(
                    f"👤 کاربر پیدا شد: `{target}`\n"
                    f"💎 موجودی فعلی: **{fmt(s['diamonds'])} الماس**\n\n"
                    f"مقدار الماس برای {'افزایش' if st['delta'] > 0 else 'کاهش'} را ارسال کن:",
                    parse_mode='md'
                )
                return

            if step == 'A_BAL_AMOUNT':
                if not text.isdigit() or int(text) <= 0:
                    await event.reply("❌ مقدار باید عدد مثبت باشد. دوباره ارسال کن:")
                    return

                amt = int(text)
                target = st['target']
                delta = st['delta']

                s = db.get_user_settings(target)
                before = s['diamonds']
                from services.balance_service import admin_adjust
                after = admin_adjust(target, delta * amt, floor_zero=True, description='Admin panel adjustment')
                db.update_user_settings(target, {'last_alert_hours': 9999})
                flow_states.pop(uid, None)

                await event.reply(
                    f"✅ موجودی کاربر `{target}` تغییر کرد:\n"
                    f"قبل: **{fmt(before)}** | بعد: **{fmt(after)}** الماس",
                    parse_mode='md'
                )
                return

            if step == 'A_BC':
                if not text and not event.media:
                    await event.reply("❌ متن یا رسانه پیام را ارسال کن:")
                    return

                async def sender(target):
                    if event.media:
                        await bot.send_file(target, event.message, caption=text or None)
                    else:
                        await bot.send_message(target, text, parse_mode='md')

                report_future = await broadcast_queue.add(BroadcastJob(
                    targets=db.iter_user_ids(),
                    sender=sender,
                    batch_size=20,
                    delay=1.0,
                    retries=2,
                ))
                report = await report_future

                flow_states.pop(uid, None)
                await event.reply(
                    f"📢 **پیام همگانی ارسال شد**\n\n"
                    f"✅ موفق: **{fmt(report.success)}**\n"
                    f"❌ ناموفق: **{fmt(report.failed)}**\n"
                    f"🚫 بلاک کرده: **{fmt(report.blocked)}**",
                    parse_mode='md'
                )
                return

        # --------------------------------
        # گفتگوهای خرید کاربر
        # --------------------------------
        if st and st.get('step') == 'MANUAL_AMOUNT':
            if text and 'لغو' in text:
                flow_states.pop(uid, None)
                w_text, w_buttons = render_wallet_menu(uid)
                await event.reply(w_text, buttons=w_buttons, parse_mode='md')
                return

            clean = (text or '').replace(',', '').strip()
            if not clean.isdigit() or int(clean) < MIN_DIAMOND or int(clean) > MAX_DIAMOND:
                await event.reply(
                    f"❌ مقدار نامعتبر است.\nعدد بین **{fmt(MIN_DIAMOND)}** تا **{fmt(MAX_DIAMOND)}** ارسال کن:",
                    parse_mode='md'
                )
                return

            st['amount'] = int(clean)
            st['step'] = 'AMOUNT'

            a_text, a_buttons = render_amount_menu(uid)
            await event.reply(a_text, buttons=a_buttons, parse_mode='md')
            return

        if st and st.get('step') == 'WAIT_SCREENSHOT':
            if text and 'لغو' in text:
                flow_states.pop(uid, None)
                w_text, w_buttons = render_wallet_menu(uid)
                await event.reply(w_text, buttons=w_buttons, parse_mode='md')
                return

            is_image = bool(event.photo) or (
                event.document and
                (getattr(event.document, 'mime_type', '') or '').startswith('image/')
            )

            if not is_image:
                await event.reply("⚠️ لطفاً فقط **اسکرین‌شات رسید (عکس)** ارسال کن.")
                return

            for p in db.list_payments('pending'):
                if p['uid'] == uid:
                    await event.reply(
                        f"⚠️ شما یک درخواست در انتظار داری: `PAY-{p['id']}`\n"
                        "تا تعیین تکلیف آن، درخواست جدید ثبت نمی‌شود.",
                        parse_mode='md'
                    )
                    flow_states.pop(uid, None)
                    return

            amount = int(st.get('amount', MIN_DIAMOND))
            total = amount * PRICE_PER_DIAMOND

            try:
                pay_id = db.create_payment(uid, amount, total)
            except payment_service.PaymentError as exc:
                await event.reply(f'❌ {exc}')
                return
            s = db.get_user_settings(uid)

            info = (
                "🧾 **درخواست شارژ الماس**\n\n"
                f"🧾 کد پیگیری: `PAY-{pay_id}`\n"
                f"👤 نام: **{s['first_name'] or '-'}**\n"
                f"🆔 آیدی عددی: `{uid}`\n"
                f"🏷 یوزرنیم: @{s['username'] or 'ندارد'}\n"
                f"💎 مقدار: **{fmt(amount)} الماس**\n"
                f"💵 مبلغ: **{fmt(total)} تومان**\n"
                f"🕐 زمان: `{get_tehran_time()}`\n\n"
                "برای بررسی، تایید یا رد کن:"
            )

            buttons = [[
                ui.inline_button("✅ تأیید پرداخت", f"pay_ok_{pay_id}", "success"),
                ui.inline_button("❌ رد پرداخت", f"pay_no_{pay_id}", "danger"),
            ]]

            try:
                await bot.send_file(
                    ADMIN_ID,
                    event.message,
                    caption=info,
                    buttons=buttons,
                    parse_mode='md'
                )
            except Exception:
                try:
                    await bot.forward_messages(ADMIN_ID, event.message)
                    await bot.send_message(ADMIN_ID, info, buttons=buttons, parse_mode='md')
                except Exception as e:
                    print(f"⚠️ خطا در ارسال رسید به مدیر: {e}")

            flow_states.pop(uid, None)

            await event.reply(
                "✅ **رسید دریافت شد و برای مدیر ارسال شد.**\n\n"
                f"🧾 کد پیگیری: `PAY-{pay_id}`\n"
                "پس از تایید مدیر، الماس‌ها به‌صورت آنی شارژ می‌شوند.",
                parse_mode='md'
            )
            return

        # --------------------------------
        # گفتگوهای انتقال الماس
        # --------------------------------
        if st and st.get('step') == 'TR_TARGET':
            if text and 'لغو' in text:
                flow_states.pop(uid, None)
                m_text, m_buttons = render_main_menu(uid)
                await event.reply(m_text, buttons=m_buttons, parse_mode='md')
                return

            try:
                target = await transfer_service.validate_recipient(bot, uid, (text or '').strip())
            except transfer_service.TransferError as exc:
                await event.reply(f"❌ {exc}")
                return
            if flow_states.get(uid) is not st or st.get('step') != 'TR_TARGET':
                return

            st['target'] = target
            st['step'] = 'TR_AMOUNT'

            await event.reply(
                f"👤 مقصد: `{target}`\n\n"
                f"💎 **مقدار الماس برای انتقال را ارسال کن:**\n"
                f"(حداقل **{MIN_TRANSFER}** الماس — کارمزد **{TRANSFER_FEE_PERCENT}%** کسر می‌شود)",
                parse_mode='md'
            )
            return

        if st and st.get('step') == 'TR_AMOUNT':
            if text and 'لغو' in text:
                flow_states.pop(uid, None)
                m_text, m_buttons = render_main_menu(uid)
                await event.reply(m_text, buttons=m_buttons, parse_mode='md')
                return

            try:
                amount, fee, recv = transfer_service.quote((text or '').strip())
            except transfer_service.TransferError as exc:
                await event.reply(f"❌ {exc}")
                return
            s = db.get_user_settings(uid)

            if s['diamonds'] < amount:
                await event.reply(
                    f"❌ موجودی کافی نیست.\n"
                    f"💎 موجودی شما: **{fmt(s['diamonds'])}** | درخواستی: **{fmt(amount)}**",
                    parse_mode='md'
                )
                return

            st['request_id'] = secrets.token_hex(16)

            st['amount'] = amount
            st['fee'] = fee
            st['recv'] = recv
            st['step'] = 'TR_CONFIRM'

            await event.reply(
                "🔁 **تأیید انتقال الماس**\n\n"
                f"👤 مقصد: `{st['target']}`\n"
                f"💎 مقدار انتقال: **{fmt(amount)} الماس**\n"
                f"🧾 کارمزد ({TRANSFER_FEE_PERCENT}%): **{fmt(fee)} الماس**\n"
                f"✅ به مقصد می‌رسد: **{fmt(recv)} الماس**\n"
                f"💳 از موجودی شما کسر می‌شود: **{fmt(amount)} الماس**\n"
                f"💎 موجودی فعلی شما: **{fmt(s['diamonds'])} الماس**",
                buttons=[
                    [ui.inline_button("✅ تأیید و انتقال", f"tr_confirm_{st['request_id']}", "success")],
                    [ui.inline_button("✖️ لغو انتقال", f"tr_cancel_{st['request_id']}", "danger")],
                ],
                parse_mode='md'
            )
            return

    # ========================================
    # دکمه‌ها
    # ========================================
    @bot.on(events.CallbackQuery)
    async def cb_handler(event):
        try:
            data = event.data.decode('utf-8')
        except Exception:
            return

        uid = int(event.sender_id)
        log_user_action(uid, 'main_bot_callback', callback=data[:96])
        try:
            sender = await event.get_sender()
            tabchi_models.touch_profile(
                uid, username=getattr(sender, 'username', '') or '',
                first_name=getattr(sender, 'first_name', '') or '',
                last_name=getattr(sender, 'last_name', '') or '',
                registered_at=db.get_user_settings(uid).get('registered_at') or None,
            )
            admin_manager.note_identity(uid, getattr(sender, 'username', '') or '')
        except Exception:
            sender = None

        if is_bot_off(uid):
            return

        if data.startswith('game_'):
            await game.handle_callback(event, data)
            return

        if data.startswith('ag_'):
            await admin_game.handle_callback(event, data)
            return

        if event.chat_id != uid:
            await event.answer('این بخش فقط در گفتگوی خصوصی ربات در دسترس است.', alert=True)
            return

        login = login_states.get(uid)
        if login and not data.startswith('code_'):
            if login.get('step') == 'FINALIZING' or data not in {'main_menu','install_self','trial_begin'}:
                await event.answer('ابتدا ورود را کامل یا لغو کنید.', alert=True)
                return
        if not data.startswith('em_'):
            premium_emoji.cancel(uid)
        if not data.startswith('ai_'):
            ai.cancel(uid)
        if data.startswith('em_') or data.startswith('ai_') or data.startswith('translate_') or data in {'main_menu','install_self','trial_begin','username_lookup','transfer_menu'}:
            flow_states.pop(uid, None)
            support.states.pop(uid, None)
            tabchi.states.pop(uid, None)
            admin.states.pop(uid, None)
            admin_game.states.pop(uid, None)
            if data == 'main_menu':
                cleanup_state(uid, expected=login)

        # New modular callback namespaces.
        if data.startswith('adm2_'):
            await admin.handle_callback(event, data)
            return

        # --------------------------------
        # بررسی پرداخت توسط مدیر
        # --------------------------------
        if data.startswith("pay_ok_") or data.startswith("pay_no_"):
            if uid != ADMIN_ID:
                await event.answer("⛔️ دسترسی ندارید.", alert=True)
                return

            approve = data.startswith("pay_ok_")

            try:
                pay_id = int(data.split("_")[2])
            except Exception:
                return

            pay = db.get_payment(pay_id)

            if not pay:
                await event.answer("❌ درخواست یافت نشد.", alert=True)
                return

            if pay['status'] != 'pending':
                await event.answer("⚠️ این درخواست قبلاً بررسی شده.", alert=True)
                return

            try:
                outcome = payment_service.review(pay_id, approve=approve, reviewer_id=uid)
            except payment_service.PaymentError as exc:
                await event.answer(f'❌ {exc}', alert=True)
                return
            except Exception:
                await event.answer('ثبت پرداخت ناموفق بود؛ دوباره تلاش کنید.', alert=True)
                return
            if outcome['duplicate']:
                await event.answer('این درخواست قبلاً بررسی شده است.', alert=True)
                return

            if approve:
                # Compatibility counters are non-financial; failure cannot replay credit.
                try:
                    s = db.get_user_settings(pay['uid'])
                    db.update_user_settings(pay['uid'], {
                        'total_diamonds_bought': s['total_diamonds_bought'] + pay['diamonds'],
                        'total_spent_toman': s['total_spent_toman'] + pay['amount'],
                        'last_alert_hours': 9999,
                    })
                    db.add_stats({'approved':1,'diamonds_sold':pay['diamonds'],'revenue_toman':pay['amount']})
                except Exception:
                    pass

                note = f"\n\n✅ **وضعیت: تایید شد** — {fmt(pay['diamonds'])} الماس شارژ شد."
                user_msg = (
                    "✅ **درخواست شما تایید شد.**\n\n"
                    f"💎 **{fmt(pay['diamonds'])} الماس** به کیف پول شما اضافه شد.\n"
                    f"🧾 کد پیگیری: `PAY-{pay_id}`"
                )
            else:
                try:
                    db.add_stats({'rejected': 1})
                except Exception:
                    pass

                note = "\n\n❌ **وضعیت: رد شد** — مبلغی شارژ نشد."
                user_msg = (
                    "❌ **درخواست شما رد شد.**\n\n"
                    f"🧾 کد پیگیری: `PAY-{pay_id}`\n"
                    "اگر وجه را واریز کرده‌ای، با مدیر هماهنگ کن."
                )

            try:
                caption = (event.message.message or '') + note
                await event.edit(caption, buttons=None, parse_mode='md')
            except Exception:
                pass

            try:
                await bot.send_message(pay['uid'], user_msg, parse_mode='md')
            except Exception:
                pass

            await event.answer("انجام شد.", alert=False)
            return

        # --------------------------------
        # دکمه‌های مدیر
        # --------------------------------
        if uid == ADMIN_ID and data.startswith("adm_"):
            if data == "adm_menu":
                flow_states.pop(uid, None)
                text, buttons = render_admin_menu()
                await edit_safe(event, text, buttons)

            elif data == "adm_pay_info":
                flow_states.pop(uid, None)
                text, buttons = render_admin_pay_info()
                await edit_safe(event, text, buttons)

            elif data == "adm_card":
                flow_states[uid] = {'step': 'A_CARD'}
                await edit_safe(
                    event,
                    "💳 **شماره کارت جدید را ارسال کن:**\n(16 رقم)",
                    [[ui.inline_button("✖️ لغو", b"adm_pay_info", "danger")]]
                )

            elif data == "adm_holder":
                flow_states[uid] = {'step': 'A_HOLDER'}
                await edit_safe(
                    event,
                    "👤 **نام دریافت‌کننده وجه را ارسال کن:**",
                    [[ui.inline_button("✖️ لغو", b"adm_pay_info", "danger")]]
                )

            elif data == "adm_force":
                flow_states[uid] = {'step': 'A_FORCE'}
                g = db.get_global()
                cur = ", ".join(g['force_channels']) if g['force_channels'] else 'غیرفعال'
                await edit_safe(
                    event,
                    "🔒 **تنظیم جوین اجباری**\n\n"
                    f"فعلی: `{cur}`\n\n"
                    "لیست چنل‌ها را با کاما بفرست:\n`@channel1, @channel2`\n\n"
                    "برای غیرفعال کردن بنویس: `off`",
                    [[ui.inline_button("✖️ لغو", b"adm_menu", "danger")]]
                )

            elif data == "adm_toggle":
                g = db.get_global()
                new_state = not g['bot_enabled']
                db.set_global({'bot_enabled': new_state})

                status = "روشن شد 🟢" if new_state else "خاموش شد 🔴"
                await event.answer(f"ربات {status}", alert=True)

                text, buttons = render_admin_menu()
                await edit_safe(event, text, buttons)

            elif data == "adm_bal":
                flow_states.pop(uid, None)
                await edit_safe(
                    event,
                    "💎 **مدیریت موجودی کاربر**\n\nاول نوع عملیات را انتخاب کن:",
                    [
                        [
                            ui.inline_button("➕ افزایش", b"adm_bal_plus", "success"),
                            ui.inline_button("➖ کاهش", b"adm_bal_minus", "danger"),
                        ],
                        [ui.inline_button("↩️ بازگشت", b"adm_menu", "secondary")],
                    ]
                )

            elif data in ("adm_bal_plus", "adm_bal_minus"):
                delta = 1 if data == "adm_bal_plus" else -1
                flow_states[uid] = {'step': 'A_BAL_USER', 'delta': delta}

                await edit_safe(
                    event,
                    "👤 **آیدی عددی یا یوزرنیم کاربر را ارسال کن:**\n"
                    "مثال: `123456789` یا `@username`",
                    [[ui.inline_button("✖️ لغو", b"adm_bal", "danger")]]
                )

            elif data == "adm_bc":
                flow_states[uid] = {'step': 'A_BC'}
                await edit_safe(
                    event,
                    "📢 **متن پیام همگانی را ارسال کن:**\n"
                    "(می‌توانی عکس با کپشن هم بفرستی)",
                    [[ui.inline_button("✖️ لغو", b"adm_menu", "danger")]]
                )

            elif data == "adm_pending":
                text, buttons = render_admin_pending()
                await edit_safe(event, text, buttons)

            elif data == "adm_stats":
                text, buttons = render_admin_stats()
                await edit_safe(event, text, buttons)

            elif data.startswith("adm_emoji_"):
                flow_states.pop(uid, None)
                try:
                    page = int(data.rsplit('_', 1)[1])
                except Exception:
                    page = 0
                text, buttons = render_admin_custom_emojis(page)
                await edit_safe(event, text, buttons)

            elif data.startswith("adm_emdel_"):
                flow_states.pop(uid, None)
                try:
                    _, _, entry_id, page = data.split('_', 3)
                    deleted = tabchi_models.admin_delete_custom_emoji(int(entry_id))
                    page = int(page)
                except Exception:
                    deleted, page = False, 0
                await event.answer("حذف شد." if deleted else "ایموجی پیدا نشد.", alert=False)
                text, buttons = render_admin_custom_emojis(page)
                await edit_safe(event, text, buttons)

            return

        # --------------------------------
        # بررسی عضویت اجباری برای کاربران
        # --------------------------------
        if data == "force_check":
            joined, missing = await check_force_join(uid)
            if joined:
                await process_referral(uid)
                text, buttons = render_main_menu(uid)
                await edit_safe(event, text, buttons)
            else:
                text, buttons = await force_join_view(uid, missing)
                await edit_safe(event, text, buttons)
                await event.answer("❌ هنوز عضو همه چنل‌ها نشدی.", alert=True)
            return

        if uid != ADMIN_ID and not data.startswith("code_"):
            joined, missing = await check_force_join(uid)
            if not joined:
                text, buttons = await force_join_view(uid, missing)
                await edit_safe(event, text, buttons)
                return

        if data.startswith('ai_'):
            await ai.handle_callback(event, data)
            return
        if data.startswith('translate_'):
            await event.answer('ترجمه به سلف منتقل شده است: .ترجمه فارسی', alert=True)
            return
        if data.startswith('crypto_'):
            await crypto.handle_callback(event, data)
            return
        if data.startswith('em_'):
            await premium_emoji.handle_callback(event, data)
            return
        if data.startswith('trial_'):
            await trial.handle_callback(event, data)
            return
        if data.startswith('support_'):
            await support.handle_callback(event, data)
            return

        # Tabchi callback namespace is isolated under tb_.
        if data.startswith('tb_'):
            await tabchi.handle_callback(event, data)
            return

        # --------------------------------
        # دکمه‌های کد ورود
        # --------------------------------
        if data.startswith("code_"):
            parts = data.split('_')
            if len(parts) != 3:
                return

            try:
                button_uid = int(parts[1])
            except Exception:
                return

            action = parts[2]

            if button_uid != uid:
                await event.answer("❌ این دکمه متعلق به شما نیست.", alert=True)
                return

            await handle_code_button(event, uid, action)
            return

        # --------------------------------
        # منوی اصلی و نصب
        # --------------------------------
        if data == "install_self":
            # Send the installation prompt first. Only remove the old menu after
            # the new UI is visible, so a runtime error can never blank the chat.
            try:
                await start_login(bot, event.chat_id, uid)
            except Exception as exc:
                print(f"❌ install_self failed for {uid}: {type(exc).__name__}: {exc}")
                try:
                    await event.answer(
                        "❌ صفحه نصب باز نشد. دوباره تلاش کن؛ منوی اصلی حفظ شد.",
                        alert=True
                    )
                except Exception:
                    pass
                return

            try:
                await event.delete()
            except Exception:
                pass
            return

        if data == "main_menu":
            flow_states.pop(uid, None)
            crypto.cancel(uid)
            ai.cancel(uid)
            text, buttons = render_main_menu(uid)
            await edit_safe(event, text, buttons)
            return

        # --------------------------------
        # مدیریت سلف (فقط اکانت دارای سشن)
        # --------------------------------
        if data in ("self_menu", "self_toggle", "self_delete", "self_delete_yes"):
            if not has_self_session(uid):
                await event.answer(
                    "⛔️ شما سلف‌بات نصب ندارید؛ این بخش فقط برای اکانتی است که سلف روی آن فعال است.",
                    alert=True
                )
                return

        if data == "self_menu":
            flow_states.pop(uid, None)
            text, buttons = render_self_menu(uid)
            await edit_safe(event, text, buttons)
            return

        if data == "self_toggle":
            s = db.get_user_settings(uid)

            if s['self_enabled']:
                # خاموش کردن: بدون هزینه
                db.update_user_settings(uid, {'self_enabled': False})
                await event.answer("سلف خاموش شد 🔴", alert=True)
            else:
                # During an active 24h trial, starting the self does not consume
                # diamonds. After expiry, the existing paid rule applies.
                if trial_manager.is_active(uid):
                    db.update_user_settings(uid, {'self_enabled': True})
                    await event.answer('سلف روشن شد 🟢 — تست رایگان فعال است.', alert=True)
                else:
                    if s['diamonds'] < SELF_START_COST:
                        await event.answer(
                            f"❌ موجودی کافی نیست.\nبرای روشن کردن سلف به {SELF_START_COST} الماس نیاز است.",
                            alert=True
                        )
                        return

                    from services.balance_service import admin_adjust
                    try:
                        admin_adjust(uid, -SELF_START_COST, description='SELF_START_FEE')
                    except ValueError:
                        await event.answer('موجودی برای روشن کردن سلف کافی نیست.', alert=True)
                        return
                    db.update_user_settings(uid, {'self_enabled': True})
                    db.add_stats({'start_fees': SELF_START_COST})
                    await event.answer(
                        f"سلف روشن شد 🟢 — {SELF_START_COST} الماس کسر شد.",
                        alert=True
                    )

            if db.get_user_settings(uid).get('self_enabled'):
                try:
                    name = f'user_{uid}'
                    task = self_manager.running_sessions.get(name)
                    if task is None or task.done():
                        await self_manager.start_session(name)
                except Exception as exc:
                    print(f'⚠️ self restart after toggle: {exc}')

            text, buttons = render_self_menu(uid)
            await edit_safe(event, text, buttons)
            return

        if data == "self_delete":
            await edit_safe(
                event,
                "⚠️ **هشدار حذف سلف**\n\n"
                "با این کار سلف از اکانت خارج و فایل سشن حذف می‌شود.\n"
                "برای نصب دوباره باید از منوی اصلی اقدام کنی.\n\n"
                "مطمئنی؟",
                [
                    [ui.inline_button("🗑 بله، حذف سلف", b"self_delete_yes", "danger")],
                    [ui.inline_button("↩️ بازگشت", b"self_menu", "secondary")],
                ]
            )
            return

        if data == "self_delete_yes":
            name = f"user_{uid}"
            removed = False
            try:
                removed = await self_manager.delete_session(name)
            except Exception as e:
                print(f"⚠️ delete_session: {e}")

            if removed:
                db.update_user_settings(uid, {"self_enabled": False})
                result_text = (
                    "✅ **سلف از اکانت خارج شد.**\n\n"
                    "• تسک اجرا متوقف شد.\n"
                    "• فایل سشن حذف شد.\n"
                    "• برای نصب دوباره: منوی اصلی → نصب سلف‌بات"
                )
            else:
                db.update_user_settings(uid, {"self_enabled": False})
                result_text = "⚠️ سشن فعالی برای این اکانت یافت نشد."

            await edit_safe(
                event,
                result_text,
                [[ui.inline_button("↩️ بازگشت به پنل", b"main_menu", "secondary")]]
            )
            return

        # --------------------------------
        # کیف پول و خرید
        # --------------------------------
        if data == "wallet_menu":
            flow_states.pop(uid, None)
            text, buttons = render_wallet_menu(uid)
            await edit_safe(event, text, buttons)
            return

        if data == "buy_menu":
            for p in db.list_payments('pending'):
                if p['uid'] == uid:
                    await event.answer(f"شما یک درخواست در انتظار داری: PAY-{p['id']}", alert=True)
                    return

            flow_states[uid] = {'step': 'AMOUNT', 'amount': MIN_DIAMOND}
            text, buttons = render_amount_menu(uid)
            await edit_safe(event, text, buttons)
            return

        purchase_steps = {
            'buy_show': {'AMOUNT'}, 'buy_plus': {'AMOUNT'}, 'buy_minus': {'AMOUNT'},
            'buy_manual': {'AMOUNT'}, 'buy_confirm': {'AMOUNT'}, 'buy_paid': {'PAY_METHOD'},
        }
        if data in purchase_steps:
            purchase_state = flow_states.get(uid)
            if not purchase_state or purchase_state.get('step') not in purchase_steps[data]:
                await event.answer('این دکمه منقضی شده؛ خرید را از منوی کیف پول شروع کنید.', alert=True)
                return

        if data == "buy_show":
            st = flow_states.get(uid, {})
            await event.answer(f"🛒 {fmt(st.get('amount', MIN_DIAMOND))} الماس", alert=False)
            return

        if data == "buy_plus":
            st = flow_states.setdefault(uid, {'step': 'AMOUNT', 'amount': MIN_DIAMOND})
            st['amount'] = min(MAX_DIAMOND, int(st.get('amount', MIN_DIAMOND)) + DIAMOND_STEP)
            text, buttons = render_amount_menu(uid)
            await edit_safe(event, text, buttons)
            return

        if data == "buy_minus":
            st = flow_states.setdefault(uid, {'step': 'AMOUNT', 'amount': MIN_DIAMOND})
            st['amount'] = max(MIN_DIAMOND, int(st.get('amount', MIN_DIAMOND)) - DIAMOND_STEP)
            text, buttons = render_amount_menu(uid)
            await edit_safe(event, text, buttons)
            return

        if data == "buy_manual":
            st = flow_states.setdefault(uid, {'step': 'AMOUNT', 'amount': MIN_DIAMOND})
            st['step'] = 'MANUAL_AMOUNT'

            await edit_safe(
                event,
                f"🔢 **تعداد دلخواه الماس را ارسال کن:**\n"
                f"(بین **{fmt(MIN_DIAMOND)}** تا **{fmt(MAX_DIAMOND)}**)\n\n"
                f"برای لغو، کلمه «لغو» را بفرست.",
                [[ui.inline_button("↩️ بازگشت", b"buy_menu", "secondary")]]
            )
            return

        if data == "buy_confirm":
            g = db.get_global()

            if not g['card_number'] or not g['card_holder']:
                await event.answer("⚠️ پرداخت موقتاً غیرفعال است. مدیر هنوز کارت را تنظیم نکرده.", alert=True)
                return

            st = flow_states.setdefault(uid, {'step': 'AMOUNT', 'amount': MIN_DIAMOND})
            st['step'] = 'PAY_METHOD'

            text, buttons = render_payment_menu(uid)
            await edit_safe(event, text, buttons)
            return

        if data == "buy_paid":
            st = flow_states.get(uid)
            if not st:
                flow_states[uid] = {'step': 'WAIT_SCREENSHOT', 'amount': MIN_DIAMOND}
            else:
                st['step'] = 'WAIT_SCREENSHOT'

            await edit_safe(
                event,
                "📸 **اسکرین‌شات رسید واریز را ارسال کن:**\n\n"
                "پس از ارسال، درخواست برای مدیر می‌رود و پس از تایید، الماس شارژ می‌شود.",
                [[ui.inline_button("✖️ لغو پرداخت", b"wallet_menu", "danger")]]
            )
            return

        # --------------------------------
        # حساب کاربری / رفرال / تنظیمات
        # --------------------------------
        if data == "account_menu":
            flow_states.pop(uid, None)
            text, buttons = render_account_menu(uid)
            await edit_safe(event, text, buttons)
            return

        if data == "ref_menu":
            flow_states.pop(uid, None)
            text, buttons = render_ref_menu(uid, bot_username)
            await edit_safe(event, text, buttons)
            return

        if data == "settings_menu":
            flow_states.pop(uid, None)
            text, buttons = render_settings_menu(uid)
            await edit_safe(event, text, buttons)
            return

        if data == "set_alerts":
            s = db.get_user_settings(uid)
            new_value = not s['alerts_enabled']
            db.update_user_settings(uid, {'alerts_enabled': new_value})

            status = "روشن شد 🟢" if new_value else "خاموش شد 🔴"
            await event.answer(f"هشدارها {status}", alert=True)

            text, buttons = render_settings_menu(uid)
            await edit_safe(event, text, buttons)
            return

        if data == 'username_lookup':
            flow_states[uid] = {'step': 'USERNAME_LOOKUP'}
            await edit_safe(event,
                '🆔 **تبدیل یوزرنیم به آیدی عددی**\n\n'
                'یوزرنیم کاربر یا ربات را بفرستید؛ مثلاً `@username` یا `username`.\n'
                'برای لغو، «لغو» را بفرستید.',
                [[ui.inline_button('↩️ بازگشت', b'main_menu', 'secondary')]])
            return

        # --------------------------------
        # انتقال الماس
        # --------------------------------
        if data == "transfer_menu":
            flow_states[uid] = {'step': 'TR_TARGET'}
            await edit_safe(
                event,
                "🔁 **انتقال الماس**\n\n"
                f"👤 **آیدی عددی یا یوزرنیم کاربر مقصد را ارسال کن:**\n"
                "مثال: `123456789` یا `@username`\n"
                f"(حداقل انتقال: **{MIN_TRANSFER}** الماس | کارمزد: **{TRANSFER_FEE_PERCENT}%**)\n\n"
                "در گروه: روی پیام مقصد ریپلای کن و بنویس `انتقال 100`.\n"
                "برای لغو، کلمه «لغو» را بفرست.",
                [[ui.inline_button("✖️ لغو", b"main_menu", "danger")]]
            )
            return

        if data.startswith("tr_cancel_"):
            st = flow_states.get(uid)
            if not st or data != f"tr_cancel_{st.get('request_id')}":
                await event.answer("این دکمه منقضی شده است.", alert=True)
                return
            flow_states.pop(uid, None)
            text, buttons = render_main_menu(uid)
            await edit_safe(event, text, buttons)
            return

        if data == "tr_confirm" or data.startswith("tr_confirm_"):
            st = flow_states.get(uid)

            if not st or st.get('step') != 'TR_CONFIRM' or data != f"tr_confirm_{st.get('request_id')}":
                await event.answer("⚠️ نشست انتقال منقضی شده. دوباره شروع کن.", alert=True)
                return

            st['step'] = 'TR_PROCESSING'
            try:
                target = await transfer_service.validate_recipient(bot, uid, st['target'])
                if flow_states.get(uid) is not st:
                    return
                receipt = transfer_service.transfer(uid, target, st['amount'], st['request_id'])
            except transfer_service.TransferError as exc:
                if flow_states.get(uid) is st:
                    flow_states.pop(uid, None)
                await event.answer(f"❌ {exc}", alert=True)
                return
            except Exception:
                st['step'] = 'TR_CONFIRM'
                await event.answer("ثبت انتقال ممکن نشد؛ دوباره تلاش کنید.", alert=True)
                return
            amount, fee, recv = receipt['amount'], receipt['fee'], receipt['received']
            flow_states.pop(uid, None)
            # Balance changes and fees are authoritative in wallet_transfers.
            if not receipt['duplicate']:
                try:
                    db.add_stats({'fees_collected': fee})
                    db.update_user_settings(target, {'last_alert_hours': 9999})
                except Exception:
                    pass

            await edit_safe(
                event,
                "✅ **انتقال با موفقیت انجام شد**\n\n"
                f"👤 مقصد: `{target}`\n"
                f"💎 کسرشده از شما: **{fmt(amount)} الماس**\n"
                f"✅ رسیده به مقصد: **{fmt(recv)} الماس**\n"
                f"🧾 کارمزد: **{fmt(fee)} الماس**",
                [[ui.inline_button("↩️ بازگشت به پنل", b"main_menu", "secondary")]]
            )

            try:
                await bot.send_message(
                    target,
                    f"💎 **دریافت الماس**\n\n"
                    f"👤 از کاربر: `{uid}`\n"
                    f"💎 مقدار دریافتی: **{fmt(recv)} الماس**\n"
                    f"💎 موجودی جدید: **{fmt(receipt['recipient_after'])} الماس**",
                    parse_mode='md'
                )
            except Exception:
                pass

            await event.answer("انتقال انجام شد.", alert=False)
            return

    try:
        await bot.run_until_disconnected()
    finally:
        game_scheduler_task.cancel()
        billing_task.cancel()
        await asyncio.gather(game_scheduler_task, billing_task, return_exceptions=True)
