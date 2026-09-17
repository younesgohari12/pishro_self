"""Crypto UI/controller shared by main bot and self `.ارز` command."""
from __future__ import annotations

from datetime import datetime, timezone
import time

import ui
from services.feature_flags import enabled, CRYPTO_DISABLED
from database import models
from services.crypto_api import crypto_api, CryptoAPIUnavailable, CryptoNotFound, CryptoAmbiguous, Coin
from services.crypto_parser import parse_crypto_command, normalize_asset_text
from services.price_converter import convert_amount, format_number, format_toman, to_toman

from services.ai_assistant import UserRateLimiter, RateLimitExceeded
crypto_limiter = UserRateLimiter(10, 60)

POPULAR = ('BTC', 'ETH', 'TON', 'USDT', 'DOGS')


def _age_text(unix_ts: int) -> str:
    age = max(0, int(time.time() - int(unix_ts)))
    if age < 5:
        return 'همین الان'
    if age < 60:
        return f'{age} ثانیه پیش'
    minutes = age // 60
    return f'{minutes} دقیقه پیش'


def _clock_text(unix_ts: int) -> str:
    dt = datetime.fromtimestamp(int(unix_ts), tz=timezone.utc).astimezone()
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def _amount_label(amount: float) -> str:
    return format_number(amount, max_decimals=8)


def render_price(q) -> str:
    change = '—' if q.change_24h is None else f'{q.change_24h:+.2f}%'
    toman = f'{format_toman(q.price_toman)} تومان' if q.price_toman is not None else 'نرخ بازار تومان فعلاً در دسترس نیست'
    return (
        f'🪙 {q.coin.name} · {q.coin.symbol.upper()}\n'
        f'شناسه: {q.coin.id}\n\n'
        f'💵 دلار: ${format_number(q.price_usd)}\n'
        f'🇮🇷 تومان: {toman}\n'
        f'📈 تغییر ۲۴ ساعت: {change}\n\n'
        f'🕒 قیمت دلار: {_age_text(q.updated_at)}\n'
        + (f'🕒 دریافت نرخ تومان: {_age_text(q.toman_fetched_at)}\n' if q.toman_fetched_at else '')
        + 'منبع دلار: CoinGecko' + (f' | تومان: برآورد با تتر {q.toman_source}' if q.price_toman is not None else '')
    )


async def execute_crypto_text(text: str, *, self_command: bool = False, user_id: int | None = None) -> str:
    if not enabled('self_crypto' if self_command else 'crypto'):
        return CRYPTO_DISABLED
    try:
        command = parse_crypto_command(text)
        if self_command and user_id is not None:
            crypto_limiter.check(user_id)
        if command.amount is None:
            quote = await crypto_api.quote(command.source_query)
            return render_price(quote)

        source, target = await crypto_api.resolve_amount_assets(command.source_query, command.target_query)
        source_q = await crypto_api.quote(source)
        amount = command.amount
        value_usdt = amount * source_q.price_usdt
        value_toman = amount * source_q.price_toman if source_q.price_toman is not None else None

        if target is not None:
            target_q = await crypto_api.quote(target)
            result = convert_amount(amount, source_q.price_usdt, target_q.price_usdt)
            return (
                '🔄 تبدیل ارز\n\n'
                f'مقدار:\n{_amount_label(amount)} {source.symbol.upper()}\n\n'
                'معادل:\n'
                f'{format_number(result)} {target.symbol.upper()}\n\n'
                'ارزش تتر:\n'
                f'{format_number(value_usdt)} USDT\n\n'
                'ارزش تومان:\n'
                f'{format_toman(value_toman)} تومان'
            )

        # Requested amount without explicit target: show Toman + a useful second
        # benchmark. USDT itself is benchmarked against BTC, other coins against USDT.
        if source.symbol.lower() == 'usdt':
            btc_q = await crypto_api.quote('btc')
            btc = convert_amount(amount, source_q.price_usdt, btc_q.price_usdt)
            return (
                f'💰 تبدیل {_amount_label(amount)} USDT\n\n'
                f'🇮🇷 تومان:\n{format_toman(value_toman)} تومان\n\n'
                f'₿ بیت کوین:\n{format_number(btc)} BTC\n\n'
                f'📊 قیمت لحظه‌ای:\n{_clock_text(source_q.updated_at)}'
            )
        return (
            f'💰 تبدیل {_amount_label(amount)} {source.symbol.upper()}\n\n'
            f'🇮🇷 تومان:\n{format_toman(value_toman)} تومان\n\n'
            f'💵 تتر:\n{format_number(value_usdt)} USDT\n\n'
            f'📊 قیمت لحظه‌ای:\n{_clock_text(source_q.updated_at)}'
        )
    except RateLimitExceeded as exc:
        return f'⏳ تعداد درخواست زیاد است؛ {exc.retry_after} ثانیه دیگر تلاش کن.'
    except ValueError:
        return crypto_help_text()
    except CryptoAmbiguous as exc:
        choices = '\n'.join(f'.ارز {c.id} — {c.name}' for c in exc.candidates)
        return 'چند ارز این نام یا نماد را دارند. شناسهٔ دقیق را بفرست:\n\n' + choices
    except CryptoNotFound:
        return '❌ ارز پیدا نشد؛ نماد انگلیسی یا شناسهٔ CoinGecko را امتحان کن.'
    except CryptoAPIUnavailable:
        return '⚠️ دریافت قیمت موقتاً امکان‌پذیر نیست.'
    except Exception:
        return '⚠️ دریافت قیمت موقتاً امکان‌پذیر نیست.'


