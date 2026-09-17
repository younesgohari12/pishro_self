import pytest

from services.broadcast_queue import BroadcastJob, BroadcastQueue


@pytest.mark.asyncio
async def test_broadcast_queue_worker_success_and_retry_failure():
    queue = BroadcastQueue()
    calls = {}

    async def sender(uid):
        calls[uid] = calls.get(uid, 0) + 1
        if uid == 2:
            raise RuntimeError('real failure')
        if uid == 3 and calls[uid] < 2:
            raise RuntimeError('temporary')

    future = await queue.add(BroadcastJob(
        targets=[1, 2, 3],
        sender=sender,
        batch_size=2,
        delay=0,
        retries=1,
    ))

    report = await future

    assert report.success == 2
    assert report.failed == 1
    assert report.blocked == 0
    assert calls[3] == 2


@pytest.mark.asyncio
async def test_broadcast_queue_telegram_block_error():
    queue = BroadcastQueue()

    class UserIsBlockedError(Exception):
        pass

    # Inject a Telegram-like blocked exception type for the sender result.
    import services.broadcast_queue as module
    module.BLOCK_ERRORS = (UserIsBlockedError,)

    async def sender(uid):
        raise UserIsBlockedError()

    report = await (await queue.add(BroadcastJob(
        targets=[10],
        sender=sender,
        retries=1,
        delay=0,
    )))

    assert report.success == 0
    assert report.failed == 0
    assert report.blocked == 1


@pytest.mark.asyncio
async def test_broadcast_queue_respects_rate_limit_concurrency():
    queue = BroadcastQueue()
    active = 0
    maximum = 0

    async def sender(uid):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1

    import asyncio

    report = await (await queue.add(BroadcastJob(
        targets=list(range(10)),
        sender=sender,
        batch_size=10,
        max_concurrency=2,
        delay=0,
    )))

    assert report.success == 10
    assert maximum <= 2
