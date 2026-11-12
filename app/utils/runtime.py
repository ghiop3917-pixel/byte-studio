"""Независимый супервизор задач в одном asyncio event loop."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping

from app.bot import texts
from app.config import Settings

ServiceFactory = Callable[[], Awaitable[None]]


async def supervise(name: str, factory: ServiceFactory, settings: Settings, stop: asyncio.Event) -> None:
    for attempt in range(settings.service_max_restarts):
        if stop.is_set():
            return
        try:
            await factory()
            if stop.is_set():
                return
            raise RuntimeError(texts.SERVICE_EXITED)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logging.error(texts.SERVICE_FAILED, name, type(error).__name__, attempt + 1)
            if attempt + 1 >= settings.service_max_restarts:
                logging.error(texts.SERVICE_EXHAUSTED, name)
                return
            delay = min(settings.service_retry_initial_seconds * 2**attempt, settings.service_retry_max_seconds)
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
                return
            except asyncio.TimeoutError:
                continue


async def run_services(services: Mapping[str, ServiceFactory], settings: Settings, stop: asyncio.Event) -> None:
    if not services:
        raise ValueError(texts.EMPTY_SERVICES)
    tasks = [
        asyncio.create_task(supervise(name, factory, settings, stop), name=name) for name, factory in services.items()
    ]
    watcher = asyncio.create_task(stop.wait())
    try:
        pending = set(tasks)
        while pending and not stop.is_set():
            done, _ = await asyncio.wait(pending | {watcher}, return_when=asyncio.FIRST_COMPLETED)
            pending.difference_update(done)
    finally:
        for task in [*tasks, watcher]:
            task.cancel()
        await asyncio.gather(*tasks, watcher, return_exceptions=True)
