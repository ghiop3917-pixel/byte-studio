"""Админ-команды; ни одна команда не отправляет сообщение в чужой чат."""

from html import escape

from aiogram.filters.command import CommandObject
from aiogram.types import Message

from app.bot import texts
from app.bot.accounts import AccountError, Accounts
from app.services_sources import AdminSources
from app.userbot.client import ReaderHub


async def answer_lines(message: Message, template: str, lines: list[str], limit: int, **values: str) -> None:
    """Разбиваем только между целыми HTML-строками, не повреждая сущности."""
    chunk: list[str] = []
    for line in lines or [texts.EMPTY_LIST]:
        rendered = template.format(lines="\n".join([*chunk, line]), **values)
        if len(rendered) > limit and chunk:
            await message.answer(template.format(lines="\n".join(chunk), **values), parse_mode="HTML")
            chunk = []
        if len(template.format(lines=line, **values)) > limit:
            raise AccountError(texts.UI_ERROR)
        chunk.append(line)
    await message.answer(template.format(lines="\n".join(chunk), **values), parse_mode="HTML")


def service(message: Message, accounts: Accounts, hub: ReaderHub | None) -> AdminSources:
    if message.from_user is None or message.chat.type != "private" or message.chat.id != message.from_user.id:
        raise AccountError(texts.ADMIN_ONLY)
    return AdminSources(accounts.database, accounts.settings, hub)


async def add_chat(
    message: Message, command: CommandObject, accounts: Accounts, reader_hub: ReaderHub | None = None
) -> None:
    control = service(message, accounts, reader_hub)
    if not command.args:
        raise AccountError(texts.ADD_CHAT_USAGE)
    status, title = await control.add_chat(message.from_user.id, command.args)
    template = texts.SOURCE_ADDED if status == "added" else texts.SOURCE_PENDING
    await message.answer(template.format(title=escape(title)), parse_mode="HTML")


async def remove_chat(
    message: Message, command: CommandObject, accounts: Accounts, reader_hub: ReaderHub | None = None
) -> None:
    control = service(message, accounts, reader_hub)
    try:
        identity = int(command.args or "")
    except ValueError:
        raise AccountError(texts.REMOVE_CHAT_USAGE) from None
    await control.remove_chat(message.from_user.id, identity)
    await message.answer(texts.SOURCE_DISABLED)


async def chats(message: Message, accounts: Accounts, reader_hub: ReaderHub | None = None) -> None:
    control = service(message, accounts, reader_hub)
    rows = await control.chats(message.from_user.id)
    lines = [texts.SOURCE_LINE.format(**{key: escape(str(value)) for key, value in row.items()}) for row in rows]
    await answer_lines(message, texts.SOURCES_TITLE, lines, accounts.settings.telegram_text_limit)


async def keywords(
    message: Message, command: CommandObject, accounts: Accounts, reader_hub: ReaderHub | None = None
) -> None:
    control = service(message, accounts, reader_hub)
    parts = (command.args or "").split(maxsplit=3)
    if not parts:
        raise AccountError(texts.KEYWORDS_USAGE)
    slug = parts[0]
    action = parts[1] if len(parts) > 1 else "list"
    if action in {"add", "regex"} and len(parts) == 4:
        rows = await control.keywords(
            message.from_user.id, slug, action="add", kind=parts[2], word=parts[3], regex=action == "regex"
        )
    elif action == "remove" and len(parts) == 3:
        try:
            identity = int(parts[2])
        except ValueError:
            raise AccountError(texts.KEYWORDS_USAGE) from None
        rows = await control.keywords(message.from_user.id, slug, action="remove", keyword_id=identity)
    elif action == "list" and len(parts) == 1:
        rows = await control.keywords(message.from_user.id, slug)
    else:
        raise AccountError(texts.KEYWORDS_USAGE)
    lines = [
        texts.KEYWORD_LINE.format(
            id=row["id"],
            kind=row["kind"],
            regex=" regex" if row["regex"] else "",
            word=escape(row["word"]),
            active="✓" if row["active"] else "—",
        )
        for row in rows
    ]
    await answer_lines(message, texts.KEYWORDS_TITLE, lines, accounts.settings.telegram_text_limit, slug=escape(slug))
