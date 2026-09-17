# Persistent data contract for all future PishroSelf updates

The owner requires old user data to survive every application update. Use this
release as the base for future work. These are implementation requirements;
routine compatible updates do not require a new approval step.

- Keep `storage.default_data_dir()` independent of release folders and the
  current working directory. The established default is
  `Path.home() / 'PishroSelfData'` for the same operating-system account.
- Never ship active `db`, `sessions`, `banner`, `upload`, `message_cache`,
  `voice_settings.json`, `fosh_list.txt`, `storage_meta.json`, or
  `data_location.json` over an installation. Ship initial templates only under
  `defaults`. Actual Python configuration is the persistent `config.py`.
- Existing persistent config values take priority over release defaults. New
  configuration options may gain defaults; existing values must not be reset.
- Keep the startup lock, data validation, verified pre-upgrade backup, and
  post-initialization success marker. Import runtime modules only after selecting
  the stored config. Do not contact Telegram during data migration or tests.
- Never silently create an empty replacement database for missing/corrupt
  registered data. Keep the derived-index recovery from surviving JSON shards.
- Existing SQLite wallets are authoritative. Never re-credit legacy JSON
  balances, renew trials, reset transaction history, or bulk-enable users during
  an upgrade. Preserve unrelated tables, rows, and columns.
- Initial migration copies into a staging directory, verifies the result, and
  preserves the original. Keep `data_location.json` so an old directory cannot
  be imported again as stale current data after a path/account change.
- SQLite backups must use the backup API and include committed WAL contents.
  Keep snapshots outside the application directory. Never automatically delete
  old backups or pre-restore directories.
- Any incompatible storage change must bump `STORAGE_FORMAT`, supply an
  explicit versioned migration from the previous format, and reject unsupported
  downgrade. Preserve these checks when adding the new format.
- Restoration verifies file paths, manifests and checksums first, then keeps
  the current store in a separate directory before publishing the restored one.
- Run the existing suite and extend `tests/test_storage_updates.py` with real
  previous-version fixtures for changes to schema, configuration, or paths.
  Include balances, ledger/history, sessions, settings, media references, and
  failure recovery. Test restart/idempotence and a different release folder.
- State actual test results and limits. Code cannot guarantee survival of disk
  loss or future releases that deliberately discard this contract. Do not claim
  a real Telegram login when tests used mocks.

## Durable billing and translation (v0.09.12 and later)

- Storage format 2 accepts explicit upgrade from format 1 and restoration of
  format 1 backups. Preserve format_migrations and required billing tables.
  Do not permit v0.09.11's runtime billing to operate on format 2 data.
- billing_clocks and wallet_usage are durable account state. Never reset their
  checkpoints/partial periods on reconnect, process restart, code update or
  backup/restore. Treat clock state, debit and ledger as one SQLite transaction.
- Charge 1 diamond per 1800 enabled, non-trial seconds, including process/server
  downtime. Enroll only after session identity verification. Do not invent
  pre-upgrade timestamps; migrate legacy carry once with SQLite taking priority.
- Run usage_service.run_billing_loop independently of Telegram clients. Retain
  startup catch-up, transaction_service's pre-mutation settlement, and ledger
  hooks for zero/top-up transitions. Never call the legacy accrue API for an
  enrolled account; it remains only a compatibility path for external callers.
- Manual disable pauses the account and keeps partial time. No negative wallets
  or debt for periods without credit. Retain trial boundaries through downtime.
- Main-bot translation is removed. SELF_TRANSLATE_ENABLED controls only self
  commands and inline help; the old TRANSLATE_ENABLED flag must not re-enable a
  main-bot translator or accidentally disable self translation on upgrade.
- Preserve reply text/captions, explicit source-target parsing, safe plain-text
  output, chunking, truncation detection and caching only complete translations.
  Google uses its official API only when configured; default AvalAI uses the
  stored credentials. Do not claim provider quality/live access from mock tests.

## v0.09.13 output and market-price contract

- Translation sends raw source text to AvalAI and normalizes accidental JSON
  wrappers on prose output. Preserve deliberately supplied JSON/code. Use the
  v3 translation cache namespace to avoid replaying the old wrapped response.
- SELF_CRYPTO_ENABLED enables only self commands and icrypto panel callbacks;
  keep the independent main-bot CRYPTO_ENABLED preference.
- The owner explicitly authorized the bundled CoinGecko key update. Apply only
  that named config patch after pre-upgrade backup, atomically with its marker.
  Preserve all other settings and never reapply over later user changes.
