"""OFFLINE LOGIC ONLY. Fixture ID/alt/style pairs are not real visual evidence."""
import asyncio
import copy
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from telethon import errors

import config
from services import custom_emoji_service as custom
from services import premium_emoji_injector as engine_module
from tools import premium_emoji_live_smoke as live
from test_premium_emoji_injector import record, engine

A, B, C = engine_module.CHANNEL_DOCUMENT_IDS[:3]


def run(value):
    return asyncio.run(value)


def fixtures():
    resolution = custom.EmojiResolution((record('🔥', A), record('🔥', B)), {C: 'document_invalid'}, {})
    validation = live.validation_report((A, B, C), resolution)
    review = live.curation_template(validation)
    review['groups']['🔥'].update(reviewed_ids=[A, B], ranked_ids=[B, A],
        evidence='OFFLINE simulated comparison; NOT visual verification',
        styles={'fire': {'ranked_ids': [A], 'evidence': 'OFFLINE simulated style'}})
    return validation, review


def test_missing_live_access_never_implies_valid_or_rejected():
    validation = live.validation_report(engine_module.CHANNEL_DOCUMENT_IDS)
    assert validation['valid_ids'] == validation['rejected_ids'] == []
    assert len(validation['unresolved_ids']) == 210
    assert all(r['alt'] is None and r['status'] == 'unresolved' for r in validation['metadata'])
    status = live.mapping_status(validation, live.curation_template(validation))
    assert status['valid_document_ids'] is None
    assert status['status'] != 'production_ready'


def test_validation_exact_alt_groups_and_complete_metadata():
    resolution = custom.EmojiResolution((record('❤', A, free=True), record('❤️', B)), {}, {C: 'timeout'})
    report = live.validation_report((A, B, C), resolution)
    assert report['alt_to_document_ids'] == {'❤': [A], '❤️': [B]}
    assert report['valid_ids'] == [A, B] and report['unresolved_ids'] == [C]
    assert set(report['metadata'][0]) == {'document_id', 'alt', 'free', 'animated', 'media_kind', 'status', 'reason'}


def test_review_preserves_operator_priority_without_claiming_real_visuals():
    validation, review = fixtures()
    report = live.compile_curation(validation, review)
    assert report['curated_premium_map']['🔥'] == [B, A]
    assert report['curated_variant_map']['fire']['🔥'] == [A]
    assert report['visually_approved_curated_ids'] == 2
    assert report['status'] != 'production_ready'
    assert 'evidence' not in str(report)  # Do not echo untrusted free-form review text.


@pytest.mark.parametrize('damage', ['no_live', 'source', 'unreviewed', 'deleted', 'unknown',
    'mismatch', 'no_evidence', 'duplicate', 'unreviewed_style', 'unknown_style'])
def test_review_rejects_unverified_mapping(damage):
    validation, review = fixtures()
    group = review['groups']['🔥']
    if damage == 'no_live': validation['live_metadata_attempted'] = False
    if damage == 'source': review['source'] = 'https://example.com'
    if damage == 'unreviewed': group['reviewed_ids'] = [A]
    if damage == 'deleted': group['ranked_ids'] = [C]
    if damage == 'unknown': group['ranked_ids'] = [123]
    if damage == 'mismatch': validation['metadata'][0]['alt'] = '❤️'
    if damage == 'no_evidence': group['evidence'] = ''
    if damage == 'duplicate': group['ranked_ids'] = [A, A]
    if damage == 'unreviewed_style': group['styles']['fire']['ranked_ids'] = [C]
    if damage == 'unknown_style': group['styles']['fake'] = group['styles'].pop('fire')
    with pytest.raises(ValueError): live.compile_curation(validation, review)


def test_blank_worksheet_cannot_approve_anything():
    validation, _ = fixtures()
    report = live.compile_curation(validation, live.curation_template(validation))
    assert report['visually_approved_curated_ids'] == 0
    assert report['curated_premium_map'] == {}


@pytest.mark.parametrize(('text', 'alt', 'style'), [
    ('گنگ', '😎', 'gang'), ('عشق', '❤️', 'love'), ('لوکس', '👑', 'luxury'), ('موفقیت', '🔥', 'fire')])
