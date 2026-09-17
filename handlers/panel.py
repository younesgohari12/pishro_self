from __future__ import annotations

import ui
from database import models

TYPE_LABELS = {
    'text': 'متن',
    'photo': 'عکس',
    'video': 'ویدیو',
    'voice': 'ویس',
    'file': 'فایل',
    'gif': 'گیف',
}
MODE_LABELS = {'forward': '🔁 فوروارد', 'normal': '📤 ارسال عادی'}
TARGET_LABELS = {'group': 'گروه‌ها', 'private': 'پیوی‌ها'}


def _target_text(value) -> str:
    targets = models.normalize_send_targets(value)
    return ' + '.join(TARGET_LABELS[t] for t in targets) if targets else 'انتخاب نشده'


def render_tabchi_home(user_id: int):
    banners = models.list_banners(user_id)
    active = sum(1 for b in banners if b.get('status') == 'active')
    blocked = models.blacklist_count(user_id)
    whitelist_enabled, allowed = models.whitelist_policy(user_id)
    text = (
        '🤖 **مدیریت تبچی**\n\n'
        f'📢 بنرها: **{len(banners)}**\n'
        f'🟢 فعال: **{active}**\n'
        f'🚫 بلک لیست: **{blocked}**\n'
        f"✅ لیست سفید: **{'فعال' if whitelist_enabled else 'خاموش'}** ({len(allowed)} مقصد)\n\n"
        'بخش موردنظر را انتخاب کنید:'
    )
    buttons = [
        [ui.inline_button('➕ ساخت بنر جدید', b'tb_new', 'success')],
        [ui.inline_button('📢 لیست بنرها', b'tb_list', 'primary')],
        [ui.inline_button('✅ لیست سفید', b'tb_whitelist', 'success')],
        [ui.inline_button('🚫 بلک لیست', b'tb_blacklist', 'danger')],
        [ui.inline_button('⚙️ تنظیمات تبچی', b'tb_settings', 'secondary')],
        [ui.inline_button('↩️ بازگشت به پنل اصلی', b'main_menu', 'secondary')],
    ]
    return text, buttons


def render_banner_list(user_id: int):
    banners = models.list_banners(user_id)
    text = '📢 **بنرهای من**\n\n'
    buttons = []
    if not banners:
        text += 'هنوز بنری ساخته نشده است.'
    else:
        text += '\n'.join(f"{i}- {b['name']}" for i, b in enumerate(banners, 1))
        for i, banner in enumerate(banners[:50], 1):
            mark = '🟢' if banner['status'] == 'active' else '⏸'
            buttons.append([
                ui.inline_button(
                    f"{i}. {mark} {banner['name'][:38]}",
                    f"tb_b_{banner['id']}",
                    'primary' if banner['status'] == 'active' else 'secondary',
                    icon=False,
                )
            ])
        if len(banners) > 50:
            text += '\n\n⚠️ فقط ۵۰ بنر جدیدتر در این صفحه نمایش داده می‌شود.'
    buttons += [
        [ui.inline_button('➕ ساخت بنر جدید', b'tb_new', 'success')],
        [ui.inline_button('↩️ مدیریت تبچی', b'tb_menu', 'secondary')],
    ]
    return text, buttons


def render_banner_manage(banner: dict):
    status = '🟢 فعال' if banner['status'] == 'active' else '⏸ متوقف'
    targets = _target_text(banner.get('send_target'))
    text = (
        f"📢 **{banner['name']}**\n\n"
        f"وضعیت: **{status}**\n"
        f"نوع: **{TYPE_LABELS.get(banner['type'], banner['type'])}**\n"
        f"ارسال به: **{targets}**\n"
        f"حالت: **{MODE_LABELS.get(banner['send_mode'], banner['send_mode'])}**\n"
        f"زمان: **هر {banner['interval']} دقیقه**"
    )
    bid = banner['id']
    toggle = (
        ui.inline_button('⏸ توقف', f'tb_off_{bid}', 'danger')
        if banner['status'] == 'active'
        else ui.inline_button('▶️ فعال', f'tb_on_{bid}', 'success')
    )
    buttons = [
        [toggle],
        [ui.inline_button('✏️ تغییر نام', f'tb_name_{bid}', 'primary')],
        [ui.inline_button('📝 تغییر متن', f'tb_text_{bid}', 'primary')],
        [ui.inline_button('🖼 تغییر فایل', f'tb_file_{bid}', 'primary')],
        [ui.inline_button('👥 تغییر مقصد ارسال', f'tb_targets_{bid}', 'success')],
        [ui.inline_button('🔁 تغییر حالت ارسال', f'tb_mode_{bid}', 'primary')],
        [ui.inline_button('⏱ تغییر زمان', f'tb_time_{bid}', 'primary')],
        [ui.inline_button('🚫 حذف', f'tb_del_{bid}', 'danger')],
        [ui.inline_button('↩️ لیست بنرها', b'tb_list', 'secondary')],
    ]
    return text, buttons


def render_target_selector(selected, *, prefix: str, cancel_data: str):
    selected_list = models.normalize_send_targets(selected)
    selected_set = set(selected_list)
    group_label = ('☑️ ' if 'group' in selected_set else '⬜️ ') + 'گروه‌ها'
    private_label = ('☑️ ' if 'private' in selected_set else '⬜️ ') + 'پیوی‌ها'
    text = (
        '👥 **انتخاب مقصد ارسال**\n\n'
        'می‌توانید یک یا هر دو گزینه را انتخاب کنید.\n\n'
        f"انتخاب فعلی: **{_target_text(selected_list)}**"
    )
    buttons = [
        [
            ui.inline_button(group_label, f'{prefix}_group', 'primary', icon=False),
            ui.inline_button(private_label, f'{prefix}_private', 'primary', icon=False),
        ],
        [ui.inline_button('✅ تایید', f'{prefix}_ok', 'success')],
        [ui.inline_button('❌ لغو', cancel_data, 'danger')],
    ]
    return text, buttons


