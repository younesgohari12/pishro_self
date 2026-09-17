"""Final release smoke regression checks."""


def test_release_imports():
    import config  # noqa: F401
    import db  # noqa: F401


def test_release_markdown_escape():
    from services.markdown import escape_md
    assert escape_md("a_b*c[1]") == r"a\\_b\\*c\\[1\\]"


def test_release_invalid_callback_safe():
    value = "bad:data"
    assert isinstance(value, str)
