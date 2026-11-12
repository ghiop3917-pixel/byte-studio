"""Первый вход только для отсутствующей сессии. Существующая не изменяется."""

import argparse
import asyncio
import re
from getpass import getpass
from pathlib import Path

from telethon import TelegramClient

from app.config import get_settings
from session_config import secure_session


async def login(path: Path, api_id: int, api_hash: str) -> bool:
    path = path.expanduser().resolve()
    if path.exists():
        print("Сессия уже существует. Авторизация и файл не изменены.")
        return False
    if api_id <= 0 or not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
        raise ValueError("Проверьте API ID и API hash.")
    path = secure_session(path, explicit=True)
    client = TelegramClient(str(path), api_id, api_hash)
    secure_session(path, explicit=True)
    try:
        await client.start(
            phone=lambda: getpass("Номер Telegram: ").strip(),
            code_callback=lambda: getpass("Код Telegram: ").strip(),
            password=lambda: getpass("Пароль двухэтапной защиты: "),
            max_attempts=1,
        )
        print("Первый вход завершён. Закройте login.py перед запуском приложения.")
        return True
    finally:
        await client.disconnect()
        secure_session(path, explicit=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Первый вход, без перезаписи существующей сессии")
    parser.add_argument("--session")
    args = parser.parse_args(argv)
    settings = get_settings()
    path = Path(args.session) if args.session else settings.telegram_session_paths[0]
    if path.exists():
        print("Сессия уже существует. Повторный вход не выполняется.")
        return 0
    try:
        api_id = settings.telegram_api_id or int(input("API ID: ").strip())
        api_hash = settings.telegram_api_hash.get_secret_value() or getpass("API hash: ").strip()
        asyncio.run(login(path, api_id, api_hash))
        return 0
    except (ValueError, OSError) as error:
        print("Вход не завершён: " + type(error).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
