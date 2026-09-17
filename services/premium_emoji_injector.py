"""Premium Emoji Injection Engine, layered on custom_emoji_service.

Only the connected self client is wrapped. Telethon still owns transport,
parsing, replies, media, rate limits, login and sessions. The catalogue is
resolved once per process from real channel IDs, never from guessed alt text.
"""
from __future__ import annotations

import asyncio
import copy
import inspect
import logging
import re
import time
import sys
from collections import OrderedDict
from contextvars import ContextVar
from functools import wraps

import config
from telethon import errors, utils
from telethon.tl.types import (
    Message, MessageEntityCustomEmoji, MessageEntityCode, MessageEntityPre,
    MessageEntityUrl, MessageEntityTextUrl,
)
from services import away_bypass
from services import custom_emoji_service as custom

logger = logging.getLogger(__name__)

# Public source: https://t.me/s/CustomEmojiPack (reviewed 2026-09-15).
# The preview exposes IDs but renders their emoji as a generic square. Do NOT
# invent ID->emoji associations. Resolve alt/free via the shared service first.
# Candidate packs: WhiteXkysluv_by_TgEmodziBot, royal_emoji00996_by_TgEmodziBot,
# Iranemoji_Mehran. Animated variants take precedence over static variants.
CHANNEL_DOCUMENT_IDS = (
    5415647572037500278, 5417849099258987952, 5294103717002371452,
    5260448052623197709, 5199819173286397467, 5440716905302217580,
    5294315493249805406, 5321282377425643440, 5199593567244272818,
    5199496823105930855, 5456294528346512296, 5233435646088989699,
    5202007283030050939, 5199947133247046979, 5454062128900103118,
    5201866919203850475, 5199444557648905765, 5199446627823143100,
    5332655223192185693, 5201919541143159152, 5199508110279983066,
    5334618332484105247, 5199780290947465970, 5346283201667024924,
    5199896444043019019, 5334871757029403436, 5202062138352355049,
    5199631431675953224, 5442927696768181716, 5440786543901978135,
    5334692493684399330, 5199939028643761333, 5199622034287510774,
    5199809780192919852, 5199606933182501116, 5199408870765643027,
    5199664421319755204, 5199926113677100887, 5201659674146916776,
    5202047629952828287, 5335038934336432784, 5310052631379462280,
    5199762256379790227, 5202044498921673067, 5199750299190838577,
    5199516949322680181, 5456214298357426109, 5332636896566732841,
    5199750440924759645, 5456669423156874912, 5308041766346181618,
    5199884950710535129, 5199946854074173775, 5199907898720798436,
    5456120311588083160, 5201769986086946005, 5321369599621490581,
    5334598253511992444, 5199891187003048420, 5334537548444234926,
    5199897715353339070, 5442732434670003082, 5201679409521641719,
    5321407519887747396, 5202146577409394203, 5334910497634413105,
    5309862755170273267, 5199499580474935914, 5273971074982233662,
    5310130172719026033,
    6030878513085552308, 6030627038455403494, 6028310646628554529,
    6028319434131641884, 6028130627369330466, 6028585511650596550,
    6028293496824141193, 6028576067017511683, 6028477385848918510,
    6030843418907776703, 6028358157556782320, 6028579674790041462,
    6030467553434802420, 6028093415772657213, 6030643260546882238,
    6028157801627391042, 6028450031202210426, 6030509257567245697,
    6028145994762293850, 6030800473529783587, 6028603855455917564,
    6028412076576216340, 6028151865982587882, 6028398938271258010,
    6028143589580607996, 6030880239662404337, 6030835572002526073,
    6028444945960932052, 6028431189180683416, 6028070583726510691,
    6028322084126462756, 6028481474657784933, 6028284318479030064,
    6028593251181663251, 6030444738568524718, 6028300059534168991,
    6030444794403099554, 6028316169956497972, 6028535939138065378,
    6028342584005366467, 6028458359143797487, 6030436307547722441,
    6030449278348956975, 6030599507715036314, 6030368331100329091,
    6028146153676084823, 6030326880370955203, 6030599524894905379,
    6028164716524739336, 6028119207051268763, 6028617676660676389,
    6030763910473192721, 6028114018730776058, 6028573623181121496,
    6028138319655736001, 6028374104770352135, 6030666509204854204,
    6028298625015092970, 6030740275268163819, 6028535896188391968,
    6028405488096384603, 6030661681661613132, 6030706529710118027,
    6028193552935162672, 6030427064778102202, 6030438656894834179,
    6030608355347666053, 6028288591971488929, 6028337627613107776,
    6028143641120215925,
    5936064722024536128, 5776096981457836889, 5974184051425155786,
    5969903528104172292, 5836967517129546441, 5836705150462337369,
    5807866102927071266, 5800761466810278569, 5803051929919560068,
    5805523593404095489, 5805624164358298948, 5803373653034802245,
    5816591513672491604, 5801107739958583440, 5798882684906250403,
    5855003643878579964, 5836934557550516347, 5816457635246906894,
    5803131257965518938, 5805229057431840048, 5800638944278224230,
    5803043193956082956, 5800833733930000185, 5800883392341878728,
    5802950555806474012, 5802976317020315445, 5832376463378096000,
    6044130428518931774, 6041629207069465835, 6012808185511943224,
    5974308291944127344, 5974538171478710896, 5830159190806503644,
    5832334909569507238, 6044385566756182879, 5832718544638321058,
    5830420599696008075, 5787197191290362945, 6014729749585206092,
    5868279731387899243, 5767252226456166660, 5766959236672135550,
    5866078869886344315, 5775953830197862261, 5794013867029830691,
    5792104354634800015, 5809838880190372297, 5791804900924989133,
    5816875299341607151, 5812158244134591852, 5794160222335409671,
    6021361114665720596, 6014857993013697989, 5787386517743738669,
    5787496181143707587, 5830425779426562930, 5787197418923629829,
    5940668918375914779, 5962919559093556862, 5764943501145938954,
    5787186733044998296, 5819051039579446609, 5969618539844214631,
    5972335738019126981, 5857136820990517597, 5787468676173143368,
    5807759922745579227, 5810147653979215744, 6003650675286743020,
    5816659996926025458,
)

