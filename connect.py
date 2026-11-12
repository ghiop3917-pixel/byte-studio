import asyncio
import os
import re
from getpass import getpass

from telethon import TelegramClient
from telethon.errors import RPCError

from session_config import secure_session, session_path


async def connect():
    api_id = os.environ.pop("TELEGRAM_LEADS_API_ID", "") or input("Введи api_id приложения Telegram: ").strip()
    api_hash = (
        os.environ.pop("TELEGRAM_LEADS_API_HASH", "")
        or getpass("Введи api_hash приложения Telegram (ввод скрыт): ").strip()
    )
    if not api_id.isdecimal() or not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
        raise SystemExit("Проверь формат api_id и api_hash.")

    path = secure_session(session_path())
    client = TelegramClient(str(path), int(api_id), api_hash)
    secure_session(path)
    try:
        await client.start(
            phone=lambda: input("Номер аккаунта с кодом страны: ").strip(),
            code_callback=lambda: getpass("Код входа от Telegram (ввод скрыт): ").strip(),
            password=lambda: getpass("Пароль двухэтапной защиты Telegram: "),
            max_attempts=1,
        )
        account = await client.get_me()
        print(f"Аккаунт подключён. ID: {account.id}.")
    except (RPCError, OSError, ValueError) as error:
        raise SystemExit(f"Не удалось подключиться ({type(error).__name__}).") from None
    finally:
        await client.disconnect()
        secure_session(path)


if __name__ == "__main__":
    asyncio.run(connect())
