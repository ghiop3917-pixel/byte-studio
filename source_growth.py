"""Проверка и добавление активных публичных групп с сохранением прогресса."""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telethon import TelegramClient, errors, functions, types, utils

from bot_menu import MONITOR_DB, saved_directions
from delivery_filter import normalized
from expand_sources import CITIES, is_group
from lead_analysis import analyze
from monitor import EXTRA_SOURCES, INACTIVE_SOURCES, open_session, recipient_id, source_names
from reliability import acquire_lock, heartbeat, ingest, open_database, renew_lock
from session_config import session_path

STATE = EXTRA_SOURCES.with_name("source-expansion.json")
CANDIDATES = EXTRA_SOURCES.with_name("business-candidates.json")
TARGET = 200
REQUEST_INTERVAL = 6


class SharedCooldown(RuntimeError):
    pass


def save_json(path, value):
    temporary = path.with_name(path.name + ".growth-tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def assess_messages(messages, directions, now=None):
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=7)
    human = [
        m
        for m in messages
        if m.date
        and m.date >= cutoff
        and m.raw_text
        and isinstance(m.from_id, types.PeerUser)
        and not getattr(m.sender, "bot", False)
    ]
    authors = {m.sender_id for m in human}
    latest = max((m.date for m in human), default=None)
    matches = set()
    for message in human:
        sender = message.sender
        author = {"id": message.sender_id, "username": getattr(sender, "username", None), "is_bot": False}
        if analyze(message.raw_text, directions, author)["bucket"] == "lead":
            matches.add(normalized(message.raw_text))
    active = len(human) >= 10 and len(authors) >= 5 and latest is not None and latest >= now - timedelta(days=2)
    return {
        "active": active,
        "human_messages": len(human),
        "authors": len(authors),
        "lead_examples": len(matches),
        "latest_message_at": latest.isoformat() if latest else None,
        "accepted": active and len(matches) >= 1,
    }


def append_source(name):
    extra = json.loads(EXTRA_SOURCES.read_text(encoding="utf-8")) if EXTRA_SOURCES.exists() else []
    if name.casefold() not in {n.casefold() for n in (*source_names(), *INACTIVE_SOURCES)}:
        save_json(EXTRA_SOURCES, [*extra, name])


class GrowthClient(TelegramClient):
    def configure_growth(self, db, token, state):
        self.growth_db = db
        self.growth_token = token
        self.growth_state = state
        self.growth_last = 0
        self.growth_mutex = asyncio.Lock()

    async def __call__(self, request, *args, **kwargs):
        async with self.growth_mutex:
            until = max(
                (
                    float(row[0])
                    for row in self.growth_db.execute(
                        (
                            "SELECT detail FROM core_heartbeat WHERE component IN ('cooldown:reso"
                            "lve','cooldown:history','cooldown:growth')"
                        )
                    )
                    if row[0]
                ),
                default=0,
            )
            if until > time.time():
                self.growth_state.update(status="cooldown", cooldown_until=until)
                save_json(STATE, self.growth_state)
                raise SharedCooldown("Действует пауза аккаунта Telegram.")
            renew_lock(self.growth_db, "source_growth", self.growth_token)
            await asyncio.sleep(max(0, REQUEST_INTERVAL - (time.monotonic() - self.growth_last)))
            self.growth_last = time.monotonic()
            try:
                return await super().__call__(request, *args, **kwargs)
            except errors.FloodWaitError as error:
                until = time.time() + error.seconds + 5
                kind = type(request).__name__
                method = "resolve" if "ResolveUsername" in kind else "history" if "History" in kind else "growth"
                heartbeat(self.growth_db, "cooldown:" + method, str(until))
                self.growth_state.update(
                    status="cooldown", cooldown_until=until, error=type(error).__name__, method=kind
                )
                save_json(STATE, self.growth_state)
                raise


async def mute_archive_join(client, group):
    peer = await client.get_input_entity(group)
    notify = types.InputNotifyPeer(peer)
    # Уведомления отключаются до вступления, чтобы новая группа не засыпала аккаунт.
    await client(
        functions.account.UpdateNotifySettingsRequest(notify, types.InputPeerNotifySettings(mute_until=2147483647))
    )
    if group.left:
        await client(functions.channels.JoinChannelRequest(group))
    await client(functions.folders.EditPeerFoldersRequest([types.InputFolderPeer(peer, 1)]))
    settings = await client(functions.account.GetNotifySettingsRequest(notify))
    dialogs = await client(functions.messages.GetPeerDialogsRequest([types.InputDialogPeer(peer)]))
    muted = bool(settings.mute_until and settings.mute_until > datetime.now(timezone.utc))
    archived = any(getattr(dialog, "folder_id", None) == 1 for dialog in dialogs.dialogs)
    return muted and archived


