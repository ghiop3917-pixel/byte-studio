import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telethon import TelegramClient, errors, functions, types

from bot import DIRECTIONS, classify, fingerprint
from monitor import EXTRA_SOURCES, INACTIVE_SOURCES, open_session, source_names
from session_config import session_path

TARGET = 200
CITIES = (
    "Москва",
    "Петербург",
    "Казань",
    "Екатеринбург",
    "Новосибирск",
    "Краснодар",
    "Ростов",
    "Самара",
    "Уфа",
    "Пермь",
    "Сочи",
    "Тюмень",
    "Воронеж",
    "Омск",
    "Челябинск",
    "Красноярск",
    "Владивосток",
    "Иркутск",
    "Калининград",
    "Тула",
    "Ярославль",
    "Тверь",
    "Нижний Новгород",
    "Волгоград",
    "Саратов",
    "Барнаул",
    "Хабаровск",
    "Кемерово",
    "Ижевск",
    "Ставрополь",
    "Томск",
    "Липецк",
    "Рязань",
    "Пенза",
    "Оренбург",
    "Киров",
    "Курск",
    "Брянск",
    "Мурманск",
)


def is_group(group):
    return isinstance(group, types.Channel) and group.megagroup and not group.broadcast


async def candidates(client):
    cache = EXTRA_SOURCES.with_name("business-candidates.json")
    existing = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    seen = set()
    queries = [
        "ищу подрядчика",
        "заказы разработка",
        "дизайн заказы чат",
        "заказы сайты чат",
        "заказы монтаж чат",
        "чат заказчиков",
        "поиск подрядчиков",
        "чат селлеров",
        "бизнес нетворкинг",
    ]
    queries.extend(f"{topic} {city}" for city in CITIES for topic in ("бизнес чат", "предприниматели"))
    for name in existing:
        seen.add(name.casefold())
        yield name
    for query in queries:
        result = await client(functions.contacts.SearchRequest(query, 40))
        print(f"Проверен поиск «{query}». Найдено результатов: {len(result.chats)}.", flush=True)
        for group in result.chats:
            if is_group(group) and group.username and group.username.casefold() not in seen:
                seen.add(group.username.casefold())
                yield group.username


async def active_group(client, name):
    group = await client.get_entity("@" + name)
    if not is_group(group):
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    messages = [
        m
        for m in await client.get_messages(group, limit=150)
        if m.date >= cutoff
        and m.raw_text
        and isinstance(m.from_id, types.PeerUser)
        and not getattr(m.sender, "bot", False)
    ]
    authors = {m.sender_id for m in messages}
    matches = {fingerprint(m.raw_text) for m in messages if classify(m.raw_text, DIRECTIONS)}
    if len(authors) < 5 or len(matches) < 2:
        return None
    return group


async def add_group(client, group):
    if group.left:
        await client(functions.channels.JoinChannelRequest(group))
    peer = await client.get_input_entity(group)
    notify = types.InputNotifyPeer(peer)
    await client(
        functions.account.UpdateNotifySettingsRequest(notify, types.InputPeerNotifySettings(mute_until=2147483647))
    )
    await client(functions.folders.EditPeerFoldersRequest([types.InputFolderPeer(peer, 1)]))
    settings = await client(functions.account.GetNotifySettingsRequest(notify))
    dialogs = await client(functions.messages.GetPeerDialogsRequest([types.InputDialogPeer(peer)]))
    return bool(settings.mute_until and settings.mute_until > datetime.now(timezone.utc)) and any(
        getattr(dialog, "folder_id", None) == 1 for dialog in dialogs.dialogs
    )


async def main(session=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("telethon.network").setLevel(logging.WARNING)
    path = Path(session).expanduser().resolve() if session else session_path()
    client = TelegramClient(
        open_session(path, explicit=True if session else None),
        int(os.environ.pop("TELEGRAM_LEADS_API_ID")),
        os.environ.pop("TELEGRAM_LEADS_API_HASH"),
        flood_sleep_threshold=86400,
    )
    await client.connect()
    producer = None
    try:
        if not await client.is_user_authorized():
            raise SystemExit("Сессия Telegram требует повторного входа.")
        queue = asyncio.Queue(maxsize=25)

        async def select_groups():
            try:
                async for name in candidates(client):
                    current = (*source_names(), *INACTIVE_SOURCES)
                    if name.casefold() in {item.casefold() for item in current}:
                        continue
                    try:
                        group = await active_group(client, name)
                    except (errors.RPCError, ValueError) as error:
                        print(f"@{name}: {type(error).__name__}.", flush=True)
                        if isinstance(error, errors.FloodWaitError):
                            raise
                        continue
                    if group:
                        await queue.put(group)
            except Exception as error:
                await queue.put(error)
            else:
                await queue.put(None)

        producer = asyncio.create_task(select_groups())
        while True:
            current = source_names()
            if len(current) >= TARGET:
                print(f"Достигнута цель: {len(current)} групп.", flush=True)
                return
            group = await queue.get()
            if group is None:
                break
            if isinstance(group, Exception):
                raise group
            name = group.username
            try:
                accepted = await add_group(client, group)
            except (errors.RPCError, ValueError) as error:
                print(f"@{name}: {type(error).__name__}.", flush=True)
                if isinstance(error, errors.FloodWaitError):
                    raise
                continue
            if accepted:
                extra = json.loads(EXTRA_SOURCES.read_text(encoding="utf-8")) if EXTRA_SOURCES.exists() else []
                temporary = EXTRA_SOURCES.with_suffix(".tmp")
                temporary.write_text(json.dumps([*extra, name], indent=2), encoding="utf-8")
                temporary.replace(EXTRA_SOURCES)
                print(f"@{name} в архиве без уведомлений. Групп: {len(source_names())}/{TARGET}.", flush=True)
        print(f"Список кандидатов исчерпан. Групп: {len(source_names())}/{TARGET}.", flush=True)
    finally:
        if producer:
            producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)
        await client.disconnect()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--session")
    asyncio.run(main(parser.parse_args().session))
