import argparse
import asyncio
import json
import logging
import os
import re
import sqlite3
from contextlib import closing
from getpass import getpass
from pathlib import Path

from telethon import TelegramClient, errors, types
from telethon.crypto import AuthKey
from telethon.sessions import MemorySession

from bot import Telegram, TelegramError
from bot_menu import MONITOR_DB, run_menu, saved_directions
from session_config import secure_session, session_path

SOURCES = (
    "predprinimateli_msk",
    "bizness_moskva",
    "businesss_msk1",
    "bizneschat_spb",
    "predprinimateli_rus",
    "bmsk_ed",
    "predprinimateli_kzn",
)
INACTIVE_SOURCES = {"predprinimateli_77", "tclubnetwork"}
EXTRA_SOURCES = Path(__file__).with_name("business-sources.json")
RECIPIENT_FILE = Path(__file__).with_name("recipient.json")


def recipient_id(default):
    if not RECIPIENT_FILE.exists():
        return default
    value = json.loads(RECIPIENT_FILE.read_text(encoding="utf-8"))
    if type(value) is not int or value <= 0:
        raise ValueError("Некорректный ID получателя уведомлений.")
    return value


def source_names():
    extra = json.loads(EXTRA_SOURCES.read_text(encoding="utf-8")) if EXTRA_SOURCES.exists() else []
    return tuple(name for name in dict.fromkeys((*SOURCES, *extra)) if name not in INACTIVE_SOURCES)


async def resolve_group(client, username):
    group = await client.get_entity("@" + username)
    if not isinstance(group, types.Channel) or not group.megagroup or group.broadcast or group.left:
        raise ValueError(f"@{username} не является доступной группой аккаунта.")
    return group


def open_session(path, *, explicit=None):
    path = secure_session(path, explicit=explicit)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        row = db.execute("SELECT dc_id, server_address, port, auth_key FROM sessions").fetchone()
    if not row or not row[3]:
        raise SystemExit("Сначала подключи аккаунт через connect.cmd.")
    session = MemorySession()
    session.set_dc(*row[:3])
    session.auth_key = AuthKey(row[3])
    return session


async def monitor(args):
    api_id = os.environ.pop("TELEGRAM_LEADS_API_ID", "") or input("API ID: ").strip()
    api_hash = os.environ.pop("TELEGRAM_LEADS_API_HASH", "") or getpass("API hash: ").strip()
    if not api_id.isdecimal() or not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
        raise SystemExit("Проверь API ID и API hash.")
    session_file = Path(args.session).expanduser().resolve() if args.session else session_path()
    client = TelegramClient(
        open_session(session_file, explicit=True if args.session else None),
        int(api_id),
        api_hash,
        flood_sleep_threshold=0,
        connection_retries=3,
        request_retries=2,
        timeout=10,
    )
    try:
        await asyncio.wait_for(client.connect(), timeout=30)
        if not await client.is_user_authorized():
            raise SystemExit("Сессия Telegram требует повторного входа.")
        owner = await client.get_me()
        if args.check:
            for name in source_names():
                try:
                    group = await resolve_group(client, name)
                    messages = await client.get_messages(group, limit=100)
                    print(f"@{name}: прочитано {len(messages)}.")
                except (errors.RPCError, OSError, ValueError) as error:
                    print(f"@{name}: {type(error).__name__}.")
            return
        token = os.environ.pop("TELEGRAM_BOT_TOKEN", "") or getpass("Токен бота: ").strip()
        if not re.fullmatch(r"\d+:[\w-]+", token, flags=re.ASCII):
            raise SystemExit("Проверь токен бота.")
        telegram = Telegram(token)
        from reliability import migrate, open_database
        from telegram_runtime import run

        with closing(open_database(MONITOR_DB)) as db:
            migrate(db)
            await run(
                client,
                db,
                telegram,
                source_names,
                resolve_group,
                lambda: recipient_id(owner.id),
                saved_directions,
                run_menu,
            )
    finally:
        await client.disconnect()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Мониторинг заявок в группах Telegram")
    parser.add_argument("--check", action="store_true", help="Проверить чтение без отправки сообщений")
    parser.add_argument("--session")
    try:
        asyncio.run(monitor(parser.parse_args()))
    except TelegramError as error:
        raise SystemExit(f"Ошибка бота Telegram ({error.code}). Проверь токен и отправь боту /start.") from None
    except (errors.RPCError, OSError, sqlite3.Error, ValueError, RuntimeError) as error:
        raise SystemExit(f"Мониторинг остановлен ({type(error).__name__}).") from None
    except KeyboardInterrupt:
        print("Мониторинг остановлен.")