CATEGORY_EMOJIS = {
    'cool': ('😎', '🗿', '🖤', '🤟', '🤘'),
    'love': ('❤️', '❤', '🥹', '✨', '😍', '🥰', '💕', '💖', '😘', '💔'),
    'funny': ('🤣', '💀', '😂', '😅', '😁', '😆', '😹'),
    'fire': ('🔥', '⚡', '👑', '🎉', '🚀', '💪', '✅', '✔️'),
    'angry': ('😈', '💢', '🔥', '😡', '🤬', '😠'),
    'luxury': ('👑', '💎', '✨', '🏆', '💰', '🌟'),
}
KEYWORDS = {
    'cool': ('خفن', 'گنگ', 'لوتی', 'باحال', 'cool', 'gang'),
    'love': ('عشق', 'عاشق', 'احساسی', 'دوستت دارم', 'دلتنگ', 'love', 'miss you'),
    'funny': ('خنده', 'خندیدم', 'شوخی', 'هههه', 'funny', 'lol', 'haha'),
    'fire': ('موفق', 'موفقیت', 'انجام شد', 'فعال شد', 'تبریک', 'success', 'done'),
    'angry': ('عصبی', 'عصبانی', 'لعنت', 'خشم', 'angry', 'hate'),
    'luxury': ('الماس', 'لوکس', 'سلطان', 'شاه', 'luxury', 'diamond', 'king'),
}
WORD_PATTERNS = {key: re.compile(r'(?<!\w)(?:' + '|'.join(map(re.escape, words))
                               + r')(?!\w)', re.IGNORECASE)
                 for key, words in KEYWORDS.items()}
