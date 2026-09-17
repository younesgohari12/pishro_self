"""UI rendering helpers for the top-level message font feature."""
from __future__ import annotations

import db
import ui
from services.font_formatter import FONT_STYLES, build_entities, normalize_font_style


def _settings(user_id: int):
    s = db.get_user_settings(user_id)
    return bool(s.get("message_font_enabled", False)), normalize_font_style(s.get("message_font_style"))


def render_font_menu(user_id: int):
    enabled, style = _settings(user_id)
    status = "🟢 فعال" if enabled else "🔴 خاموش"
    current = FONT_STYLES[style]
    text = (
        "🔤 **مدیریت فونت**\n\n"
        f"وضعیت: **{status}**\n"
        f"فونت انتخابی: **{current}**\n\n"
        "فونت روی پیام‌های متنی جدیدی که از حساب ارسال می‌کنی اعمال می‌شود."
    )
    buttons = [
        [ui.inline_button("🔤 انتخاب فونت", b"msgfont_select", "primary")],
        [ui.inline_button("👁 پیش نمایش", b"msgfont_preview", "success")],
        [
            ui.inline_button("✅ فعال کردن", b"msgfont_enable", "success"),
            ui.inline_button("❌ خاموش کردن", b"msgfont_disable", "danger"),
        ],
        [ui.inline_button("↩️ بازگشت به پنل اصلی", b"back_main", "secondary")],
    ]
    return text, buttons


def render_font_selector(user_id: int):
    _enabled, current = _settings(user_id)
    text = (
        "🔤 **انتخاب فونت پیام**\n\n"
        f"انتخاب فعلی: **{FONT_STYLES[current]}**\n\n"
        "یکی از حالت‌های واقعی متن تلگرام را انتخاب کن:"
    )
    labels = [
        ("bold", "1- 𝗕𝗼𝗹𝗱 • ضخیم"),
        ("italic", "2- 𝘐𝘵𝘢𝘭𝘪𝘤 • کج"),
        ("bold_italic", "3- 𝗕𝗼𝗹𝗱 + 𝘐𝘵𝘢𝘭𝘪𝘤"),
        ("strike", "4- خط خورده"),
        ("underline", "5- زیر خط دار"),
        ("monospace", "6- Monospace"),
    ]
    buttons = []
    for key, label in labels:
        prefix = "✅ " if key == current else ""
        buttons.append([
            ui.inline_button(
                prefix + label,
                f"msgfont_set_{key}",
                "success" if key == current else "primary",
                icon=False,
            )
        ])
    buttons.append([ui.inline_button("↩️ مدیریت فونت", b"msgfont_menu", "secondary")])
    return text, buttons


def render_font_preview(user_id: int):
    enabled, style = _settings(user_id)
    status = "🟢 فعال" if enabled else "🔴 خاموش"
    sample = "سلام Younes — این یک Sample Text 123 است."
    prefix = (
        "👁 پیش نمایش فونت\n\n"
        f"حالت: {FONT_STYLES[style]}\n"
        f"وضعیت سرویس: {status}\n\n"
        "نمونه:\n"
    )
    text = prefix + sample
    entities = build_entities(text, style, start_char=len(prefix), end_char=len(text))
    buttons = [
        [ui.inline_button("🔤 انتخاب فونت", b"msgfont_select", "primary")],
        [ui.inline_button("↩️ مدیریت فونت", b"msgfont_menu", "secondary")],
    ]
    return text, buttons, entities