def crypto_help_text() -> str:
    return (
        '💰 ارز دیجیتال\n\n'
        'نمونه دستورات:\n'
        '`.ارز بیت کوین`\n'
        '`.ارز btc`\n'
        '`.ارز 10 تتر`\n'
        '`.ارز 100 داگز بیت کوین`\n'
        '`.ارز 100 تون به تتر`'
    )


def crypto_menu():
    text = '💰 **ارز دیجیتال**\n\nقیمت لحظه‌ای، تبدیل ارز و ارزهای محبوب.'
    buttons = [
        [ui.inline_button('📊 قیمت ارز', b'crypto_price', 'primary')],
        [ui.inline_button('🔄 تبدیل ارز', b'crypto_convert', 'success')],
        [ui.inline_button('🔥 ارزهای محبوب', b'crypto_popular', 'primary')],
        [ui.inline_button('⚙️ تنظیمات', b'crypto_settings', 'secondary')],
        [ui.inline_button('↩️ بازگشت', b'main_menu', 'secondary')],
    ]
    return text, buttons


def crypto_settings_view(uid: int):
    settings = models.get_crypto_settings(uid)
    favorites = ', '.join(settings['favorite_coins']) or ', '.join(POPULAR)
    text = (
        '⚙️ **تنظیمات ارز دیجیتال**\n\n'
        f'⭐ ارزهای محبوب شما: `{favorites}`\n\n'
        'برای تغییر، دکمه زیر را بزن و Symbolها را با کاما ارسال کن.\n'
        'مثال: `BTC, ETH, TON, SOL, DOGE`'
    )
    buttons = [
        [ui.inline_button('✏️ تغییر ارزهای محبوب', b'crypto_favorites_edit', 'primary')],
        [ui.inline_button('♻️ بازنشانی پیش‌فرض', b'crypto_favorites_reset', 'secondary')],
        [ui.inline_button('↩️ بازگشت', b'crypto_menu', 'secondary')],
    ]
    return text, buttons