async def main(session=None):
    logging.basicConfig(level=logging.WARNING)
    state = (
        json.loads(STATE.read_text(encoding="utf-8"))
        if STATE.exists()
        else {"checked": {}, "query_index": 0, "added": []}
    )
    if state.get("cooldown_until", 0) > time.time():
        print(
            "Действует ограничение Telegram до " + datetime.fromtimestamp(state["cooldown_until"]).isoformat(),
            flush=True,
        )
        return
    db = open_database(MONITOR_DB)
    token = acquire_lock(db, "source_growth")
    client = GrowthClient(
        open_session(
            Path(session).expanduser().resolve() if session else session_path(),
            explicit=True if session else None,
        ),
        int(os.environ.pop("TELEGRAM_LEADS_API_ID")),
        os.environ.pop("TELEGRAM_LEADS_API_HASH"),
        flood_sleep_threshold=0,
        request_retries=0,
        connection_retries=2,
        timeout=15,
    )
    client.configure_growth(db, token, state)
    try:
        state.update(status="running", target=TARGET, started_at=time.time())
        save_json(STATE, state)
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError("Сессия требует проверки владельцем.")
        owner = recipient_id(None)
        directions = saved_directions(owner)
        if not directions:
            raise RuntimeError("Направления поиска выключены владельцем.")
        candidates = json.loads(CANDIDATES.read_text(encoding="utf-8")) if CANDIDATES.exists() else {}
        entities = {}
        async for dialog in client.iter_dialogs(limit=300):
            entity = dialog.entity
            if is_group(entity) and entity.username:
                candidates.setdefault(entity.username, entity.title)
                entities[entity.username] = entity
        save_json(CANDIDATES, candidates)
        queries = [
            "чат заказчиков",
            "заказы сайты",
            "фриланс заказы чат",
            "поиск подрядчика",
            "заказы telegram боты",
            "ищу исполнителя",
            "стартап чат",
            "бизнес нетворкинг",
            "чат селлеров",
        ]
        queries += [f"{topic} {city}" for city in CITIES for topic in ("бизнес чат", "предприниматели")]
        cached = list(candidates)
        pending = []
        while len(source_names()) < TARGET:
            if not pending:
                index = state.get("query_index", 0)
                if index >= 9 and cached:
                    pending = cached
                    cached = []
                    continue
                if index >= len(queries):
                    state["status"] = "candidates_exhausted"
                    break
                query = queries[index]
                result = await client(functions.contacts.SearchRequest(query, 40))
                state["query_index"] = index + 1
                for group in result.chats:
                    if is_group(group) and group.username and group.username not in candidates:
                        candidates[group.username] = group.title
                        pending.append(group.username)
                        entities[group.username] = group
                save_json(CANDIDATES, candidates)
                save_json(STATE, state)
                print(f"Поиск «{query}»: новых кандидатов {len(pending)}.", flush=True)
                continue
            name = pending.pop(0)
            if name.casefold() in {n.casefold() for n in (*source_names(), *INACTIVE_SOURCES)}:
                continue
            previous = state["checked"].get(name, {})
            if previous.get("checked_at", 0) > time.time() - 7 * 86400 and previous.get("status") != "setup_pending":
                continue
            try:
                group = entities.get(name) or await client.get_entity("@" + name)
                if not is_group(group) or not group.username:
                    state["checked"][name] = {"status": "not_public_group", "checked_at": time.time()}
                    save_json(STATE, state)
                    continue
                already = db.execute(
                    "SELECT 1 FROM source_health WHERE chat_id=?", (utils.get_peer_id(group),)
                ).fetchone()
                if already:
                    continue
                messages = await client.get_messages(group, limit=100)
                report = assess_messages(messages, directions)
                state["checked"][name] = {
                    **report,
                    "checked_at": time.time(),
                    "status": "eligible"
                    if report["accepted"]
                    else "no_recent_orders"
                    if report["active"]
                    else "inactive",
                }
                save_json(STATE, state)
                print(
                    f"@{name}: авторов {report['authors']}, запросов {report['lead_examples']}, "
                    + ("подходит" if report["accepted"] else "не добавлен")
                    + ".",
                    flush=True,
                )
                if not report["accepted"]:
                    continue
                state["checked"][name]["status"] = "setup_pending"
                save_json(STATE, state)
                if await mute_archive_join(client, group):
                    append_source(group.username)
                    state["checked"][name]["status"] = "added"
                    if group.username not in state["added"]:
                        state["added"].append(group.username)
                    from telegram_runtime import telegram_message

                    for message in reversed(messages):
                        ingest(db, telegram_message(group, message), owner)
                    save_json(STATE, state)
                    print(
                        f"Добавлен @{group.username}: архив, без звука. Всего {len(source_names())}/{TARGET}.",
                        flush=True,
                    )
            except errors.FloodWaitError:
                raise
            except (errors.ChannelsTooMuchError, errors.PeerFloodError):
                raise
            except (errors.RPCError, ValueError) as error:
                state["checked"][name] = {
                    "status": "unavailable",
                    "checked_at": time.time(),
                    "error": type(error).__name__,
                }
                save_json(STATE, state)
                print(f"@{name}: {type(error).__name__}.", flush=True)
        if len(source_names()) >= TARGET:
            state["status"] = "completed"
    except errors.FloodWaitError as error:
        print(f"Telegram требует паузу {error.seconds} сек. Прогресс сохранён; обхода и повторов нет.", flush=True)
    except SharedCooldown:
        print("Расширение остановлено: действует пауза Telegram. Прогресс сохранён.", flush=True)
    except (errors.ChannelsTooMuchError, errors.PeerFloodError) as error:
        state.update(status="telegram_limit", error=type(error).__name__)
        print("Остановлено по ограничению Telegram: " + type(error).__name__, flush=True)
    except (errors.RPCError, OSError, ValueError, RuntimeError) as error:
        state.update(status="failed", error=type(error).__name__)
        print("Проверка остановлена: " + type(error).__name__, flush=True)
    finally:
        if state.get("status") == "running":
            state["status"] = "interrupted"
        state["total_sources"] = len(source_names())
        state["updated_at"] = time.time()
        save_json(STATE, state)
        db.execute("UPDATE core_locks SET lease_until=0 WHERE name='source_growth' AND token=?", (token,))
        await client.disconnect()
        db.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--session")
    asyncio.run(main(parser.parse_args().session))