- All dollar prices come from CoinGecko. Validate provider timestamps, finite
  positive values and full catalogue identities; disambiguate duplicate symbols.
  AI may identify a Persian name candidate only, never a price.
- Toman estimates use USDT/IRR from Nobitex (divide by 10), or Wallex USDTTMN
  (already toman). Display actual source and retrieval time. If both fail,
  retain valid USD and explicitly mark Toman unavailable. Never use a fixed FX
  constant or pretend a missing timestamp is current.
- Retain per-user limits, shared requests/cache, 429 cooldown and key isolation.
  CoinGecko headers must never be forwarded to other providers or redirect hosts.

## Premium Emoji Converter contract (v0.09.13 — Strict Mapping patch)

- `premium_emoji_mapping.py` is data-only. Document IDs come exclusively from
  t.me/CustomEmojiPack channel packs and enter the map ONLY after Telegram's
  own `DocumentAttributeCustomEmoji.alt` resolution equals the target emoji
  exactly (VS16-insensitive). **Semantic matching is mandatory**: a Unicode
  emoji may only become the custom emoji whose real alt is itself.
- Strict Mode (`PREMIUM_EMOJI_STRICT_MODE = True`, default): no generic
  fallback. A mapping key with no alt-validated IDs is inactive (`[]`) and the
  emoji stays plain Unicode. Since DEBUG_FINAL the generic fallback id and the
  whole Premium Prefix system (placeholder emoji, auto-prefix injection,
  `PREMIUM_EMOJI_PREFIX_*` config) are REMOVED; no sent message ever gains an
  automatic emoji. The central mapping file is `emoji_map.json`
  (`tools/export_emoji_map.py` regenerates it; broken/missing file = built-in
  map, never a crash).
- The resolver tool `tools/resolve_premium_emoji_mapping.py` is the only way
  IDs enter/leave the map; its official output is
  `PREMIUM_EMOJI_RESOLVED_MAPPING.json` (document_id/alt/free/animated/
  media_type per ID; active/inactive/rejected per target). No network calls
  happen at import or send time.
- The converter installs ONLY on a verified non-bot self client
  (`account.bot is False`), wrapping send_message/send_file/edit_message/
  _send_album. Bot-token clients, inline panel messages and
  forward_messages must never be wrapped.
- Conversion is text-preserving and idempotent: spans carrying
  MessageEntityCustomEmoji, Code, Pre, Url or TextUrl are never reconverted;
  UTF-16 offsets are computed against the untouched message text. No glyph is
  ever swapped (😂 never becomes a non-😂 premium emoji).
- Emoji rejection retries the caller's original content exactly once, never
  replays committed album chunks, then cools down for 300 seconds.
- The per-account toggle lives in user settings (`premium_emoji_converter`:
  None/True/False). Production-Safe enabling: the engine is active when
  `PREMIUM_EMOJI_ENABLED` is true AND the panel has not explicitly disabled it;
  None means the release default `PREMIUM_EMOJI_CONVERTER_ENABLED` (now True)
  applies; explicit panel choices persist and win. Legacy installs that
  persisted the old release False are flipped once via the marker-guarded
  update `v0.09.13-premium-emoji-converter-default-on` (main.py); the panel
  choice in the database is never touched by that update.
  Pre-send conversion on the client send methods is the ONLY main path;
  `install_premium_emoji_outgoing_injector` is a FALLBACK reserved for
  messages sent from other devices (phone/desktop) that never crossed the
  Python wrappers. Every fallback/error is reported to the admin log bot
  (`services/telegram_logger.py`, token only via `PREMIUM_LOG_BOT_TOKEN`).
- `services/premium_report.py` reads its bot token only from the
  `PREMIUM_REPORT_BOT_TOKEN` environment variable. No token value may be
  committed to the repository.


## Premium Resend + Close + Away contract (v0.09.13)

### Premium Resend Mode (`services/premium_resend.py`)
- Purpose: entity-based conversion silently fails in some chats / Telegram
  server conditions, and messages sent from other devices never cross the
  converter. Resend adds a verify/repair layer AFTER the send:
  send → verify (does the final outgoing message carry the custom entity?)
  → copy the message EXACTLY (text, entities, reply-to, photo/video/document,
  caption, album, silent) → resend through the Unified Pipeline (so the copy
  carries real MessageEntityCustomEmoji) → delete the original.
- Safety rules: the original is deleted ONLY after the resend succeeded; if
  the resend OR the delete fails the original stays (a message is never
  lost). Loop protection: recently-resent message ids are remembered, the
  engine cooldown is honoured, and after 2 consecutive "server stripped the
  entity again" events a 300s cooldown pauses the feature. Albums are
  buffered per grouped_id and resent as ONE album.
