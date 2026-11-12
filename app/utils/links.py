"""Ссылки Telegram без обращения к API и без небезопасного tg://user."""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

USERNAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{3,31}")
INVITE = re.compile(r"[A-Za-z0-9_-]+")


@dataclass(frozen=True)
class ChatReference:
    username: str | None = None
    invite_hash: str | None = None


def parse_chat_reference(value: str) -> ChatReference:
    value = value.strip()
    if value.startswith("@"):
        username = value[1:]
    elif "t.me/" in value or "telegram.me/" in value:
        parsed = urlsplit(value if "://" in value else "https://" + value)
        if parsed.hostname not in {"t.me", "telegram.me"} or parsed.scheme not in {"http", "https"}:
            raise ValueError("Нужна ссылка t.me или @username.")
        parts = parsed.path.strip("/").split("/")
        if parts[0].startswith("+"):
            invite = parts[0][1:]
            if not INVITE.fullmatch(invite):
                raise ValueError("Некорректный инвайт.")
            return ChatReference(invite_hash=invite)
        if parts[0] == "joinchat" and len(parts) == 2 and INVITE.fullmatch(parts[1]):
            return ChatReference(invite_hash=parts[1])
        username = parts[0]
    else:
        username = value
    if not USERNAME.fullmatch(username):
        raise ValueError("Некорректный username.")
    return ChatReference(username=username)


def message_link(chat_id: int, username: str | None, message_id: int, thread_id: int | None = None) -> str | None:
    if message_id <= 0:
        return None
    if username and USERNAME.fullmatch(username):
        base = "https://t.me/" + username
    elif str(chat_id).startswith("-100"):
        base = "https://t.me/c/" + str(chat_id)[4:]
    else:
        return None
    topic = f"{thread_id}/" if thread_id and thread_id > 0 and thread_id != message_id else ""
    return f"{base}/{topic}{message_id}"
