"""
Task Queue — async in-memory queue with optional Redis backend.

Supports:
  • Parallel task dispatch
  • Semaphore-bounded concurrency
  • Per-task retry tracking
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Deque, Dict, List, Optional

import structlog

from app.config import QueueBackend, settings

log = structlog.get_logger(__name__)


@dataclass
class Task:
    id: str
    coro_fn: Callable[..., Coroutine]
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)
    retries: int = 0
    max_retries: int = 2


class InMemoryQueue:
    def __init__(self, concurrency: int = 10) -> None:
        self._sem = asyncio.Semaphore(concurrency)
        self._tasks: Deque[Task] = deque()
        self._results: Dict[str, Any] = {}

    def enqueue(self, task: Task) -> None:
        self._tasks.append(task)

    async def run_all(self) -> Dict[str, Any]:
        workers = []
        while self._tasks:
            task = self._tasks.popleft()
            workers.append(self._run(task))
        results = await asyncio.gather(*workers, return_exceptions=True)
        for t, r in zip(list(self._tasks) + [], results):
            if isinstance(r, Exception):
                log.error("task_error", task_id=getattr(t, "id", "?"), error=str(r))
        return self._results

    async def _run(self, task: Task) -> Any:
        async with self._sem:
            try:
                result = await task.coro_fn(*task.args, **task.kwargs)
                self._results[task.id] = result
                return result
            except Exception as exc:
                if task.retries < task.max_retries:
                    task.retries += 1
                    log.warning("task_retry", task_id=task.id, attempt=task.retries, error=str(exc))
                    await asyncio.sleep(settings.retry_delay * task.retries)
                    self._tasks.appendleft(task)
                else:
                    log.error("task_failed", task_id=task.id, error=str(exc))
                    self._results[task.id] = None


async def run_tasks_concurrent(
    coros: List[Coroutine],
    concurrency: int = 10,
) -> List[Any]:
    """Run a list of coroutines with bounded concurrency."""
    sem = asyncio.Semaphore(concurrency)

    async def _guarded(coro):
        async with sem:
            try:
                return await coro
            except Exception as exc:
                log.warning("concurrent_task_error", error=str(exc))
                return None

    return await asyncio.gather(*[_guarded(c) for c in coros])