- Enabling chain (Production-Safe): `PREMIUM_EMOJI_RESEND_MODE` (config hard
  switch, default True) AND converter `effective_enabled()` AND the
  per-account panel toggle `premium_emoji_resend` (None = untouched → ON).
  Panel: .پنل → «🔁 Resend هوشمند».
- Integration point: the outgoing injector calls
  `PremiumResendManager.handle_outgoing()` FIRST; only when it returns
  'skipped' does the previous edit-fallback run. Resend is never applied to
  forwards, via-bot panel messages or service messages.
- Log block: `[PremiumResend]` (chat / message_id / converted / deleted /
  resent / reason) → premium_emoji.log + admin channel
  (`PREMIUM_EMOJI_RESEND_DEBUG`).

### Debug round: [PREMIUM_CHECK] + server-truth Resend (v0.09.13 DEBUG)
- Why: the resend decision previously used the LOCAL Telethon objects; if
  Telegram strips the entity server-side the local view lies. The flow is
  now: send → `[PREMIUM_CHECK]` → sleep `VERIFY_DELAY_SECONDS` (0.5s) →
  `client.get_messages(chat_id, ids=id)` re-fetch → decide on the SERVER
  copy → resend (copy exactly + custom entities) → delete original.
- `[PREMIUM_CHECK]` block (exact owner spec): chat_id / message_id /
  has_entity / entities / media_type / reply_to, plus a `server_check:`
  line on the decision path (fetch result | mapping result). Logged for
  every relevant outgoing message (has emoji OR has media) whenever the
  resend chain is enabled; local log always, Telegram via
  `PREMIUM_EMOJI_RESEND_DEBUG` (anti-spam kind `premium_check`).
- Fetch outcomes: server copy HAS entity → keep (no resend); server copy
  missing entity → resend; message gone → nothing to do; fetch error →
  fall back to the event view (previous behaviour).
- Album queue: parts buffered by grouped_id → after flush delay sleep 0.5s
  → ALL parts re-fetched once → if any part lacks the entity AND at least
  one mapping exists, the WHOLE album is resent exactly once and originals
  deleted afterwards (never per-part duplicates).
- `.premium debug on|off|status` (self.py): live step-by-step reports of
  every stage (event view → server fetch → decision → result) delivered to
  Saved Messages. Reports are sent with the converter bypass ContextVar and
  their message ids go into an ignore set, so reports never trigger
  [PREMIUM_CHECK]/resend themselves. Status shows the resend chain state,
  engine cooldown and resent/deleted/kept/failed counters.
- Strip observation: the resent copy's own outgoing event is observed too;
  in debug mode it is re-fetched from the server so the log shows whether
  Telegram kept or stripped the entity of the resent message.

### Command .بستن (`services/state_closer.py`)
- Closes EVERY pending operation in the same moment: inline panel messages
  (via_bot messages of the inline bot in that chat), custom-emoji wizard,
  save-message destination input, copy-protected destination input, away
  text input, TTS voice selectors (all chats, selector messages deleted),
  running spam/cleanup tasks (all chats) and admin panel confirmation
  states (memory + database). Always active, works in every chat.
- Reply: «✅ عملیات بسته شد» — no state survives. Log block: `[STATE]`
  (closed / chat / old_state) → system_events.log
  (`STATE_DEBUG`, channel level `AWAY_LOG_LEVEL`).

### Away Message (`services/away.py`)
- Auto-reply for private chats only while enabled; one message per user
  until the owner comes back online (any owner outgoing message resets the
  sent list), a manual reset, or the optional `AWAY_RESET_HOURS` TTL.
  Bot senders, service messages, muted and enemy chats are ignored. The
  away reply itself never triggers a reset (self-ignoring set), and it is
  sent through client.send_message so the Unified Pipeline still applies.
- Settings live in the database: `away_enabled`, `away_text`,
  `away_sent_users` ({user_id: last_sent_ts}). Commands: `.away` (status),
  `.away on|off`, `.away text <متن>`, `.away reset`. Panel: .پنل →
  «💤 Away Message» (toggle / change text via capture / reset list).
- Log block: `[AWAY]` (user / sent / reason) → system_events.log
  (`AWAY_DEBUG`); suppressed repeats stay local-only.
- New config keys are marker-guarded once:
  `v0.09.13-premium-resend-away` (main.py) adds
  `PREMIUM_EMOJI_RESEND_MODE` when missing; manual owner edits always win.