# Recognition covers complete emoji sequences generally. This inventory is for
# diagnostics/tests and includes common Unicode plus actual runtime literals.
COMMON_EMOJIS = ('ℹ️', '⌛', '⌨️', '⌫', '⏯', '⏰', '⏱', '⏳', '⏸', '⏹', '▫️', '▶️', '☑️', '♻️', '⚔️', '⚙️', '⚠️', '⚡', '⚪️', '⛔', '⛔️', '✅', '✍️', '✏️', '✓', '✔️', '✖️', '✨', '❌', '❤', '❤️', '➕', '➖', '➡️', '⬅️', '⬜️', '⭐', '🆔', '🇮🇷', '🌐', '🌟', '🎁', '🎉', '🎊', '🎙', '🎛', '🎧', '🎨', '🎫', '🎬', '🎮', '🎯', '🎲', '🏅', '🏆', '🏷', '👁', '👋', '👌', '👍', '👎', '👑', '👛', '👤', '👥', '👨', '👩', '👴', '👹', '💀', '💎', '💔', '💕', '💖', '💘', '💙', '💚', '💛', '💜', '💡', '💪', '💫', '💬', '💯', '💰', '💳', '💵', '💸', '💾', '📁', '📂', '📅', '📈', '📉', '📊', '📍', '📛', '📜', '📝', '📞', '📡', '📢', '📣', '📤', '📥', '📦', '📨', '📩', '📭', '📱', '📲', '📸', '🔁', '🔄', '🔇', '🔊', '🔍', '🔎', '🔐', '🔒', '🔔', '🔕', '🔗', '🔢', '🔤', '🔥', '🔴', '🔵', '🔹', '🕊', '🕐', '🕒', '🖤', '🖼', '🗑', '🗿', '😁', '😂', '😅', '😈', '😉', '😊', '😍', '😎', '😏', '😘', '😡', '😭', '🙂', '🙏', '🚀', '🚨', '🚫', '🛍', '🛑', '🛒', '🛠', '🛡', '🟠', '🟡', '🟢', '🤍', '🤔', '🤖', '🤘', '🤝', '🤟', '🤣', '🤬', '🥰', '🥲', '🧑', '🧠', '🧩', '🧪', '🧭', '🧾', '🪙', '🫡', '🫶')
EMOJIS = COMMON_EMOJIS
EMOJI_PATTERN = custom.EMOJI_PATTERN
_whole_emoji = custom.whole_emoji

# Intentionally no invented or visually "approved" mappings. Populate ordered
# lists ONLY after reviewing channel-sourced IDs with the optional live tool.
# Every configured ID is still checked against Telegram's real alt at warm-up.
# Empty entries mean "not visually curated yet", not "no premium exists".
CURATED_PREMIUM_MAP = {emoji: [] for emoji in COMMON_EMOJIS}
CURATED_VARIANT_MAP = {style: {} for style in
                       ('fire', 'dark', 'cute', 'luxury', 'neon', 'gang', 'love')}
PREMIUM_EMOJI_POOL = {name: [] for name in CATEGORY_EMOJIS}
LITERAL_PATTERN = re.compile(r'```[\s\S]*?(?:```|$)|`[^`\n]*`|https?://[^\s<>]+', re.IGNORECASE)
PROTECTED = (MessageEntityCustomEmoji, MessageEntityCode, MessageEntityPre,
             MessageEntityUrl, MessageEntityTextUrl)
BYPASS = ContextVar('premium_emoji_bypass', default=False)
ENTITY_REJECTIONS = tuple(getattr(errors, name) for name in (
    'PremiumAccountRequiredError', 'EmoticonInvalidError',
    'EntityBoundsInvalidError', 'EntitiesTooLongError',
) if hasattr(errors, name))
REJECTED = ENTITY_REJECTIONS + (errors.DocumentInvalidError,)
MAX_CACHE_ENTRIES = 256
MAX_CACHE_BYTES = 256 * 1024


def _sentiment(text):
    scores = {name: len(pattern.findall(text)) for name, pattern in WORD_PATTERNS.items()}
    category = max(scores, key=scores.get)
    return category if scores[category] else None


def _style(value):
    value = str(value or '').lower()
    # The old 'smart' value may already be stored in persistent config. It now
    # aliases exact-smart. Creative replacement is strictly explicit opt-in.
    return value if value in ('creative', 'exact', 'exact-smart', *CURATED_VARIANT_MAP) else 'exact-smart'


def _variant(text, style):
    if style in CURATED_VARIANT_MAP:
        return style
    mood = _sentiment(text)
    return {'cool': 'gang', 'love': 'love', 'funny': 'cute', 'angry': 'dark',
            'fire': 'fire', 'luxury': 'luxury'}.get(mood, 'default')


