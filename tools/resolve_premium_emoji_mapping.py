"""Resolve واقعی alt تمام شناسه‌های Custom Emoji پروژه — ابزار رسمی نسخه Strict.

منبع شناسه‌ها فقط دو چیز مجاز است:
  1) شناسه‌های موجود در خود پروژه (premium_emoji_injector.CHANNEL_DOCUMENT_IDS،
     premium_emoji_mapping، تنظیمات prefix) که خودشان از پک‌های عمومی
     https://t.me/CustomEmojiPack گردآوری شده‌اند؛
  2) خروجی رسمی Telegram API: messages.getCustomEmojiDocuments.

هیچ شناسه‌ای حدس زده نمی‌شود و هیچ نگاشت ایموجی→شناسه بدون تأیید
DocumentAttributeCustomEmoji.alt از خود تلگرام ساخته نمی‌شود.

Usage (بدون توکن در کد — فقط ENV):

    PREMIUM_RESOLVE_TOKEN=123:abc python -m tools.resolve_premium_emoji_mapping \
        [--out PREMIUM_EMOJI_RESOLVED_MAPPING.json] \
        [--candidates extra_ids.json]

خروجی PREMIUM_EMOJI_RESOLVED_MAPPING.json شامل برای هر شناسه:
    document_id, alt, free, animated, media_type
و برای هر ایموجی هدف وضعیت:  active | inactive | rejected
    active   → alt واقعی دقیقاً همان ایموجی است
    inactive → شناسه‌ای با آن alt در منابع پروژه پیدا نشد
    rejected → شناسه‌ای قبلاً به این ایموجی نسبت داده شده بود ولی alt تلگرام
               چیز دیگری می‌گوید (هرگز وارد mapping نمی‌شود)

بدون توکن ENV، حالت --check-only فقط وضعیت فایل خروجی موجود را گزارش می‌کند.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402  (اعتبارنامه API فقط از config/ENV — هرگز داخل کد)
from premium_emoji_mapping import (  # noqa: E402
    CHECKED_EMOJIS,
    EMOJI_SOURCE,
    FALLBACK_DOCUMENT_ID,
    PREMIUM_EMOJI_MAP,
)
from services.premium_emoji_injector import CHANNEL_DOCUMENT_IDS  # noqa: E402

RESOLVE_TOKEN_ENVS = ('PREMIUM_RESOLVE_TOKEN', 'PREMIUM_REPORT_BOT_TOKEN')
RESOLVE_METHOD = 'messages.getCustomEmojiDocuments'
DEFAULT_OUT = 'PREMIUM_EMOJI_RESOLVED_MAPPING.json'
BATCH_SIZE = 100
BATCH_SLEEP_SECONDS = 0.6


# --------------------------------------------------------------- pure helpers
def normalize_emoji(emoji):
    """VS16 حذف می‌شود تا ❤ و ❤️ یک ایموجی شمرده شوند؛ متن alt دست‌نخورده
    در خروجی خام ذخیره می‌شود و مقایسه فقط برای تعیین «همان ایموجی» است."""
    return str(emoji or '').replace('\ufe0f', '').strip()


def alt_matches_target(alt, target):
    """تطبیق دقیق معنایی: alt باید دقیقاً همان ایموجی هدف باشد (فقط VS16
    نادیده گرفته می‌شود). هیچ تطبیق فازی، شامل‌بودن یا حدس معنایی مجاز نیست."""
    if not alt or not target:
        return False
    return normalize_emoji(alt) == normalize_emoji(target)


def collect_project_document_ids():
    """تمام شناسه‌های Custom Emoji موجود در پروژه — بدون تکرار، ترتیب پایدار.

    منابع: کاتالوگ کانال، نگاشت فعلی مبدل، شناسه fallback مالک و تنظیمات
    prefix. هیچ منبع خارجی یا حدسی اضافه نمی‌شود.
    """
    ids = []
    for value in CHANNEL_DOCUMENT_IDS:
        ids.append(int(value))
    for value in PREMIUM_EMOJI_MAP.values():
        for item in value if isinstance(value, (list, tuple)) else ():
            ids.append(int(item))
    ids.append(int(FALLBACK_DOCUMENT_ID))
    try:
        import config
        for item in getattr(config, 'PREMIUM_EMOJI_PREFIX_IDS', ()) or ():
            ids.append(int(item))
    except Exception:
        pass  # config خارج از محیط تست ممکن است در دسترس نباشد؛ منابع بالا کافی‌اند
    seen, ordered = set(), []
    for value in ids:
        if value > 0 and value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def classify_targets(documents, claimed=None):
    """تعیین وضعیت هر ایموجی هدف فقط بر اساس alt واقعی تلگرام.

    claimed: نگاشت «ایموجی → شناسه‌های نسبت‌داده‌شده قبلی» (نگاشت فعلی) برای
    شناسایی rejectها؛ شناسه‌ای که alt آن با ایموجی منتسب نمی‌خواند reject است
    (حتی اگر برای همان ایموجی شناسه درست هم پیدا شده باشد).
    """
    claimed = claimed or {}
    alt_index = {}
    for doc_id, meta in (documents or {}).items():
        alt = (meta or {}).get('alt') or ''
        if alt:
            alt_index.setdefault(normalize_emoji(alt), []).append(int(doc_id))
    status = {}
    for target in CHECKED_EMOJIS:
        ids = sorted(alt_index.get(normalize_emoji(target), ()))
        previous = [int(v) for v in claimed.get(target, ()) or ()]
        wrong = [d for d in previous
                 if str(d) in (documents or {})
                 and not alt_matches_target((documents or {})[str(d)].get('alt'), target)]
        alts = {str(d): (documents or {}).get(str(d), {}).get('alt', '')
                for d in wrong}
        if ids:
            entry = {'status': 'active', 'ids': ids}
            if wrong:
                entry['rejected_ids'] = wrong
                entry['telegram_alt'] = alts
            status[target] = entry
            continue
        if previous:
            # قبلاً شناسه‌ای به این ایموجی نسبت داده شده بود ولی تلگرام alt
            # دیگری برای آن می‌گوید → reject (نه فعال، نه fallback).
            status[target] = {'status': 'rejected', 'ids': previous,
                              'telegram_alt': alts}
        else:
            status[target] = {'status': 'inactive', 'ids': []}
    return status


def build_validated_map(target_status):
    """نگاشت نهایی فقط از وضعیت‌های active — هیچ ورودی دیگر مجاز نیست."""
    return {
        emoji: list(data.get('ids') or ())[:15]
        for emoji, data in sorted((target_status or {}).items())
        if data.get('status') == 'active' and data.get('ids')
    }


def extract_document_info(document):
    """استخراج document_id / alt / free / animated / media_type از Document."""
    alt, free = '', False
    for attribute in (getattr(document, 'attributes', None) or []):
        if type(attribute).__name__ == 'DocumentAttributeCustomEmoji':
            alt = getattr(attribute, 'alt', '') or ''
            free = bool(getattr(attribute, 'free', False))
            break
    mime = getattr(document, 'mime_type', '') or ''
    return {
        'document_id': int(getattr(document, 'id', 0) or 0),
        'alt': alt,
        'free': free,
        'animated': mime == 'application/x-tgsticker',
        'media_type': mime,
    }


# ------------------------------------------------------------------ resolver
async def resolve_documents(client, ids, *, progress=None):
    """پرس‌وجوی دسته‌ای GetCustomEmojiDocuments؛ فقط شناسه‌های پرسیده‌شده
    برمی‌گردند (پاسخ تلگرام مرجع است، هیچ چیز پر نمی‌شود).

    import درون تابع است تا import ماژول در تست‌های آفلاین به شبکه/TL وابسته
    نباشد (رفع باگ NameError نسخه قبل)."""
    from telethon.tl.functions.messages import GetCustomEmojiDocumentsRequest
    documents = {}
    calls = 0
    for start in range(0, len(ids), BATCH_SIZE):
        batch = [int(v) for v in ids[start:start + BATCH_SIZE]]
        docs = await client(GetCustomEmojiDocumentsRequest(document_id=batch))
        calls += 1
        for document in docs or []:
            info = extract_document_info(document)
            if info['document_id']:
                documents[str(info['document_id'])] = info
        if progress:
            progress(start + len(batch), len(ids), calls)
        await asyncio.sleep(BATCH_SLEEP_SECONDS)
    return documents, calls


def read_env_token(envs=RESOLVE_TOKEN_ENVS):
    for name in envs:
        token = (os.environ.get(name) or '').strip()
        if token:
            return token
    return ''


def fallback_record(documents):
    """سابقهٔ تصمیم درباره شناسه ثابت مالک (FALLBACK_DOCUMENT_ID).

    طبق قانون Strict این شناسه فقط زمانی می‌توانست وارد نگامت شود که alt
    واقعی تلگرام دقیقاً یکی از ایموجی‌های هدف می‌بود؛ خروجی واقعی تلگرام
    مرجع تصمیم است و هرگز حدس زده نمی‌شود."""
    key = str(FALLBACK_DOCUMENT_ID)
    meta = (documents or {}).get(key) or {}
    alt = meta.get('alt', '')
    in_target = any(alt_matches_target(alt, emoji) for emoji in CHECKED_EMOJIS)
    if not alt:
        decision = 'REJECTED: no alt returned by Telegram'
    elif in_target:
        decision = 'ALLOWED only for the exact matching emoji'
    else:
        decision = f'REJECTED: telegram alt is {alt!r}, not a target emoji'
    return {
        'document_id': FALLBACK_DOCUMENT_ID,
        'telegram_alt': alt,
        'in_target_list': in_target,
        'decision': decision,
    }


def write_output(out_path, documents, target_status, *, calls, missing,
                 candidates_extra=0):
    checked = len(collect_project_document_ids()) + candidates_extra
    payload = {
        'resolved_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'source': EMOJI_SOURCE,
        'method': RESOLVE_METHOD,
        'strict_semantic_matching': True,
        'totals': {
            'checked': checked,
            'resolved': len(documents),
            'missing': missing,
            'calls': calls,
        },
        'fallback_document_id': fallback_record(documents),
        'selection_rule': ('alt must exactly equal the target emoji '
                           '(VS16-insensitive); max 15 ids per emoji; '
                           'empty list = checked but inactive'),
        'documents': documents,
        'target_status': target_status,
        'validated_map': build_validated_map(target_status),
    }
    path = Path(out_path)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding='utf-8')
    return payload


# ---------------------------------------------------------------------- main
async def async_main(args):
    from telethon import TelegramClient
    from telethon.sessions import MemorySession

    ids = collect_project_document_ids()
    extra = 0
    if args.candidates:
        path = Path(args.candidates)
        if path.exists():
            raw = json.loads(path.read_text(encoding='utf-8'))
            known = set(ids)
            for value in raw.get('document_ids', []) if isinstance(raw, dict) else raw:
                value = int(value)
                if value > 0 and value not in known:
                    ids.append(value)
                    known.add(value)
                    extra += 1

    token = read_env_token()
    if not token:
        print('NO_TOKEN — توکن را با PREMIUM_RESOLVE_TOKEN بدهید.')
        return 1
    # اعتبارنامه API از ENV یا config پروژه خوانده می‌شود؛ هرگز داخل کد نیست.
    api_id = int(os.environ.get('PREMIUM_RESOLVE_API_ID',
                                str(getattr(config, 'API_ID', 0) or 0)))
    api_hash = os.environ.get('PREMIUM_RESOLVE_API_HASH',
                              str(getattr(config, 'API_HASH', '') or ''))
    if not api_id or not api_hash:
        print('NO_API_CREDENTIALS — PREMIUM_RESOLVE_API_ID / PREMIUM_RESOLVE_API_HASH '
              'یا API_ID/API_HASH در config لازم است.')
        return 1
    client = TelegramClient(MemorySession(), api_id, api_hash)
    await client.start(bot_token=token)
    try:
        documents, calls = await resolve_documents(
            client, ids,
            progress=lambda done, total, c: print(f'  {done}/{total} ({c})'))
    finally:
        await client.disconnect()

    claimed = {emoji: list(values) for emoji, values in PREMIUM_EMOJI_MAP.items()}
    target_status = classify_targets(documents, claimed)
    missing = len(ids) - len(documents)
    write_output(args.out, documents, target_status, calls=calls,
                 missing=missing, candidates_extra=extra)

    active = [e for e, s in target_status.items() if s['status'] == 'active']
    rejected = [e for e, s in target_status.items() if s['status'] == 'rejected']
    print(f'checked={len(ids)} resolved={len(documents)} missing={missing} calls={calls}')
    print(f'active({len(active)}): {" ".join(active)}')
    print(f'rejected({len(rejected)}): {" ".join(rejected)}')
    print(f'inactive({len(target_status) - len(active) - len(rejected)})')
    print(f'out: {args.out}')
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default=DEFAULT_OUT)
    parser.add_argument('--candidates', default='',
                        help='JSON آرایه/شیء document_ids از همان پک کانال')
    args = parser.parse_args()
    return asyncio.run(async_main(args))


if __name__ == '__main__':
    raise SystemExit(main())
