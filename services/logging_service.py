"""Central logging with rotation and secret redaction.

The logger is intentionally dependency-free so it is safe to import from
configuration/database code during early startup.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
import re
from typing import Any

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from config import DATA_DIR
LOG_DIR = os.path.join(DATA_DIR, 'logs')
LOG_FILE = os.path.join(LOG_DIR, 'pishro.log')
_CONFIGURED = False

_SECRET_PATTERNS = (
    re.compile(r'\b\d{6,12}:[A-Za-z0-9_-]{20,}\b'),  # Telegram bot token
    re.compile(r'\baa-[A-Za-z0-9_-]{12,}\b', re.I),  # AvalAI-like key
    re.compile(r'(?i)(api[_ -]?key\s*[=:]\s*)\S+'),
    re.compile(r'(?i)(api[_ -]?hash\s*[=:]\s*)\S+'),
)
_PHONE_RE = re.compile(r'(?<!\d)\+\d{8,15}(?!\d)')


def redact(value: Any) -> str:
    text = str(value)
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            text = pattern.sub(lambda m: (m.group(1) if m.lastindex else '') + '[REDACTED]', text)
        else:
            text = pattern.sub('[REDACTED]', text)
    text = _PHONE_RE.sub('[PHONE]', text)
    return text


class _SafeFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        return redact(rendered)


def setup_logging(level: int | str = logging.INFO) -> logging.Logger:
    global _CONFIGURED
    root = logging.getLogger('pishro')
    if _CONFIGURED:
        return root

    root.setLevel(level)
    root.propagate = False
    fmt = _SafeFormatter('%(asctime)s | %(levelname)s | %(name)s | %(message)s')

    # Console logging must remain available even on a read-only filesystem.
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8'
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError as exc:
        # Logging itself must never prevent the bot/database from importing.
        root.warning('File logging unavailable: %s: %s', type(exc).__name__, exc)

    _CONFIGURED = True
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f'pishro.{name}')


def _fields(fields: dict[str, Any]) -> str:
    return ' '.join(f'{key}={redact(value)}' for key, value in sorted(fields.items()))


def log_user_action(user_id: int | None, action: str, **fields: Any) -> None:
    get_logger('user').info('action=%s user_id=%s %s', action, user_id, _fields(fields))


def log_db_error(action: str, exc: BaseException, **fields: Any) -> None:
    get_logger('database').error(
        'action=%s error=%s %s', action, redact(f'{type(exc).__name__}: {exc}'), _fields(fields)
    )


def log_api_error(action: str, exc: BaseException, **fields: Any) -> None:
    get_logger('api').warning(
        'action=%s error=%s %s', action, redact(f'{type(exc).__name__}: {exc}'), _fields(fields)
    )
