"""Явный запуск интерфейса. Схема проверяется read-only до любых записей."""

import asyncio
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from aiogram import Bot, Dispatcher
from sqlalchemy import inspect, text

from app.bot import texts
from app.bot.router import create_router
from app.config import PROJECT_ROOT, Settings
from app.db.models import Base
from app.db.session import Database
from app.utils.runtime import run_services


@contextmanager
def instance_lock(path: Path = PROJECT_ROOT / ".bot-ui.lock") -> Iterator[None]:
    stream = path.open("a+b")
    locked = False
    try:
        if stream.seek(0, 2) == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError:
            raise RuntimeError(texts.INTERFACE_LOCKED) from None
        yield
    finally:
        if locked:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


async def verify_schema(database: Database) -> bool:
    async with database.engine.connect() as connection:

        def compatible(sync):
            schema = inspect(sync)
            names = set(schema.get_table_names())
            if not set(Base.metadata.tables) <= names:
                return names, False
            for name, table in Base.metadata.tables.items():
                columns = {column["name"] for column in schema.get_columns(name)}
                if not set(table.columns.keys()) <= columns:
                    return names, False
            return names, True

        names, ready = await connection.run_sync(compatible)
        if not ready:
            return False
        if "core_locks" in names:
            query = text("SELECT 1 FROM core_locks WHERE name IN ('monitor','source_growth') AND lease_until>:now")
            if (await connection.execute(query, {"now": time.time()})).first():
                raise RuntimeError(texts.LEGACY_RUNNING)
        return True


async def _run(settings: Settings, with_reader: bool) -> None:
    if not settings.bot_token.get_secret_value():
        raise ValueError(texts.BOT_MISSING)
    inspection = Database(settings)
    try:
        if not await verify_schema(inspection):
            raise RuntimeError(texts.SCHEMA_NOT_READY)
    finally:
        await inspection.close()
    with instance_lock():
        database = Database(settings, read_only=False)
        from app.pipeline.ingestion import Ingestion
        from app.userbot.client import ReaderHub, run_reader

        hub = ReaderHub() if with_reader else None

        async def polling() -> None:
            bot = Bot(settings.bot_token.get_secret_value())
            dispatcher = Dispatcher(reader_hub=hub)
            dispatcher.include_router(create_router(database, settings))
            try:
                await dispatcher.start_polling(bot, handle_signals=False, close_bot_session=False)
            finally:
                await dispatcher.storage.close()
                await bot.session.close()

        try:
            services = {"bot": polling}
            if with_reader:
                services["reader"] = lambda: run_reader(database, settings, hub=hub)
                services["ingestion"] = Ingestion(database, settings).processing_loop
            await run_services(services, settings, asyncio.Event())
            raise RuntimeError(texts.INTERFACE_STOPPED)
        finally:
            await database.close()


async def run_interface(settings: Settings) -> None:
    await _run(settings, False)


async def run_application(settings: Settings) -> None:
    await _run(settings, True)