class CryptoController:
    def __init__(self, bot):
        self.bot = bot
        self.states: dict[int, dict] = {}

    def has_state(self, uid: int) -> bool:
        return int(uid) in self.states

    def cancel(self, uid: int):
        self.states.pop(int(uid), None)

    async def popular_text(self, uid: int) -> str:
        if not enabled('crypto'):
            return CRYPTO_DISABLED
        favorites = models.get_crypto_settings(uid)['favorite_coins'] or list(POPULAR)
        lines = ['🔥 **ارزهای محبوب**', '']
        for symbol in favorites[:10]:
            try:
                q = await crypto_api.quote(symbol)
                change = '' if q.change_24h is None else f' ({q.change_24h:+.2f}%)'
                lines.append(f'• **{q.coin.symbol.upper()}** — ${format_number(q.price_usd)}{change}')
                lines.append(f'  🇮🇷 {format_toman(q.price_toman)} تومان')
            except (CryptoNotFound, CryptoAPIUnavailable):
                lines.append(f'• **{symbol.upper()}** — ⚠️ در دسترس نیست')
        return '\n'.join(lines)

    async def handle_message(self, event) -> bool:
        uid = int(event.sender_id)
        state = self.states.get(uid)
        if not state:
            return False
        if not enabled('crypto'):
            self.cancel(uid)
            await event.reply(CRYPTO_DISABLED, parse_mode=None)
            return True
        text = (event.text or '').strip()
        if text.lower() in {'لغو', 'cancel'}:
            self.states.pop(uid, None)
            menu_text, buttons = crypto_menu()
            await event.reply(menu_text, buttons=buttons, parse_mode='md')
            return True

        step = state.get('step')
        if step == 'price':
            response = await execute_crypto_text(f'.ارز {text}')
            self.states.pop(uid, None)
            await event.reply(response, buttons=[[ui.inline_button('↩️ ارز دیجیتال', b'crypto_menu', 'secondary')]], parse_mode=None)
            return True
        if step == 'convert':
            body = text if text.startswith('.ارز') else f'.ارز {text}'
            response = await execute_crypto_text(body)
            self.states.pop(uid, None)
            await event.reply(response, buttons=[[ui.inline_button('↩️ ارز دیجیتال', b'crypto_menu', 'secondary')]], parse_mode=None)
            return True
        if step == 'favorites':
            raw = text.replace('،', ',').replace('\n', ',')
            items = [normalize_asset_text(x).upper() for x in raw.split(',') if normalize_asset_text(x)]
            if not 1 <= len(items) <= 10:
                await event.reply('❌ بین 1 تا 10 Symbol با کاما ارسال کن. مثال: `BTC, ETH, TON`', parse_mode='md')
                return True
            validated = []
            try:
                for item in items:
                    coin = await crypto_api.resolve_coin(item)
                    symbol = coin.symbol.upper()
                    if symbol not in validated:
                        validated.append(symbol)
            except CryptoNotFound:
                await event.reply('❌ یکی از ارزها پیدا نشد. Symbolهای معتبر ارسال کن.')
                return True
            except CryptoAPIUnavailable:
                await event.reply('⚠️ دریافت لیست ارزها موقتاً امکان‌پذیر نیست.')
                return True
            models.set_crypto_favorites(uid, validated)
            self.states.pop(uid, None)
            text2, buttons = crypto_settings_view(uid)
            await event.reply('✅ ارزهای محبوب ذخیره شدند.\n\n' + text2, buttons=buttons, parse_mode='md')
            return True
        return False

    async def handle_callback(self, event, data: str) -> bool:
        uid = int(event.sender_id)
        if data.startswith('crypto_') and not enabled('crypto'):
            self.cancel(uid)
            await event.answer(CRYPTO_DISABLED, alert=True)
            return True
        if data == 'crypto_menu':
            self.cancel(uid)
            text, buttons = crypto_menu()
            await event.edit(text, buttons=buttons, parse_mode='md')
            return True
        if data == 'crypto_price':
            self.states[uid] = {'step': 'price'}
            await event.edit(
                '📊 نام فارسی، نام انگلیسی یا Symbol ارز را ارسال کن.\n\nمثال: `بیت کوین` یا `bitcoin` یا `BTC`',
                buttons=[[ui.inline_button('✖️ لغو', b'crypto_menu', 'danger')]], parse_mode='md')
            return True
        if data == 'crypto_convert':
            self.states[uid] = {'step': 'convert'}
            await event.edit(
                '🔄 مقدار و ارز را ارسال کن.\n\nمثال‌ها:\n`100 DOGS BTC`\n`100 تون به تتر`\n`10 USDT`',
                buttons=[[ui.inline_button('✖️ لغو', b'crypto_menu', 'danger')]], parse_mode='md')
            return True
        if data == 'crypto_popular':
            self.cancel(uid)
            try:
                text = await self.popular_text(uid)
            except Exception:
                text = '⚠️ دریافت قیمت موقتاً امکان‌پذیر نیست.'
            await event.edit(text, buttons=[[ui.inline_button('↩️ ارز دیجیتال', b'crypto_menu', 'secondary')]], parse_mode='md')
            return True
        if data == 'crypto_settings':
            self.cancel(uid)
            text, buttons = crypto_settings_view(uid)
            await event.edit(text, buttons=buttons, parse_mode='md')
            return True
        if data == 'crypto_favorites_edit':
            self.states[uid] = {'step': 'favorites'}
            await event.edit(
                '✏️ Symbol ارزهای محبوب را با کاما ارسال کن.\nمثال: `BTC, ETH, TON, SOL, DOGE`',
                buttons=[[ui.inline_button('✖️ لغو', b'crypto_settings', 'danger')]], parse_mode='md')
            return True
        if data == 'crypto_favorites_reset':
            models.set_crypto_favorites(uid, list(POPULAR))
            text, buttons = crypto_settings_view(uid)
            await event.answer('به حالت پیش‌فرض برگشت.', alert=False)
            await event.edit(text, buttons=buttons, parse_mode='md')
            return True
        return False