def diagnostic_selection(emoji, document, source_priority, style):
    """No user text, chat ID or credentials; DEBUG only (caller de-duplicates)."""
    details = dict(unicode=emoji, document_id=document.document_id, alt=document.alt,
                   free=document.free, media_kind=document.media_kind,
                   source_priority=source_priority, style=style)
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug('Premium emoji selection %s', details)
    return details


class EmojiCatalogue:
    """Shared immutable snapshots. Sending never awaits network or this lock."""
    def __init__(self):
        self.records = ()
        self.by_alt = {}
        self.generation = 0
        self.expires_at = 0.0
        self.lock = asyncio.Lock()
        self.task = None
        self.task_client = None
        self.rejected = {}
        self.unresolved = {}
        self.curated_rejected = {}
        self.curated = {}
        self.variants = {}
        self._ranked = {}
        self.attempted = False
        self.source_position = {value: i for i, value in enumerate(CHANNEL_DOCUMENT_IDS)}

    def _publish(self, resolution):
        self.records = tuple(resolution.documents)
        self.rejected = dict(resolution.rejected)
        self.unresolved = dict(resolution.unresolved)
        self.by_alt = {}
        for record in self.records:
            self.by_alt.setdefault(record.alt, []).append(record)
        self.by_alt = {alt: tuple(records) for alt, records in self.by_alt.items()}
        known = {r.document_id: r for r in self.records}
        self.curated_rejected = {}

        def validate(mapping, label):
            result = {}
            for emoji, ids in mapping.items():
                accepted = []
                for doc_id in ids:
                    record = known.get(doc_id)
                    if doc_id not in self.source_position:
                        reason = 'not_in_channel_sources'
                    elif record is None:
                        reason = 'not_validated'
                    elif record.alt != emoji:
                        reason = 'alt_mismatch'
                    else:
                        if doc_id not in accepted: accepted.append(doc_id)
                        continue
                    self.curated_rejected[f'{label}:{emoji}:{doc_id}'] = reason
                if accepted: result[emoji] = tuple(accepted)
            return result
        self.curated = validate(CURATED_PREMIUM_MAP, 'curated')
        self.variants = {style: validate(mapping, style) for style, mapping in CURATED_VARIANT_MAP.items()}
        for name, alts in CATEGORY_EMOJIS.items():
            PREMIUM_EMOJI_POOL[name][:] = [r.document_id for r in self.records if r.alt in alts]
        self.generation += 1
        self._ranked.clear()

    def priority(self, emoji, record, variant):
        variants = self.variants.get(variant, {}).get(emoji, ())
        curated = self.curated.get(emoji, ())
        source = self.source_position.get(record.document_id, len(self.source_position))
        if record.document_id in variants:
            return (0, variants.index(record.document_id), not record.animated, source)
        if record.document_id in curated:
            return (1, curated.index(record.document_id), not record.animated, source)
        # Non-curated fallback uses media type then stable source order. Numeric
        # document IDs NEVER stand for aesthetics. No invented variant labels.
        return (2, not record.animated, source)

    def candidates(self, emoji, premium, variant='default'):
        if emoji not in self.by_alt:
            return ()
        key = (emoji, premium, variant)
        if key not in self._ranked:
            self._ranked[key] = tuple(sorted(
                (r for r in self.by_alt.get(emoji, ()) if premium or r.free),
                key=lambda r: self.priority(emoji, r, variant)))
        return self._ranked[key]

    async def warmup(self, client):
        if time.monotonic() < self.expires_at:
            return self.records
        async with self.lock:
            if time.monotonic() < self.expires_at:
                return self.records
            self.attempted = True
            try:
                resolution = await custom.resolve_custom_emoji_catalogue(client, CHANNEL_DOCUMENT_IDS)
                self._publish(resolution)
                self.expires_at = time.monotonic() + max(
                    300 if resolution.unresolved or not self.records else 86400,
                    resolution.retry_after)
                logger.info('Premium emoji warm-up: valid=%d rejected=%d unresolved=%d Unicode=%d curated=%d',
                            len(self.records), len(self.rejected), len(self.unresolved), len(self.by_alt),
                            sum(map(len, self.curated.values())))
            except Exception as exc:
                self.expires_at = time.monotonic() + max(300, getattr(exc, 'seconds', 0))
                self.unresolved = {i: type(exc).__name__ for i in CHANNEL_DOCUMENT_IDS}
                logger.debug('Premium emoji warm-up unavailable (%s)', type(exc).__name__)
            return self.records

    def start_warmup(self, client):
        if time.monotonic() < self.expires_at or (self.task is not None and not self.task.done()):
            return self.task
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        self.task_client = client
        self.task = loop.create_task(self.warmup(client), name='premium-emoji-warmup')
        self.task.add_done_callback(self._warmup_finished)
        return self.task

    def _warmup_finished(self, task):
        # A process-wide catalogue must not retain a disconnected account's
        # TelegramClient for its 24-hour TTL. Also consume unexpected task errors.
        if task is self.task:
            self.task_client = None
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                logger.debug('Premium emoji background task failed (%s)', type(error).__name__)

    async def close(self, client):
        if self.task_client is client and self.task is not None and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def get(self, client):
        """Compatibility accessor: returns a snapshot immediately."""
        self.start_warmup(client)
        return self.records

    def report(self, *, premium=True, variant='default'):
        return dict(attempted=self.attempted, source_ids=len(CHANNEL_DOCUMENT_IDS),
                    valid_ids=len(self.records) if self.attempted else None,
                    rejected_ids=len(self.rejected) if self.attempted else None,
                    unresolved_ids=len(self.unresolved) if self.attempted else None,
                    rejected=self.rejected, unresolved=self.unresolved,
                    curated_rejected=self.curated_rejected,
                    supported_unicode=len(self.by_alt),
                    mapping={emoji: [r.document_id for r in self.candidates(emoji, premium, variant)]
                             for emoji in sorted(self.by_alt)})


