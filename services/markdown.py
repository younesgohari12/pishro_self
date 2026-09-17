"""Telegram MarkdownV1 helpers."""


def escape_md(value) -> str:
    """Escape dynamic values before inserting them into Telegram Markdown."""
    if value is None:
        return '-'
    text = str(value)
    for ch in ('_', '*', '`', '[', ']'):
        text = text.replace(ch, '\\' + ch)
    return text
