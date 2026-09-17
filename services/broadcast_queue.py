from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable

try:
    from telethon.errors import (
        UserIsBlockedError,
        UserDeactivatedError,
        ChatWriteForbiddenError,
        UserBannedInChannelError,
    )
except Exception:  # pragma: no cover
    UserIsBlockedError = UserDeactivatedError = ChatWriteForbiddenError = UserBannedInChannelError = ()


BLOCK_ERRORS = tuple(
    e for e in (
        UserIsBlockedError,
        UserDeactivatedError,
        ChatWriteForbiddenError,
        UserBannedInChannelError,
    ) if isinstance(e, type)
)


@dataclass
class BroadcastReport:
    success: int = 0
    failed: int = 0
    blocked: int = 0


@dataclass
class BroadcastJob:
    targets: Iterable[int]
    sender: Callable[[int], Awaitable[Any]]
    batch_size: int = 20
    delay: float = 1.0
    retries: int = 2
    max_concurrency: int = 1
    future: asyncio.Future | None = field(default=None, repr=False)


class BroadcastQueue:
    """Real async broadcast queue. add() only registers jobs; worker sends them."""

    def __init__(self):
        self.queue: asyncio.Queue[BroadcastJob] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None

    def _ensure_worker(self):
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker())

    async def add(self, job: BroadcastJob) -> asyncio.Future:
        loop = asyncio.get_running_loop()
        job.future = loop.create_future()
        await self.queue.put(job)
        self._ensure_worker()
        return job.future

    async def _worker(self):
        while True:
            job = await self.queue.get()
            try:
                report = await self._process(job)
                if job.future and not job.future.done():
                    job.future.set_result(report)
            except Exception as exc:
                if job.future and not job.future.done():
                    job.future.set_exception(exc)
            finally:
                self.queue.task_done()

    async def _process(self, job: BroadcastJob) -> BroadcastReport:
        report = BroadcastReport()
        semaphore = asyncio.Semaphore(max(1, job.max_concurrency))

        async def limited_send(uid: int):
            async with semaphore:
                await self._send_one(job, uid, report)

        batch = []
        for uid in job.targets:
            batch.append(uid)
            if len(batch) >= job.batch_size:
                await asyncio.gather(*(limited_send(item) for item in batch))
                batch = []
                if job.delay:
                    await asyncio.sleep(job.delay)

        if batch:
            await asyncio.gather(*(limited_send(item) for item in batch))
            if job.delay:
                await asyncio.sleep(job.delay)
        return report

    async def _send_one(self, job: BroadcastJob, uid: int, report: BroadcastReport):
        for attempt in range(job.retries + 1):
            try:
                await job.sender(uid)
                report.success += 1
                return
            except BLOCK_ERRORS:
                report.blocked += 1
                return
            except Exception:
                if attempt >= job.retries:
                    report.failed += 1
                    return
                await asyncio.sleep(0.5 * (attempt + 1))


broadcast_queue = BroadcastQueue()