def test_exact_smart_variant_priority_logic(monkeypatch, text, alt, style):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_STYLE', 'exact-smart')
    monkeypatch.setattr(engine_module, 'CURATED_PREMIUM_MAP', {alt: [A, B]})
    monkeypatch.setattr(engine_module, 'CURATED_VARIANT_MAP', {style: {alt: [B]}})
    injector = engine(record(alt, A, animated=True), record(alt, B, animated=False))
    output, entities = run(injector.inject(text + ' ' + alt))
    assert output == text + ' ' + alt
    assert entities[0].document_id == B


def test_fallback_variant_then_curated_then_validated_free(monkeypatch):
    monkeypatch.setattr(engine_module, 'CURATED_PREMIUM_MAP', {'🔥': [A]})
    monkeypatch.setattr(engine_module, 'CURATED_VARIANT_MAP', {'fire': {'🔥': [B]}})
    injector = engine(record('🔥', A), record('🔥', B), record('🔥', C, free=True))
    assert [r.document_id for r in injector.catalogue.candidates('🔥', True, 'fire')] == [B, A, C]
    assert [r.document_id for r in injector.catalogue.candidates('🔥', True, 'gang')] == [A, B, C]
    assert [r.document_id for r in injector.catalogue.candidates('🔥', False, 'fire')] == [C]


def test_curated_deleted_and_alt_mismatch_rejection(monkeypatch):
    monkeypatch.setattr(engine_module, 'CURATED_PREMIUM_MAP', {'🔥': [A, B]})
    injector = engine(record('❤️', A), record('🔥', C))
    assert set(injector.catalogue.curated_rejected.values()) == {'not_validated', 'alt_mismatch'}
    assert run(injector.inject('🔥'))[1][0].document_id == C


@pytest.mark.parametrize('style', ['exact-smart', 'smart', 'gang', 'creative'])
def test_only_explicit_creative_may_change_glyph(monkeypatch, style):
    monkeypatch.setattr(config, 'PREMIUM_EMOJI_STYLE', style)
    output, _ = run(engine(record('🔥', A)).inject('✅'))
    assert output == ('🔥' if style == 'creative' else '✅')


def setup_live(monkeypatch, records=None):
    records = records or (record('🔥', A), record('🔥', B))
    resolver = AsyncMock(return_value=custom.EmojiResolution(records, {}, {}))
    monkeypatch.setattr(custom, 'resolve_custom_emoji_catalogue', resolver)
    client = NS(get_me=AsyncMock(return_value=NS(premium=True)), send_message=AsyncMock(return_value=NS(id=1, entities=[])))
    sleep = AsyncMock()
    monkeypatch.setattr(live.asyncio, 'sleep', sleep)
    return client, resolver, sleep


def test_curation_is_metadata_only_by_default_and_resolves_all_ids(monkeypatch):
    client, resolver, _ = setup_live(monkeypatch)
    report = run(live.curation_run(client))
    assert resolver.call_args.args[1] == engine_module.CHANNEL_DOCUMENT_IDS
    client.send_message.assert_not_called()
    assert report['curation']['groups']['🔥']['ranked_ids'] == []


def test_all_candidates_labelled_utf16_batched_and_no_implicit_review(monkeypatch):
    client, _, sleep = setup_live(monkeypatch)
    report = run(live.curation_run(client, send_samples=True, batch_size=1))
    assert client.send_message.await_count == 2
    for call, doc_id in zip(client.send_message.call_args_list, (A, B)):
        peer, text = call.args
        entity = call.kwargs['formatting_entities'][0]
        assert peer == 'me' and str(doc_id) in text
        assert text.encode('utf-16-le')[entity.offset*2:(entity.offset+entity.length)*2].decode('utf-16-le') == '🔥'
    sleep.assert_awaited_once_with(2.0)
    assert 'NOT_PERFORMED' in report['visual_approval']


def test_nonpremium_curation_sends_free_only(monkeypatch):
    client, _, _ = setup_live(monkeypatch, (record('🔥', A), record('🔥', B, free=True)))
    client.get_me.return_value = NS(premium=False)
    report = run(live.curation_run(client, send_samples=True))
    assert report['samples'][0]['status'] == 'skipped_premium_required'
    assert client.send_message.await_count == 1


