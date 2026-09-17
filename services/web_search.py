"""Low-cost web-search gateway for time-sensitive AI questions."""
from __future__ import annotations

import asyncio
import re

import aiohttp

from config import (
    SEARCH_API_KEY,
    SEARCH_API_PROVIDER,
    SEARCH_API_URL,
    SEARCH_HTTP_TIMEOUT,
    SEARCH_MAX_RESULTS,
)


class WebSearchError(RuntimeError):
    pass


_TIME_SENSITIVE_PATTERNS = (
    r'\b(today|tonight|current|currently|latest|newest|recent|news|price|weather|score|version|release|update|outage|exchange rate|stock)\b',
    r'(امروز|امشب|الان|اکنون|فعلی|جدیدترین|آخرین|اخبار|خبر|قیمت|هوا|آب\s*و\s*هوا|نتیجه|نسخه|آپدیت|به.?روزرسانی|بورس|دلار|طلا)',
)
_DYNAMIC_TOPICS = (
    'bitcoin', 'btc', 'ethereum', 'crypto', 'بیت کوین', 'اتریوم', 'ارز دیجیتال',
    'president', 'prime minister', 'ceo', 'رئیس جمهور', 'نخست وزیر',
)


def needs_web_search(question: str) -> bool:
    text = (question or '').strip().lower()
    if not text:
        return False
    if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in _TIME_SENSITIVE_PATTERNS):
        return True
    # Dynamic topics only trigger search when the user also asks a state-like question.
    if any(topic in text for topic in _DYNAMIC_TOPICS):
        state_words = ('چنده', 'چی شده', 'وضعیت', 'قیمت', 'الان', 'latest', 'current', 'price', 'news')
        if any(word in text for word in state_words):
            return True
    return False


def _compact_serper(data: dict, limit: int) -> list[dict[str, str]]:
    out = []
    answer_box = data.get('answerBox')
    if isinstance(answer_box, dict):
        answer = answer_box.get('answer') or answer_box.get('snippet')
        if answer:
            out.append({
                'title': str(answer_box.get('title') or 'Direct answer'),
                'snippet': str(answer),
                'url': str(answer_box.get('link') or ''),
            })
    for item in data.get('organic') or []:
        if not isinstance(item, dict):
            continue
        out.append({
            'title': str(item.get('title') or ''),
            'snippet': str(item.get('snippet') or ''),
            'url': str(item.get('link') or ''),
        })
        if len(out) >= limit:
            break
    return out[:limit]


def _compact_serpapi(data: dict, limit: int) -> list[dict[str, str]]:
    out = []
    box = data.get('answer_box')
    if isinstance(box, dict):
        answer = box.get('answer') or box.get('snippet') or box.get('result')
        if answer:
            out.append({
                'title': str(box.get('title') or 'Direct answer'),
                'snippet': str(answer),
                'url': str(box.get('link') or ''),
            })
    for item in data.get('organic_results') or []:
        if not isinstance(item, dict):
            continue
        out.append({
            'title': str(item.get('title') or ''),
            'snippet': str(item.get('snippet') or ''),
            'url': str(item.get('link') or ''),
        })
        if len(out) >= limit:
            break
    return out[:limit]


async def search_web(query: str, limit: int | None = None) -> list[dict[str, str]]:
    """Search via Serper (default) or SerpAPI using SEARCH_API_KEY."""
    if not SEARCH_API_KEY:
        raise WebSearchError("SEARCH_API_KEY تنظیم نشده است.")
    query = (query or '').strip()
    if not query:
        return []

    limit = max(1, min(int(limit or SEARCH_MAX_RESULTS), 10))
    provider = (SEARCH_API_PROVIDER or 'serper').strip().lower()
    timeout = aiohttp.ClientTimeout(total=SEARCH_HTTP_TIMEOUT)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            if provider == 'serper':
                url = SEARCH_API_URL or 'https://google.serper.dev/search'
                headers = {'X-API-KEY': SEARCH_API_KEY, 'Content-Type': 'application/json'}
                async with session.post(url, headers=headers, json={'q': query, 'num': limit}) as response:
                    if response.status < 200 or response.status >= 300:
                        raise WebSearchError(f"خطای سرویس جستجو: HTTP {response.status}")
                    data = await response.json(content_type=None)
                    if not isinstance(data, dict):
                        raise WebSearchError('پاسخ Serper ساختار JSON معتبری ندارد.')
                    return _compact_serper(data, limit)

            if provider == 'serpapi':
                base = SEARCH_API_URL or 'https://serpapi.com/search'
                params = {'engine': 'google', 'q': query, 'api_key': SEARCH_API_KEY, 'num': limit}
                async with session.get(base, params=params) as response:
                    if response.status < 200 or response.status >= 300:
                        raise WebSearchError(f"خطای سرویس جستجو: HTTP {response.status}")
                    data = await response.json(content_type=None)
                    if not isinstance(data, dict):
                        raise WebSearchError('پاسخ SerpAPI ساختار JSON معتبری ندارد.')
                    return _compact_serpapi(data, limit)

            if provider == 'bing':
                raise WebSearchError(
                    'Bing Search API v7 بازنشسته شده است؛ SEARCH_API_PROVIDER را روی serper یا serpapi بگذار.'
                )

            raise WebSearchError("SEARCH_API_PROVIDER باید serper یا serpapi باشد.")
    except WebSearchError:
        raise
    except asyncio.TimeoutError:
        raise WebSearchError("زمان جستجوی اینترنت تمام شد.")
    except aiohttp.ClientError as exc:
        raise WebSearchError(f"خطا در اتصال به سرویس جستجو: {exc}")
    except Exception as exc:
        raise WebSearchError(f"پاسخ سرویس جستجو معتبر نبود: {type(exc).__name__}")


def render_search_context(results: list[dict[str, str]]) -> str:
    lines = []
    for idx, item in enumerate(results, 1):
        title = (item.get('title') or '').strip()
        snippet = (item.get('snippet') or '').strip()
        url = (item.get('url') or '').strip()
        lines.append(f"[{idx}] {title}\n{snippet}\nSource: {url}".strip())
    return '\n\n'.join(lines)