_CATALOGUE = EmojiCatalogue()


class PremiumEmojiInjector:
    def __init__(self, client, *, premium=False, catalogue=None):
        self.client = client
        self.premium = premium
        self.catalogue = catalogue if catalogue is not None else _CATALOGUE
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.cache_generation = -1
        self.disabled_until = 0.0
        self._diagnosed = OrderedDict()

    def start_warmup(self):
        if getattr(config, 'PREMIUM_EMOJI_ENABLED', True):
            return self.catalogue.start_warmup(self.client)

    async def close(self):
        await self.catalogue.close(self.client)
        self.cache.clear()
        self.cache_bytes = 0

    async def inject(self, text, entities=None):
        """Exact alt replacement by default. No metadata await on the send path."""
        original = list(entities or ())
        if (not isinstance(text, str) or not text or BYPASS.get()
                or not getattr(config, 'PREMIUM_EMOJI_ENABLED', True)
                or time.monotonic() < self.disabled_until or len(text) > 16384
                or any(isinstance(e, MessageEntityCustomEmoji) for e in original)
                or not EMOJI_PATTERN.search(text)):
            return text, original
        try:
            self.start_warmup()
            if not self.catalogue.records:
                return text, original
            if self.cache_generation != self.catalogue.generation:
                self.cache.clear()
                self.cache_bytes = 0
                self.cache_generation = self.catalogue.generation
            style = _style(getattr(config, 'PREMIUM_EMOJI_STYLE', 'exact-smart'))
            key = (text, style, self.premium,
                   tuple((type(e).__name__, e.offset, e.length) for e in original))
            if key in self.cache:
                plan, _ = self.cache[key]
                self.cache.move_to_end(key)
            else:
                plan = self._plan(text, original, style)
                # Bound retained text and plans by both bytes and entry count.
                cost = (sys.getsizeof(text) + sys.getsizeof(key) + sys.getsizeof(key[3])
                        + sum(sys.getsizeof(e) for e in key[3]) + sys.getsizeof(plan)
                        + sum(sys.getsizeof(item) for item in plan) + 256)
                if cost <= MAX_CACHE_BYTES:
                    self.cache[key] = (plan, cost)
                    self.cache_bytes += cost
                    while len(self.cache) > MAX_CACHE_ENTRIES or self.cache_bytes > MAX_CACHE_BYTES:
                        _, (_, size) = self.cache.popitem(last=False)
                        self.cache_bytes -= size
            return self._apply(text, original, plan)
        except Exception as exc:
            # Malformed input must not flood production logs or disclose text.
            logger.debug('Premium emoji preparation failed (%s)', type(exc).__name__)
            return text, original

    def _plan(self, text, entities, style):
        offsets = [0]
        for char in text:
            offsets.append(offsets[-1] + (2 if ord(char) > 0xffff else 1))
        boundaries = set(offsets)
        for entity in entities:
            if (entity.offset not in boundaries or entity.length <= 0
                    or entity.offset + entity.length not in boundaries):
                raise ValueError('Invalid UTF-16 entity boundary')
        # utf16_length also rejects lone surrogates rather than sending them.
        custom.utf16_length(text)
        variant = _variant(text, style)
        plan = []
        literals = [m.span() for m in LITERAL_PATTERN.finditer(text)]
        for match in EMOJI_PATTERN.finditer(text):
            if len(plan) >= 100:
                break
            start, end = match.span()
            if (not _whole_emoji(text, start, end)
                    or any(a < end and b > start for a, b in literals)):
                continue
            offset, stop = offsets[start], offsets[end]
            overlaps = [e for e in entities if e.offset < stop and e.offset + e.length > offset]
            if any(isinstance(e, PROTECTED) or not (e.offset <= offset and e.offset + e.length >= stop)
                   for e in overlaps):
                continue
            emoji = match.group()
            choices = self.catalogue.candidates(emoji, self.premium, variant)
            if not choices and style == 'creative':
                groups = [name for name, alts in CATEGORY_EMOJIS.items() if emoji in alts]
                category = _sentiment(text)
                if groups:
                    chosen = category if category in groups else groups[0]
                    for alt in CATEGORY_EMOJIS[chosen]:
                        choices = self.catalogue.candidates(alt, self.premium, variant)
                        if choices: break
            if choices:
                selected = choices[0]
                plan.append((start, end, offset, stop, selected.alt, selected.document_id))
                diagnostic_key = (emoji, selected.document_id, variant)
                if logger.isEnabledFor(logging.DEBUG) and diagnostic_key not in self._diagnosed:
                    diagnostic_selection(emoji, selected,
                        self.catalogue.priority(selected.alt, selected, variant), variant)
                    self._diagnosed[diagnostic_key] = None
                    if len(self._diagnosed) > 256: self._diagnosed.popitem(last=False)
        return tuple(plan)

    @staticmethod
    def _apply(text, entities, plan):
        if not plan:
            return text, entities
        pieces, added = [], []
        last, delta = 0, 0
        for start, end, offset, stop, alt, doc_id in plan:
            pieces.extend((text[last:start], alt))
            payload = custom.create_custom_emoji(alt, doc_id)
            entity = payload.entities[0]
            entity.offset = offset + delta
            added.append(entity)
            delta += custom.utf16_length(alt) - (stop - offset)
            last = end
        pieces.append(text[last:])
        adjusted = []
        def shift(boundary):
            return boundary + sum(custom.utf16_length(alt) - (stop - offset)
                                  for _, _, offset, stop, alt, _ in plan if stop <= boundary)
        for entity in entities:
            clone = copy.copy(entity)
            clone.offset = shift(entity.offset)
            clone.length = shift(entity.offset + entity.length) - clone.offset
            adjusted.append(clone)
        return ''.join(pieces), sorted(adjusted + added, key=lambda e: (e.offset, -e.length))


