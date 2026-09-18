from handlers.admin import AdminController


def _button_datas(buttons):
    out = []
    for row in buttons:
        for btn in row:
            data = getattr(btn, 'data', None)
            if isinstance(data, bytes):
                out.append(data.decode('utf-8'))
            elif data is not None:
                out.append(str(data))
    return out


def test_admin_users_page_two():
    controller = AdminController.__new__(AdminController)
    controller._profile = lambda uid: {
        "username": f"user{uid}",
        "first_name": f"User {uid}",
    }

    import handlers.admin as admin_module

    old_iter = admin_module.db.iter_user_ids
    old_count = admin_module.db.count_users
    old_profiles = admin_module.models.list_profiles
    try:
        admin_module.db.iter_user_ids = lambda: iter(range(1, 81))
        admin_module.db.count_users = lambda: 80
        admin_module.models.list_profiles = lambda limit=500: []

        text, buttons = controller.render_users(page=2)

        assert "صفحه: **2/2**" in text
        assert "adm2_users_page_1" in _button_datas(buttons)
    finally:
        admin_module.db.iter_user_ids = old_iter
        admin_module.db.count_users = old_count
        admin_module.models.list_profiles = old_profiles
