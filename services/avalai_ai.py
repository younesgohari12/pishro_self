"""OpenAI-compatible AvalAI chat client used by AI and translation features."""
from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp

from services.ai_error_classifier import classify_provider_error

from config import (
    AVALAI_API_KEY,
    AVALAI_BASE_URL,
    AVALAI_REQUEST_TIMEOUT,
    AI_MODEL,
    AI_MAX_TOKENS,
    AI_TEMPERATURE,
)


class AvalAIChatError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class AvalAITruncatedError(AvalAIChatError):
    pass


def _error_message(body: str, status: int) -> str:
    raw = (body or '').strip()
    if not raw:
        return f"خطای AvalAI با کد HTTP {status}"
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            error = data.get('error')
            if isinstance(error, dict):
                msg = error.get('message') or error.get('detail')
                if msg:
                    return str(msg)[:700]
            msg = data.get('message') or data.get('detail')
            if msg:
                return str(msg)[:700]
    except Exception:
        pass
    return raw[:700]


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, str):
                chunks.append(item)
            elif isinstance(item, dict):
                text = item.get('text')
                if isinstance(text, str):
                    chunks.append(text)
                elif isinstance(text, dict) and isinstance(text.get('value'), str):
                    chunks.append(text['value'])
        return ''.join(chunks).strip()
    if isinstance(content, dict):
        value = content.get('text') or content.get('value')
        if isinstance(value, str):
            return value.strip()
    return ''


async def chat_completion(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout_seconds: int | None = None,
    require_complete: bool = False,
) -> str:
    """Send an OpenAI-compatible /chat/completions request to AvalAI."""
    if not AVALAI_API_KEY:
        raise AvalAIChatError("کلید API سرویس AvalAI تنظیم نشده است.")
    if not messages:
        raise AvalAIChatError("پیام معتبری برای هوش مصنوعی ارسال نشده است.")

    payload = {
        'model': (model or AI_MODEL).strip(),
        'messages': messages,
        'temperature': AI_TEMPERATURE if temperature is None else float(temperature),
        'max_tokens': AI_MAX_TOKENS if max_tokens is None else int(max_tokens),
    }
    timeout = aiohttp.ClientTimeout(total=timeout_seconds or AVALAI_REQUEST_TIMEOUT)
    headers = {
        'Authorization': f'Bearer {AVALAI_API_KEY}',
        'Content-Type': 'application/json',
    }
    url = f"{AVALAI_BASE_URL.rstrip('/')}/chat/completions"

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as response:
                body = await response.text()
                if response.status < 200 or response.status >= 300:
                    _kind, _fa = classify_provider_error(
                        _error_message(body, response.status), response.status
                    )
                    raise AvalAIChatError(
                        _fa,
                        status_code=response.status,
                    )
    except AvalAIChatError:
        raise
    except asyncio.TimeoutError:
        raise AvalAIChatError("زمان پاسخ AvalAI تمام شد؛ دوباره تلاش کن.")
    except aiohttp.ClientError as exc:
        raise AvalAIChatError(f"خطا در اتصال به AvalAI: {exc}")

    try:
        data = json.loads(body)
        choices = data.get('choices') if isinstance(data, dict) else None
        if not choices:
            raise ValueError('missing choices')
        if require_complete and choices[0].get('finish_reason') == 'length':
            raise AvalAITruncatedError('پاسخ مترجم کامل نبود؛ متن را کوتاه‌تر کن.')
        message = choices[0].get('message') or {}
        text = _content_to_text(message.get('content'))
        if not text:
            # A few OpenAI-compatible gateways return `text` directly.
            text = _content_to_text(choices[0].get('text'))
        if not text:
            raise ValueError('empty content')
        return text
    except (TypeError, ValueError, KeyError, IndexError, json.JSONDecodeError):
        raise AvalAIChatError("AvalAI پاسخ قابل استفاده‌ای برنگرداند.")