def install_premium_emoji_injector(client, *, premium=False):
    """Wrap only this self instance's public send/edit methods, once.

    Event.reply/respond and Message.edit delegate to these methods in Telethon
    1.44. Explicit formatting_entities and media albums are handled as well.
    No outgoing event handler: manually sent account messages are not scanned.
    """
    installed = getattr(client, '_premium_emoji_injector', None)
    if isinstance(installed, PremiumEmojiInjector):
        installed.premium = premium
        return installed
    engine = PremiumEmojiInjector(client, premium=premium)

    def wrap(original, field):
        signature = inspect.signature(original)

        @wraps(original)
        async def wrapped(*args, **kwargs):
            # 💤 AWAY_BYPASS_PREMIUM: پاسخ عدم حضور هرگز وارد سیستم ایموجی
            # ویژه نمی‌شود — بدون تبدیل، بدون Resend، بدون Delete/New Send.
            if (BYPASS.get() or away_bypass.active()
                    or not getattr(config, 'PREMIUM_EMOJI_ENABLED', True)):
                return await original(*args, **kwargs)
            changed = False
            try:
                bound = signature.bind(*args, **kwargs)
                value = bound.arguments.get(field, '')
                supplied = bound.arguments.get('formatting_entities')
                mode = bound.arguments.get('parse_mode', ())

                async def prepare(value, formatting):
                    if not isinstance(value, str) or not EMOJI_PATTERN.search(value):
                        return value, formatting, False
                    if formatting is None:
                        parsed, existing = await client._parse_message_text(value, mode)
                    else:
                        parsed, existing = value, formatting
                    updated, result = await engine.inject(parsed, existing)
                    old_count = sum(isinstance(e, MessageEntityCustomEmoji) for e in existing or [])
                    new_count = sum(isinstance(e, MessageEntityCustomEmoji) for e in result or [])
                    return updated, result, new_count > old_count

                if isinstance(value, Message):
                    new_text, new_entities, changed = await prepare(value.message, value.entities or [])
                    if changed:
                        value = copy.copy(value)
                        value.message, value.entities = new_text, new_entities
                        bound.arguments[field] = value
                elif field == 'caption' and isinstance(value, (list, tuple)):
                    prepared = []
                    for i, caption in enumerate(value):
                        fmt = supplied[i] if supplied is not None and i < len(supplied) else None
                        prepared.append(await prepare(caption, fmt))
                    changed = any(item[2] for item in prepared)
                    if changed:
                        # Once some captions are parsed, supply explicit entities
                        # for all captions so Telethon cannot parse one twice.
                        texts, all_entities = [], []
                        for i, (caption, fmt, did_change) in enumerate(prepared):
                            if not did_change and fmt is None:
                                caption, fmt = await client._parse_message_text(caption or '', mode)
                            texts.append(caption)
                            all_entities.append(fmt or [])
                        bound.arguments[field] = texts
                        bound.arguments['formatting_entities'] = all_entities
                        bound.arguments['parse_mode'] = None
                else:
                    new_text, new_entities, changed = await prepare(value, supplied)
                    if changed:
                        bound.arguments[field] = new_text
                        bound.arguments['formatting_entities'] = new_entities
                        bound.arguments['parse_mode'] = None
            except Exception as exc:
                logger.debug('Premium emoji send preparation failed (%s); preserving arguments',
                               type(exc).__name__)
                changed = False
            if not changed:
                return await original(*args, **kwargs)
            # Block nested send_message -> send_file calls from reinjecting and
            # bypass the engine during a confirmed emoji rejection fallback.
            token = BYPASS.set(True)
            try:
                try:
                    return await original(*bound.args, **bound.kwargs)
                except REJECTED as exc:
                    # A multi-file send can have committed earlier album chunks.
                    # Replaying the entire upload could duplicate those messages.
                    media = bound.arguments.get('file', kwargs.get('file'))
                    message_value = bound.arguments.get('message')
                    has_media = (field == 'caption' or media is not None
                                 or (isinstance(message_value, Message)
                                     and message_value.media is not None))
                    if utils.is_list_like(media) or (has_media and isinstance(exc, errors.DocumentInvalidError)):
                        raise
                    engine.disabled_until = time.monotonic() + 300
                    logger.warning('Telegram rejected injected emoji; retrying original message once')
                    return await original(*args, **kwargs)
            finally:
                BYPASS.reset(token)
        return wrapped

    for method, field in (('send_message', 'message'), ('send_file', 'caption'), ('edit_message', 'text')):
        original = getattr(client, method, None)
        # Lightweight adapters/test doubles may expose only some operations.
        if callable(original) and field in inspect.signature(original).parameters:
            setattr(client, method, wrap(original, field))
    client._premium_emoji_injector = engine
    # Installer is called after authentication, before registering self handlers.
    # Queue warm-up and continue startup immediately; Unicode is used until ready.
    if callable(client):
        engine.start_warmup()
    return engine


async def close_premium_emoji_injector(client):
    engine = getattr(client, '_premium_emoji_injector', None)
    if isinstance(engine, PremiumEmojiInjector):
        await engine.close()