def preview_summary(banner_or_draft: dict):
    name = banner_or_draft.get('name') or '-'
    typ = TYPE_LABELS.get(banner_or_draft.get('type'), banner_or_draft.get('type') or '-')
    mode = MODE_LABELS.get(banner_or_draft.get('send_mode'), banner_or_draft.get('send_mode') or '-')
    interval = banner_or_draft.get('interval') or '-'
    targets = _target_text(banner_or_draft.get('send_target'))
    return (
        '📢 **اطلاعات بنر**\n\n'
        f'نام: **{name}**\n'
        f'نوع: **{typ}**\n'
        f'ارسال به: **{targets}**\n'
        f'حالت: **{mode}**\n'
        f'زمان: **هر {interval} دقیقه**'
    )


def render_blacklist(user_id: int):
    rows = models.list_blacklist(user_id)
    text = '🚫 **بلک لیست**\n\n'
    if rows:
        text += '\n'.join(
            f"{i}- {row['target']} ({'پیوی' if row['target_type'] == 'user' else 'گروه'})"
            for i, row in enumerate(rows[:40], 1)
        )
    else:
        text += 'بلک لیست خالی است.'
    buttons = [[ui.inline_button('➕ افزودن به بلک لیست', b'tb_bl_add', 'danger')]]
    for row in rows[:40]:
        buttons.append([
            ui.inline_button(
                f"🗑 {row['target'][:42]}",
                f"tb_bl_del_{row['id']}",
                'danger',
                icon=False,
            )
        ])
    buttons.append([ui.inline_button('↩️ مدیریت تبچی', b'tb_menu', 'secondary')])
    return text, buttons


def render_tabchi_settings(user_id: int):
    banners = models.list_banners(user_id)
    active = [b for b in banners if b.get('status') == 'active']
    group_count = sum('group' in models.get_banner_send_targets(b) for b in banners)
    private_count = sum('private' in models.get_banner_send_targets(b) for b in banners)
    text = (
        '⚙️ **تنظیمات تبچی**\n\n'
        f'📢 کل بنرها: **{len(banners)}**\n'
        f'🟢 بنر فعال: **{len(active)}**\n'
        f'👥 بنرهای گروهی: **{group_count}**\n'
        f'👤 بنرهای پیوی: **{private_count}**\n'
        f'🚫 بلک لیست: **{models.blacklist_count(user_id)}**\n\n'
        'زمان‌بندی هر بنر و نوع مقصد از بخش مدیریت همان بنر قابل تغییر است.'
    )
    buttons = [
        [ui.inline_button('📢 لیست بنرها', b'tb_list', 'primary')],
        [ui.inline_button('✅ لیست سفید', b'tb_whitelist', 'success')],
        [ui.inline_button('🚫 بلک لیست', b'tb_blacklist', 'danger')],
        [ui.inline_button('↩️ مدیریت تبچی', b'tb_menu', 'secondary')],
    ]
    return text, buttons


def render_whitelist(user_id: int, page: int = 0):
    enabled, _ = models.whitelist_policy(user_id)
    rows = models.list_whitelist(user_id)
    pages = max(1, (len(rows) + 19) // 20)
    page = max(0, min(int(page), pages - 1))
    visible = rows[page * 20:(page + 1) * 20]
    text = (
        '✅ **لیست سفید تبچی**\n\n'
        f"وضعیت: **{'فعال' if enabled else 'خاموش'}**\n"
        f'تعداد مقصدها: **{len(rows)}**\n\n'
        'در حالت فعال، همهٔ بنرهای شما فقط به مقصدهای این لیست ارسال می‌شوند. '
        'انتخاب گروه/پیوی هر بنر همچنان اعمال می‌شود و بلک لیست اولویت دارد.\n'
        'لیست فعال و خالی یعنی هیچ ارسالی انجام نمی‌شود.\n'
        'در حالت خاموش، مقصدها طبق تنظیم گروه/پیوی هر بنر و بلک لیست انتخاب می‌شوند.\n\n'
    )
    if not rows:
        text += 'هنوز مقصدی اضافه نشده است.'
    else:
        # Titles appear only on plain button labels, never inside Markdown.
        text += f'صفحهٔ {page + 1} از {pages}؛ برای حذف هر مقصد روی دکمهٔ آن بزنید.'
    buttons = [[ui.inline_button('➕ افزودن مقصد', b'tb_wl_add', 'success')]]
    buttons.append([ui.inline_button(
        '⏸ خاموش کردن فیلتر لیست سفید' if enabled else '▶️ فقط ارسال به لیست سفید',
        b'tb_wl_disable' if enabled else b'tb_wl_enable',
        'danger' if enabled else 'success',
    )])
    for row in visible:
        kind = '👤' if row['kind'] == 'private' else '👥'
        title = ' '.join(row['title'].split())[:28]
        buttons.append([ui.inline_button(
            f"🗑 {kind} {title} | {row['peer_id']}",
            f"tb_wl_del_{row['id']}", 'secondary', icon=False,
        )])
    navigation = []
    if page > 0:
        navigation.append(ui.inline_button('⬅️ قبلی', f'tb_wl_page_{page - 1}', 'secondary'))
    if page + 1 < pages:
        navigation.append(ui.inline_button('بعدی ➡️', f'tb_wl_page_{page + 1}', 'secondary'))
    if navigation:
        buttons.append(navigation)
    buttons.append([ui.inline_button('↩️ مدیریت تبچی', b'tb_menu', 'secondary')])
    return text, buttons
