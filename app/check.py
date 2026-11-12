"""Диагностика: офлайн по умолчанию; ни одна проверка не изменяет схему."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import sqlite3
import sys
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.bot import texts

if TYPE_CHECKING:
    from app.config import Settings


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str


def inspect_sqlite(path: Path) -> bool:
    if not path.is_file():
        raise FileNotFoundError()
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("SELECT 1").fetchone()
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='lh_users'").fetchone() is not None


async def run_checks(settings: Settings | None = None, *, online: bool = False) -> list[CheckResult]:
    result = [
        CheckResult(
            "python",
            "OK" if sys.version_info >= (3, 11) else "FAIL",
            texts.PYTHON_OK if sys.version_info >= (3, 11) else texts.PYTHON_FAILED,
        )
    ]
    packages = ("aiogram", "Telethon", "SQLAlchemy", "aiosqlite", "pydantic-settings", "httpx")
    missing = []
    for name in packages:
        try:
            importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            missing.append(name)
    result.append(
        CheckResult(
            "dependencies",
            "FAIL" if missing else "OK",
            texts.DEPENDENCIES_MISSING % ", ".join(missing) if missing else texts.DEPENDENCIES_OK,
        )
    )
    try:
        from app.config import PROJECT_ROOT, get_settings

        settings = settings or await asyncio.to_thread(get_settings)
    except Exception as error:
        result.append(CheckResult("config", "FAIL", type(error).__name__))
        return result
    result.append(CheckResult("config", "OK", texts.CONFIG_OK))
    try:
        from sqlalchemy.engine import make_url

        url = make_url(settings.database_url)
        if url.drivername == "postgresql+asyncpg":
            if not online or not settings.check_network:
                raise PermissionError(texts.NETWORK_SKIPPED)
            from sqlalchemy import text

            from app.db.session import Database

            database = Database(settings)
            try:
                async with database.engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
                    query = text(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_schema=current_schema() AND table_name='lh_users'"
                    )
                    present = (await connection.execute(query)).first() is not None
            finally:
                await database.close()
        else:
            if url.drivername != "sqlite+aiosqlite" or not url.database or url.database == ":memory:":
                raise ValueError()
            path = Path(url.database).expanduser()
            if not path.is_absolute():
                path = PROJECT_ROOT / path
            present = await asyncio.to_thread(inspect_sqlite, path)
        result.append(CheckResult("database", "OK", texts.DATABASE_OK))
        result.append(
            CheckResult(
                "schema", "OK" if present else "SKIP", texts.SCHEMA_PRESENT if present else texts.SCHEMA_PROTECTED
            )
        )
    except Exception as error:
        result.append(CheckResult("database", "FAIL", type(error).__name__))
    session_path = settings.telegram_session_paths[0]
    result.append(
        CheckResult(
            "session_file",
            "OK" if session_path.is_file() else "FAIL",
            texts.SESSION_OK if session_path.is_file() else texts.SESSION_MISSING,
        )
    )
    if not online or not settings.check_network:
        for name in ("bot", "userbot", "llm"):
            result.append(CheckResult(name, "SKIP", texts.NETWORK_SKIPPED))
        return result
    try:
        if not settings.bot_token.get_secret_value():
            raise ValueError(texts.BOT_MISSING)
        from aiogram import Bot

        bot = Bot(settings.bot_token.get_secret_value())
        try:
            await asyncio.wait_for(bot.get_me(), settings.network_timeout_seconds)
        finally:
            await bot.session.close()
        result.append(CheckResult("bot", "OK", texts.BOT_OK))
    except Exception as error:
        result.append(CheckResult("bot", "FAIL", type(error).__name__))
    try:
        if (
            not settings.telegram_api_id
            or not settings.telegram_api_hash.get_secret_value()
            or not session_path.is_file()
        ):
            raise ValueError(texts.TELEGRAM_MISSING)
        from telethon import TelegramClient

        from app.utils.session import load_session

        session = await asyncio.to_thread(load_session, session_path)
        client = TelegramClient(
            session,
            settings.telegram_api_id,
            settings.telegram_api_hash.get_secret_value(),
            flood_sleep_threshold=0,
            request_retries=0,
            connection_retries=0,
            timeout=settings.network_timeout_seconds,
        )
        try:
            await asyncio.wait_for(client.connect(), settings.network_timeout_seconds)
            if not await client.is_user_authorized():
                raise PermissionError()
        finally:
            await client.disconnect()
        result.append(CheckResult("userbot", "OK", texts.TELEGRAM_AUTH))
    except Exception as error:
        result.append(CheckResult("userbot", "FAIL", type(error).__name__))
    if not settings.llm_enabled:
        result.append(CheckResult("llm", "SKIP", texts.LLM_SKIPPED))
        return result
    try:
        if not settings.llm_model:
            raise ValueError(texts.LLM_MISSING)
        import httpx

        headers = (
            {"Authorization": "Bearer " + settings.llm_api_key.get_secret_value()}
            if settings.llm_api_key.get_secret_value()
            else {}
        )
        async with httpx.AsyncClient(timeout=settings.network_timeout_seconds) as client:
            response = await client.post(
                settings.llm_base_url.rstrip("/") + "/chat/completions",
                headers=headers,
                json={
                    "model": settings.llm_model,
                    "messages": [{"role": "user", "content": texts.LLM_PROBE}],
                    "max_tokens": settings.llm_health_max_tokens,
                },
            )
            response.raise_for_status()
        result.append(CheckResult("llm", "OK", texts.LLM_OK))
    except Exception as error:
        result.append(CheckResult("llm", "FAIL", type(error).__name__))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверка окружения единого бота без изменения данных")
    parser.add_argument("--online", action="store_true", help="Сетевые проверки только при CHECK_NETWORK=true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    results = asyncio.run(run_checks(online=args.online))
    if args.json:
        import json

        print(json.dumps([asdict(item) for item in results], ensure_ascii=False))
    else:
        for item in results:
            print(f"{item.status} {item.name}: {item.detail}")
    return int(any(item.status == "FAIL" for item in results))


if __name__ == "__main__":
    raise SystemExit(main())
