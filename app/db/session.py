"""Асинхронные сессии. Рабочая SQLite по умолчанию открывается только для чтения."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.db.models import Base


class Database:
    def __init__(self, settings: Settings, *, read_only: bool = True, testing: bool = False) -> None:
        url = make_url(settings.database_url)
        self.testing = testing
        self.read_only = read_only
        if url.drivername == "postgresql+asyncpg":
            if testing:
                raise ValueError("Тестовая схема создаётся только в SQLite в памяти.")
            options = {"server_settings": {"default_transaction_read_only": "on"}} if read_only else {}
            self.engine = create_async_engine(url, connect_args=options)
            self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
            return
        if url.drivername != "sqlite+aiosqlite":
            raise ValueError("Нужен драйвер sqlite+aiosqlite или postgresql+asyncpg.")
        memory = url.database in (None, "", ":memory:")
        if testing and not memory:
            raise ValueError("Тестовый bootstrap разрешён только для SQLite в памяти.")
        if memory and not testing:
            raise ValueError("Рабочее приложение не создаёт вторую базу в памяти.")
        if not memory:
            path = Path(url.database).expanduser()
            if not path.is_absolute():
                path = Path(__file__).resolve().parents[2] / path
            path = path.resolve()
            if not path.is_file():
                raise FileNotFoundError("Существующая SQLite не найдена; новая рабочая база не создаётся.")
            url = url.set(
                database="file:" + path.as_posix(), query={"mode": "ro" if read_only else "rw", "uri": "true"}
            )
        self.engine = create_async_engine(url, connect_args={"timeout": settings.sqlite_timeout_seconds})
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        @event.listens_for(self.engine.sync_engine, "connect")
        def foreign_keys(connection: Any, record: Any) -> None:
            connection.isolation_level = None
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        @event.listens_for(self.engine.sync_engine, "begin")
        def explicit_begin(connection: Connection) -> None:
            connection.exec_driver_sql("BEGIN")

    async def create_test_schema(self) -> None:
        if not self.testing or self.read_only:
            raise PermissionError("create_all разрешён только в записываемой тестовой базе в памяти.")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session:
            try:
                yield session
                if not self.read_only:
                    await session.commit()
            except BaseException:
                await session.rollback()
                raise

    async def close(self) -> None:
        await self.engine.dispose()
