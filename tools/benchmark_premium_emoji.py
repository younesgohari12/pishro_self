"""Offline CPU/cache benchmark. Synthetic alt fixtures do not approve visuals."""
import asyncio
import gc
import json
from pathlib import Path
import statistics
import sys
import time
import tracemalloc
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from services import custom_emoji_service as custom
from services import premium_emoji_injector as mod


async def main():
    cat = mod.EmojiCatalogue()
    docs = tuple(custom.CustomEmojiDocument(doc_id, emoji, True, True, 'video/webm')
                 for doc_id, emoji in zip(mod.CHANNEL_DOCUMENT_IDS, mod.COMMON_EMOJIS))
    cat._publish(custom.EmojiResolution(docs, {}, {}))
    cat.expires_at = float('inf')
    engine = mod.PremiumEmojiInjector(None, premium=True, catalogue=cat)
    text = 'موفقیت در انجام عملیات؛ ممنون از همراهی شما 👍 🔥 ❤️ 🎉'
    await engine.inject(text)
    timings = []
    for _ in range(2000):
        start = time.perf_counter_ns()
        await engine.inject(text)
        timings.append((time.perf_counter_ns() - start) / 1000)
    cold = []
    for i in range(500):
        start = time.perf_counter_ns()
        await engine.inject(str(i) + text)
        cold.append((time.perf_counter_ns() - start) / 1000)
    gc.collect()
    tracemalloc.start()
    baseline = tracemalloc.get_traced_memory()[0]
    fresh = mod.PremiumEmojiInjector(None, premium=True, catalogue=cat)
    for i in range(1000):
        await fresh.inject(str(i) + 'x' * 3000 + ' 👍🔥❤️🎉')
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    output = dict(python=sys.version.split()[0], fixture_documents=len(docs),
        fixture_note='Synthetic alt bindings: logic benchmark only; no live visual validation',
        iterations_hot=2000, hot_median_us=round(statistics.median(timings), 2),
        hot_p95_us=round(sorted(timings)[int(len(timings)*0.95)], 2),
        cold_median_us=round(statistics.median(cold), 2),
        cold_p95_us=round(sorted(cold)[int(len(cold)*0.95)], 2),
        long_message_cache_entries=len(fresh.cache),
        accounted_cache_bytes=fresh.cache_bytes,
        retained_tracemalloc_bytes=current-baseline, peak_tracemalloc_bytes=peak-baseline,
        entry_cap=mod.MAX_CACHE_ENTRIES, accounted_byte_cap=mod.MAX_CACHE_BYTES,
        note='One account, this machine; network excluded. Byte cap is an accounting bound, not total process RSS.')
    print(json.dumps(output, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
