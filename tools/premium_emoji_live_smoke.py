"""Optional live check; never collected by pytest or required by CI.

Project session (no export or login):
  python tools/premium_emoji_live_smoke.py --user-id 123456789 --curation \
      --send-samples --output-dir live-curation-run-01
Prefer /emoji_curation or /emoji_curation_samples in the running main bot.

Metadata only:
  python tools/premium_emoji_live_smoke.py --session-string-file /path/user_123.txt
Send up to five examples to your own Saved Messages:
  python tools/premium_emoji_live_smoke.py --session-string-file /path/user_123.txt \
      --send-samples --report /path/emoji-live-report.json
Curation (all source IDs, exact-alt groups, small sample batches):
  python tools/premium_emoji_live_smoke.py --session-string-file /path/user_123.txt \
      --curation --send-samples --output-dir /path/new-curation-run

No start(), sign_in(), logout(), session-file write, migration or database write.
A metadata match is NOT approval of a variant's visual quality or personality.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from pathlib import Path
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from telethon import TelegramClient
from telethon import errors
from telethon.sessions import StringSession
from services import custom_emoji_service as custom
from services import premium_emoji_injector as injector

SOURCE = 'https://t.me/CustomEmojiPack'
STYLES = ('gang', 'dark', 'cute', 'love', 'luxury', 'fire', 'neon')
REQUIRED_ALTS = tuple('🔥 ❤️ ❤ 😂 🤣 😭 😎 😈 🥰 😍 😘 😊 😉 😏 🥲 '
    '👍 👎 🙏 🫡 🫶 🤝 👌 👑 💎 ✨ ⚡ 🌟 💫 🖤 🤍 💙 💜 💚 💛 '
    '💀 🗿 💯 🎯 🚀 💪 ✅ ✔️ ❌ 🎉 🎊 🏆 💰 🤔 🤬 😡'.split())


def validation_report(ids, resolution=None):
    """Audit artifact, never a runtime cache. Missing access is not rejection."""
    ids = tuple(ids)
    if not ids or len(set(ids)) != len(ids) or any(i not in injector.CHANNEL_DOCUMENT_IDS for i in ids):
        raise ValueError('Expected unique bundled source IDs')
    records = {r.document_id: r for r in resolution.documents} if resolution else {}
    rejected = dict(resolution.rejected) if resolution else {}
    unresolved = dict(resolution.unresolved) if resolution else {i: 'live_access_unavailable' for i in ids}
    groups, metadata = {}, []
    for doc_id in ids:
        r = records.get(doc_id)
        status = 'valid' if r else 'rejected' if doc_id in rejected else 'unresolved'
        metadata.append(dict(document_id=doc_id, alt=r.alt if r else None,
            free=r.free if r else None, media_kind=r.media_kind if r else None,
            animated=r.animated if r else None, status=status,
            reason=None if r else rejected.get(doc_id, unresolved.get(doc_id, 'not_resolved'))))
        if r:
            groups.setdefault(r.alt, []).append(doc_id)
    return dict(schema_version=1, source=SOURCE,
        generated_at=datetime.now(timezone.utc).isoformat(),
        live_metadata_attempted=resolution is not None,
        source_ids=list(ids), valid_ids=[i for i in ids if i in records],
        rejected_ids=[i for i in ids if i in rejected],
        unresolved_ids=[i for i in ids if i not in records and i not in rejected],
        metadata=metadata, alt_to_document_ids=groups)


def curation_template(validation):
    """A blank worksheet: source order is explicitly NOT aesthetic ranking."""
    return dict(schema_version=1, status='awaiting_visual_review', source=SOURCE,
        instructions='Review ALL candidates for each exact alt. Fill ranked_ids in visual preference order, '
        'reviewed_ids with every inspected candidate, evidence with a comparison note/reference, '
        'and styles only after visually reviewing that style. No automatic visual approval.',
        groups={alt: dict(candidate_ids=ids, reviewed_ids=[], ranked_ids=[], evidence='', styles={})
                for alt, ids in validation['alt_to_document_ids'].items()},
        required_alts=list(REQUIRED_ALTS),
        missing_required_alts=[a for a in REQUIRED_ALTS if a not in validation['alt_to_document_ids']])


def compile_curation(validation, review):
    """Check a human-authored review against this run's live metadata.

    This validates provenance/logic; it cannot prove that a human actually
    looked. Never treats metadata, animation, sample sending or a status string
    as visual approval. No session, free-form credentials or review text copied.
    """
    if review.get('source') != SOURCE or validation.get('source') != SOURCE:
        raise ValueError('Source mismatch')
    if not validation.get('live_metadata_attempted'):
        raise ValueError('Live validation required before accepting a review')
    source_ids = validation['source_ids']
    if len(set(source_ids)) != len(source_ids) or set(source_ids) - set(injector.CHANNEL_DOCUMENT_IDS):
        raise ValueError('Unknown source ID')
    known = {r['document_id']: r for r in validation['metadata'] if r['status'] == 'valid'}
    curated, variants, reviewed = {}, {s: {} for s in STYLES}, set()
    for alt, entry in review.get('groups', {}).items():
        ranked = entry.get('ranked_ids', [])
        if not ranked:
            if entry.get('styles'):
                raise ValueError('Style requires a curated selection')
            continue  # Unfilled template is never approval.
        candidates = validation['alt_to_document_ids'].get(alt, [])
        seen = entry.get('reviewed_ids', [])
        if not candidates or set(seen) != set(candidates) or len(seen) != len(set(seen)):
            raise ValueError('Review every exact-alt candidate before ranking')
        if not isinstance(entry.get('evidence'), str) or not entry['evidence'].strip():
            raise ValueError('Visual comparison evidence is required')
        if not 1 <= len(ranked) <= 5 or len(set(ranked)) != len(ranked):
            raise ValueError('Select one to five unique candidates in preference order')
        for doc_id in seen + ranked:
            r = known.get(doc_id)
            if doc_id not in source_ids or r is None or r['alt'] != alt:
                raise ValueError('Unvalidated ID or exact-alt mismatch')
        if set(ranked) - set(seen):
            raise ValueError('Unreviewed selection')
        curated[alt] = list(ranked)
        reviewed.update(seen)
        for style, selection in entry.get('styles', {}).items():
            if style not in STYLES:
                raise ValueError('Unknown visual style')
            style_ids = selection.get('ranked_ids', [])
            if not style_ids or len(style_ids) > 5 or len(set(style_ids)) != len(style_ids):
                raise ValueError('Invalid style ranking')
            if set(style_ids) - set(ranked) or not str(selection.get('evidence', '')).strip():
                raise ValueError('Style must have visual evidence and use curated IDs')
            variants[style][alt] = list(style_ids)
    blockers = []
    if set(source_ids) != set(injector.CHANNEL_DOCUMENT_IDS): blockers.append('incomplete_source_validation')
    if validation['unresolved_ids']: blockers.append('unresolved_ids')
    if len(curated) < 50: blockers.append('fewer_than_50_curated_emoji')
    missing = [alt for alt in REQUIRED_ALTS if alt not in curated]
    if missing: blockers.append('required_emoji_missing')
    if any(not variants[s] for s in STYLES): blockers.append('unreviewed_styles')
    approved = {i for ids in curated.values() for i in ids}
    return dict(schema_version=1, source=SOURCE,
        status='production_ready' if not blockers else 'candidate_pending_live_curation',
        visual_review_basis='operator_supplied_review_checked_against_live_metadata',
        blockers=blockers, missing_required_alts=missing,
        curated_premium_map=curated, curated_variant_map=variants,
        curated_emoji_count=len(curated), visually_approved_curated_ids=len(approved),
        reviewed_candidate_ids=len(reviewed),
        variant_mappings=sum(len(ids) for m in variants.values() for ids in m.values()))


async def curation_run(client, *, send_samples=False, batch_size=5, limit=None,
                       start=0, pause=2.0, max_flood_wait=60, review=None):
    """Resolve ALL sources, compare ALL same-alt candidates; send only to self.

    No send is retried: on explicit FloodWait wait then stop with a resume
    index; on uncertain transport errors stop without recommending a replay.
    """
    if not 1 <= batch_size <= 5 or pause < 1 or start < 0 or (limit is not None and limit < 1):
        raise ValueError('Invalid small-batch controls')
    if not 0 <= max_flood_wait <= 300:
        raise ValueError('Flood wait budget must be between 0 and 300 seconds')
    ids = injector.CHANNEL_DOCUMENT_IDS
    resolution = await custom.resolve_custom_emoji_catalogue(client, ids)
    # Read-only retry is safe; never repeat already resolved batches.
    if resolution.retry_after and resolution.retry_after <= max_flood_wait:
        await asyncio.sleep(resolution.retry_after)
        retry = await custom.resolve_custom_emoji_catalogue(client, tuple(resolution.unresolved))
        resolution = custom.EmojiResolution(resolution.documents + retry.documents,
            {**resolution.rejected, **retry.rejected}, retry.unresolved, retry.retry_after)
    validation = validation_report(ids, resolution)
    worksheet = curation_template(validation)
    report = dict(validation=validation, curation=worksheet, samples=[],
        visual_approval='NOT_PERFORMED: sample delivery is not a visual review',
        resume_index=None, requires_manual_delivery_check=False,
        retry_after=resolution.retry_after)
    if review is not None:
        report['curation'] = compile_curation(validation, review)
    if not send_samples or resolution.retry_after:
        return report
    premium = bool(getattr(await client.get_me(), 'premium', False))
    rows = [(alt, number, doc_id) for alt, values in validation['alt_to_document_ids'].items()
            for number, doc_id in enumerate(values, 1)]
    known = {r.document_id: r for r in resolution.documents}
    stop = len(rows) if limit is None else min(len(rows), start + limit)
    sent_count = 0
    for index in range(start, stop):
        alt, number, doc_id = rows[index]
        info = dict(index=index, alt=alt, candidate=number, document_id=doc_id)
        if not premium and not known[doc_id].free:
            report['samples'].append(dict(info, status='skipped_premium_required'))
            continue
        label = f'{alt} | candidate {number} | {doc_id}\n'
        text = label + alt
        entity = custom.create_custom_emoji(alt, doc_id).entities[0]
        entity.offset += custom.utf16_length(label)
        try:
            sent = await client.send_message('me', text,
                formatting_entities=[entity], parse_mode=None)
        except errors.FloodWaitError as exc:
            report['samples'].append(dict(info, status='flood_wait', retry_after=exc.seconds))
            if exc.seconds <= max_flood_wait:
                await asyncio.sleep(exc.seconds)
            report['retry_after'] = exc.seconds
            report['resume_index'] = index
            break
        except Exception as exc:
            report['samples'].append(dict(info, status='delivery_unknown', error_type=type(exc).__name__))
            report['requires_manual_delivery_check'] = True
            report['resume_index'] = None
            break  # Never replay a send that may already have reached Telegram.
        report['samples'].append(dict(info, status='sent', message_id=getattr(sent, 'id', None),
            entity_retained=custom.contains_custom_emoji(sent, doc_id)))
        sent_count += 1
        report['resume_index'] = index + 1 if index + 1 < len(rows) else None
        if sent_count % batch_size == 0 and index + 1 < stop:
            await asyncio.sleep(pause)
    return report


def mapping_status(validation, curation):
    approved = curation.get('curated_premium_map', {})
    return dict(status=curation.get('status', 'candidate_pending_live_curation'),
        live_metadata_attempted=validation['live_metadata_attempted'],
        source_document_ids=len(validation['source_ids']),
        valid_document_ids=len(validation['valid_ids']) if validation['live_metadata_attempted'] else None,
        rejected_document_ids=len(validation['rejected_ids']) if validation['live_metadata_attempted'] else None,
        unresolved_document_ids=len(validation['unresolved_ids']),
        actual_alt_count=len(validation['alt_to_document_ids']) if validation['live_metadata_attempted'] else None,
        curated_emoji_count=len(approved),
        visually_approved_curated_ids=curation.get('visually_approved_curated_ids', 0),
        variant_mappings=curation.get('variant_mappings', 0), mapping=approved)


async def smoke(client, *, ids=None, send_samples=False, limit=5):
    ids = tuple(ids if ids is not None else injector.CHANNEL_DOCUMENT_IDS)
    if not ids or any(i not in injector.CHANNEL_DOCUMENT_IDS for i in ids):
        raise ValueError('Only bundled CustomEmojiPack source IDs are allowed')
    resolution = await custom.resolve_custom_emoji_catalogue(client, ids)
    catalogue = injector.EmojiCatalogue()
    catalogue.attempted = True
    catalogue._publish(resolution)
    me = await client.get_me()
    premium = bool(getattr(me, 'premium', False))
    report = catalogue.report(premium=premium)
    report['requested_ids'] = len(ids)
    report['account_premium'] = premium
    report['visual_approval'] = 'NOT_PERFORMED: review actual samples; metadata alone is not visual curation'
    report['metadata'] = [dict(document_id=r.document_id, alt=r.alt, free=r.free,
                               media_kind=r.media_kind) for r in resolution.documents]
    report['samples'] = []
    if send_samples:
        selected = []
        for emoji in catalogue.by_alt:
            choices = catalogue.candidates(emoji, premium)
            if choices:
                selected.append(choices[0])
        for record in selected[:max(0, min(limit, 20))]:
            payload = custom.create_custom_emoji(record.alt, record.document_id)
            try:
                sent = await client.send_message('me', payload.text,
                    formatting_entities=list(payload.entities), parse_mode=None)
            except Exception as exc:
                report['samples'].append(dict(document_id=record.document_id,
                    error_type=type(exc).__name__, entity_retained=None))
                break  # Preserve partial diagnostics; never replay failed sends.
            # No automatic re-send, including silently stripped custom entities.
            report['samples'].append(dict(document_id=record.document_id,
                message_id=getattr(sent, 'id', None),
                entity_retained=custom.contains_custom_emoji(sent, record.document_id)))
    return report


def resolve_session_source(args):
    """Select exactly one source; project names must pass the existing validator."""
    import config
    from services.session_restore import session_user_id
    manual = getattr(args, 'session_string_file', None)
    uid = getattr(args, 'user_id', None)
    name = getattr(args, 'session_name', None)
    if sum(value is not None for value in (manual, uid, name)) != 1:
        raise ValueError('Choose exactly one of --session-string-file, --user-id, --session-name')
    if manual is not None:
        return Path(manual), None
    if uid is not None:
        name = f'user_{uid}'
    expected_uid = session_user_id(name)
    if expected_uid is None:
        raise ValueError('Invalid project session name or user ID')
    directory = Path(config.SESSIONS_DIR).resolve()
    path = directory / f'{name}.txt'
    if path.is_symlink() or path.resolve().parent != directory:
        raise ValueError('Session must be inside configured SESSIONS_DIR')
    return path, expected_uid


@asynccontextmanager
async def access_client(args, *, runtime_only=False):
    """Borrow a registered client, or own a read-only StringSession connection."""
    import config
    path, expected_uid = resolve_session_source(args)
    manager = sys.modules.get('self_manager')
    client = manager.get_client_for_user(expected_uid) if manager and expected_uid else None
    owned = False
    if client is None and runtime_only:
        raise RuntimeError('Self client is not online')
    try:
        if client is None:
            saved = path.read_text(encoding='utf-8-sig').strip()
            # Never send session/transport diagnostics to root logging handlers.
            logger = logging.Logger('premium_emoji_live_private')
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            client = TelegramClient(StringSession(saved), config.API_ID, config.API_HASH,
                request_retries=0, flood_sleep_threshold=0, raise_last_call_error=True,
                base_logger=logger)
            del saved
            owned = True
            await client.connect()
        elif not client.is_connected():
            raise RuntimeError('Self client is not online')
        if not await client.is_user_authorized():
            raise RuntimeError('Existing session is not authorized; no login attempted')
        me = await client.get_me()
        if me is None or getattr(me, 'bot', False):
            raise RuntimeError('A Telegram user session is required')
        if expected_uid is not None and getattr(me, 'id', None) != expected_uid:
            raise RuntimeError('Session identity mismatch')
        yield client
    finally:
        if owned:
            await client.disconnect()


def write_curation_artifacts(destination, result, *, reviewed=False):
    """Write only existing curation outputs to a fresh directory."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    artifacts = {'PREMIUM_EMOJI_LIVE_VALIDATION.json': result['validation'],
        'PREMIUM_EMOJI_CURATION.json': result['curation'],
        'PREMIUM_EMOJI_MAPPING_STATUS.json': mapping_status(result['validation'], result['curation']),
        'PREMIUM_EMOJI_SAMPLES.json': {k: v for k, v in result.items() if k not in ('validation', 'curation')}}
    for name, data in artifacts.items():
        with (destination / name).open('x', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
    if reviewed:
        from pprint import pformat
        with (destination / 'CURATED_MAPS_REVIEWED.py').open('x', encoding='utf-8') as stream:
            stream.write('# Operator review validated against live metadata. Merge after review.\n')
            for name, key in [('CURATED_PREMIUM_MAP', 'curated_premium_map'),
                              ('CURATED_VARIANT_MAP', 'curated_variant_map')]:
                stream.write(name + ' = ' + pformat(result['curation'][key], sort_dicts=False) + '\n')


async def runtime_curation(user_id, *, send_samples=False, review_file=None):
    """In-process only: never create, reconnect or disconnect a runtime client."""
    import config
    # Import in the running process; the CLI never launches the Self runtime.
    import self_manager
    args = SimpleNamespace(user_id=user_id, session_name=None, session_string_file=None)
    root = Path(config.DATA_DIR).resolve()
    parent = root / 'premium_emoji_curation'
    if parent.is_symlink() or parent.resolve().parent != root:
        raise ValueError('Curation output must remain inside DATA_DIR')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    destination = parent / ('run-' + stamp)
    async with access_client(args, runtime_only=True) as client:
        review = json.loads(Path(review_file).read_text(encoding='utf-8')) if review_file else None
        result = await curation_run(client, send_samples=send_samples, review=review)
        write_curation_artifacts(destination, result, reviewed=review is not None)
    return destination, mapping_status(result['validation'], result['curation'])


async def main(args):
    curation_mode = getattr(args, 'curation', False)
    output_dir = getattr(args, 'output_dir', None)
    review_file = getattr(args, 'review_file', None)
    session_path, _ = resolve_session_source(args)
    if curation_mode and args.ids:
        raise ValueError('Curation always resolves all bundled source IDs')
    if (output_dir or review_file) and not curation_mode:
        raise ValueError('Curation options require --curation')
    if output_dir and Path(output_dir).exists():
        raise ValueError('Output directory must be new')
    if args.report:
        target = Path(args.report)
        if target.exists() or target.resolve() == session_path.resolve():
            raise ValueError('Report must be a new path, separate from the session')
    async with access_client(args) as client:
        if curation_mode:
            review = json.loads(Path(review_file).read_text(encoding='utf-8')) if review_file else None
            result = await curation_run(client, send_samples=args.send_samples,
                batch_size=args.batch_size, limit=args.limit, start=args.start,
                pause=args.pause, max_flood_wait=args.max_flood_wait, review=review)
            if output_dir:
                write_curation_artifacts(output_dir, result, reviewed=review is not None)
        else:
            result = await smoke(client, ids=args.ids, send_samples=args.send_samples,
                                 limit=5 if args.limit is None else args.limit)
        output = json.dumps(result, ensure_ascii=False, indent=2)
        if args.report:
            with Path(args.report).open('x', encoding='utf-8') as stream:
                stream.write(output + '\n')
        print(output)


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes invalid values / unknown arguments. Do not.
        self.print_usage(sys.stderr)
        self.exit(2, 'Invalid arguments: choose exactly one of --session-string-file, '
            '--user-id, --session-name; use valid values and supported options.\n')


def build_parser():
    parser = SafeArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument('--session-string-file', help='Existing StringSession file (read only)')
    sources.add_argument('--user-id', type=int, help='Existing project user ID')
    sources.add_argument('--session-name', help='Validated project name, e.g. user_123456789')
    parser.add_argument('--ids', nargs='+', type=int)
    parser.add_argument('--send-samples', action='store_true')
    parser.add_argument('--limit', type=int, help='Sample row limit; curation defaults to all candidates')
    parser.add_argument('--report')
    parser.add_argument('--curation', action='store_true', help='Resolve all source IDs and group by exact alt')
    parser.add_argument('--output-dir', help='New directory for validation, worksheet and sample reports')
    parser.add_argument('--review-file', help='Human-completed curation worksheet; revalidate live before export')
    parser.add_argument('--batch-size', type=int, default=5, help='1 to 5 samples between pauses')
    parser.add_argument('--pause', type=float, default=2, help='Seconds between batches, minimum 1')
    parser.add_argument('--start', type=int, default=0, help='Resume sample row after checking previous delivery')
    parser.add_argument('--max-flood-wait', type=int, default=60, help='Maximum wait in seconds, capped at 300')
    return parser


def cli(argv=None):
    try:
        asyncio.run(main(build_parser().parse_args(argv)))
    except Exception as exc:
        # Exception messages can contain credentials or session data: type only.
        print('Live smoke check failed: ' + type(exc).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(cli())
