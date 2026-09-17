from handlers.admin import AdminController


def test_admin_users_page_two():
    controller = AdminController.__new__(AdminController)
    controller._profile = lambda uid: {
        "username": f"user{uid}",
        "first_name": f"User {uid}",
    }

    import handlers.admin as admin_module

    old_db = admin_module.db.get_all_user_ids
    old_profiles = admin_module.models.list_profiles
    try:
        admin_module.db.get_all_user_ids = lambda: list(range(1, 81))
        admin_module.models.list_profiles = lambda limit=500: []

        text, buttons = controller.render_users(page=2)

        assert "صفحه: **2/2**" in text
        assert any(
            "adm2_users_page_1" in str(row)
            for row in buttons
        )
    finally:
        admin_module.db.get_all_user_ids = old_db
        admin_module.models.list_profiles = old_profiles
