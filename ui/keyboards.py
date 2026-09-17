"""Keyboards for the main bot's premium emoji feature."""
import ui


def premium_emoji_menu():
    return [
        [ui.inline_button('🔍 استخراج از پیام', 'em_extract', 'primary')],
        [ui.inline_button('🧪 تست ID', 'em_test', 'success')],
        [ui.inline_button('📦 لیست ایموجی‌ها', 'em_list_0', 'primary')],
        [ui.inline_button('🗑 حذف ایموجی', 'em_delete_0', 'danger')],
        [ui.inline_button('↩️ منوی اصلی', 'main_menu', 'secondary')],
    ]


def premium_emoji_back():
    return [[ui.inline_button('↩️ ایموجی پرمیوم / لغو', 'em_menu', 'secondary')]]


def premium_emoji_save(token):
    return [[ui.inline_button('💾 ذخیرهٔ ایموجی‌ها', f'em_save_{token}', 'success')]] + premium_emoji_back()


def premium_emoji_list(rows, page, total, *, deleting=False):
    buttons = []
    if deleting:
        for row in rows:
            buttons.append([ui.inline_button(f"🗑 {row['document_id']}",
                f"em_remove_{row['id']}_{page}", 'danger', icon=False)])
    prefix = 'em_delete' if deleting else 'em_list'
    navigation = []
    if page:
        navigation.append(ui.inline_button('⬅️ قبلی', f'{prefix}_{page - 1}', 'secondary'))
    if (page + 1) * 20 < total:
        navigation.append(ui.inline_button('بعدی ➡️', f'{prefix}_{page + 1}', 'secondary'))
    if navigation:
        buttons.append(navigation)
    return buttons + premium_emoji_back()