@pytest.mark.parametrize('exc', [TimeoutError(), errors.DocumentInvalidError(None), errors.FloodWaitError(None, 3)])
def test_curation_send_errors_never_replay(monkeypatch, exc):
    client, _, sleep = setup_live(monkeypatch)
    client.send_message.side_effect = exc
    report = run(live.curation_run(client, send_samples=True))
    assert client.send_message.await_count == 1
    if isinstance(exc, errors.FloodWaitError):
        sleep.assert_awaited_once_with(3)
        assert report['resume_index'] == 0
    else:
        assert report['requires_manual_delivery_check'] is True
        assert report['resume_index'] is None


def test_floodwait_metadata_retry_is_only_for_unresolved_ids(monkeypatch):
    client, resolver, sleep = setup_live(monkeypatch)
    resolver.side_effect = [custom.EmojiResolution((record('🔥', A),), {}, {B: 'FloodWaitError'}, 2),
                            custom.EmojiResolution((record('🔥', B),), {}, {})]
    report = run(live.curation_run(client))
    assert resolver.call_args.args[1] == (B,)
    assert report['validation']['valid_ids'] == [A, B]
    sleep.assert_awaited_once_with(2)


def test_final_gate_needs_all_required_alts_and_styles_logic_only():
    # Entirely synthetic alt assignment: this exercises the release gate only.
    ids = engine_module.CHANNEL_DOCUMENT_IDS
    records = tuple(record(alt, ids[n]) for n, alt in enumerate(live.REQUIRED_ALTS))
    validation = live.validation_report(ids, custom.EmojiResolution(records,
        {i: 'offline_simulated_rejection' for i in ids[len(records):]}, {}))
    review = live.curation_template(validation)
    for alt, group in review['groups'].items():
        group.update(reviewed_ids=group['candidate_ids'], ranked_ids=group['candidate_ids'],
                     evidence='SYNTHETIC GATE TEST ONLY')
    group = review['groups']['🔥']
    group['styles'] = {s: dict(ranked_ids=group['ranked_ids'], evidence='SYNTHETIC TEST') for s in live.STYLES}
    assert live.compile_curation(validation, review)['status'] == 'production_ready'
    altered = copy.deepcopy(review)
    del altered['groups']['🫡']
    assert live.compile_curation(validation, altered)['status'] != 'production_ready'


def cli_args(tmp_path):
    session = tmp_path / 'existing-session.txt'
    session.write_text('OFFLINE_SESSION_SENTINEL')
    return NS(curation=True, output_dir=str(tmp_path / 'output'), review_file=None,
        session_string_file=str(session), ids=None, send_samples=False, limit=None,
        report=None, batch_size=5, start=0, pause=2, max_flood_wait=60)


def test_cli_artifacts_do_not_leak_session_and_disable_implicit_retries(monkeypatch, tmp_path, capsys):
    args = cli_args(tmp_path)
    client, _, _ = setup_live(monkeypatch)
    client.connect, client.disconnect = AsyncMock(), AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=True)
    seen = {}
    def construct(*positional, **kwargs):
        seen.update(kwargs)
        return client
    monkeypatch.setattr(live, 'TelegramClient', construct)
    monkeypatch.setattr(live, 'StringSession', lambda value: object())
    run(live.main(args))
    logger = seen.pop('base_logger')
    assert logger.propagate is False
    assert seen == dict(request_retries=0, flood_sleep_threshold=0, raise_last_call_error=True)
    files = list((tmp_path / 'output').iterdir())
    assert len(files) == 4
    for path in files:
        assert 'OFFLINE_SESSION_SENTINEL' not in path.read_text()
        json.loads(path.read_text())
    assert 'OFFLINE_SESSION_SENTINEL' not in capsys.readouterr().out
    assert (tmp_path / 'existing-session.txt').read_text() == 'OFFLINE_SESSION_SENTINEL'
    client.disconnect.assert_awaited_once()


def test_existing_output_is_rejected_before_connect(monkeypatch, tmp_path):
    args = cli_args(tmp_path)
    (tmp_path / 'output').mkdir()
    construct = AsyncMock()
    monkeypatch.setattr(live, 'TelegramClient', construct)
    with pytest.raises(ValueError): run(live.main(args))
    construct.assert_not_called()


def test_cli_cannot_curation_only_subset_of_sources(tmp_path):
    args = cli_args(tmp_path)
    args.ids = [A]
    with pytest.raises(ValueError): run(live.main(args))
