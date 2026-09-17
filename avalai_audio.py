"""Async AvalAI audio client used by the self-bot.

Routes are OpenAI-compatible:
- POST /v1/audio/transcriptions (speech -> text)
- POST /v1/audio/speech         (text -> speech)
"""
import asyncio
import json
import mimetypes
import os

import aiohttp


class AvalAIError(RuntimeError):
    """Readable error raised for AvalAI HTTP/API failures."""

    def __init__(self, message, *, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def _error_message(body, status):
    body = (body or '').strip()
    if not body:
        return f"خطای AvalAI با کد HTTP {status}"

    try:
        data = json.loads(body)
        error = data.get('error') if isinstance(data, dict) else None
        if isinstance(error, dict):
            message = error.get('message') or error.get('detail')
            if message:
                return str(message)[:700]
        if isinstance(data, dict):
            message = data.get('message') or data.get('detail')
            if message:
                return str(message)[:700]
    except Exception:
        pass

    return body[:700]


async def transcribe_audio(
    file_path,
    *,
    api_key,
    base_url,
    model,
    timeout_seconds=180,
    mime_type=None,
):
    """Upload an audio file to AvalAI and return plain transcript text."""
    if not api_key:
        raise AvalAIError("کلید API سرویس AvalAI تنظیم نشده است.")
    if not os.path.isfile(file_path):
        raise AvalAIError("فایل صوتی برای تبدیل پیدا نشد.")

    mime_type = mime_type or mimetypes.guess_type(file_path)[0] or 'application/octet-stream'
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    headers = {'Authorization': f'Bearer {api_key}'}

    form = aiohttp.FormData()
    form.add_field('model', model)
    form.add_field('response_format', 'text')

    try:
        with open(file_path, 'rb') as audio_file:
            form.add_field(
                'file',
                audio_file,
                filename=os.path.basename(file_path),
                content_type=mime_type,
            )

            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{base_url.rstrip('/')}/audio/transcriptions",
                    headers=headers,
                    data=form,
                ) as response:
                    body = await response.text()
                    if response.status < 200 or response.status >= 300:
                        raise AvalAIError(
                            _error_message(body, response.status),
                            status_code=response.status,
                        )

    except AvalAIError:
        raise
    except asyncio.TimeoutError:
        raise AvalAIError("زمان پاسخ AvalAI تمام شد؛ دوباره تلاش کن.")
    except aiohttp.ClientError as exc:
        raise AvalAIError(f"خطا در اتصال به AvalAI: {exc}")

    text = (body or '').strip()
    if not text:
        raise AvalAIError("AvalAI متن قابل استفاده‌ای برنگرداند.")
    return text
