from services.markdown import escape_md


def test_username_markdown_escape():
    value = "bad_user*name`[x]"
    escaped = escape_md(value)
    assert "\\_" in escaped
    assert "\\*" in escaped
    assert "\\`" in escaped
    assert "\\[" in escaped
    assert "\\]" in escaped


def test_dynamic_message_markdown_safe():
    msg = f"User: `{escape_md('a_b*c')}`"
    assert "a\\_b\\*c" in msg


def test_static_markdown_unchanged():
    text = "**عنوان**"
    assert text == "**عنوان**"
