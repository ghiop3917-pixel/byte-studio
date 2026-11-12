"""MTProto-клиент чтения, динамические источники и восстановление истории."""

import asyncio
import logging
import time
from datetime import timezone
from typing import Any

from telethon import TelegramClient, errors, events, types, utils

from app.config import Settings
from app.db.models import utc_now
from app.db.session import Database
from app.pipeline.ingestion import IncomingMessage, Ingestion, SourceInfo
from app.utils.session import load_session


class ReaderCooldown(RuntimeError):
    pass


class ReaderAuthorizationError(RuntimeError):
    pass


class ReadOnlyTelegramClient(TelegramClient):
    async def __call__(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        requests = request if isinstance(request, (tuple, list)) else (request,)
        blocked = {
            "SendMessageRequest",
            "SendMediaRequest",
            "SendMultiMediaRequest",
            "ForwardMessagesRequest",
            "SendInlineBotResultRequest",
        }
        if any(type(item).__name__ in blocked for item in requests):
            raise PermissionError("Юзербот не отправляет сообщения.")
        return await super().__call__(request, *args, **kwargs)

    async def send_message(self, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError("Юзербот не отправляет сообщения.")

    async def send_file(self, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError("Юзербот не отправляет файлы.")

    async def forward_messages(self, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError("Юзербот не пересылает сообщения.")


def snapshot(source: SourceInfo, message: Any) -> IncomingMessage:
    sender = getattr(message, "sender", None)
    peer = getattr(message, "from_id", None)
    author = peer.user_id if isinstance(peer, types.PeerUser) else None
    is_user = isinstance(sender, types.User)
    name = (
        " ".join(filter(None, (getattr(sender, "first_name", None), getattr(sender, "last_name", None))))
        if is_user
        else None
    )
    reply = getattr(message, "reply_to", None)
    thread = None
    if reply and getattr(reply, "forum_topic", False):
        thread = getattr(reply, "reply_to_top_id", None) or getattr(reply, "reply_to_msg_id", None)
    date = getattr(message, "date", None)
    if date and (date.tzinfo is None or date.utcoffset() is None):
        raise ValueError("Дата Telegram должна содержать часовой пояс.")
    metadata = {
        "service": getattr(message, "action", None) is not None,
        "forwarded": getattr(message, "fwd_from", None) is not None,
        "is_bot": getattr(sender, "bot", None) if is_user else None,
        "thread_id": thread,
        "author_kind": "user"
        if author is not None
        else "channel_or_anonymous"
        if isinstance(peer, types.PeerChannel)
        else "unknown",
        "media_type": type(message.media).__name__ if getattr(message, "media", None) else None,
    }
    return IncomingMessage(
        source.chat_id,
        message.id,
        getattr(message, "raw_text", None) or "",
        date.astimezone(timezone.utc) if date else None,
        author,
        getattr(sender, "username", None) if is_user else None,
        name,
        metadata,
    )


class ReaderRPC:
    def __init__(self, storage: Ingestion, settings: Settings) -> None:
        self.storage = storage
        self.settings = settings
        self.lock = asyncio.Lock()
        self.last_request = 0.0

    async def call(self, kind: str, action):
        async with self.lock:
            if await self.storage.cooldown(kind) > utc_now():
                raise ReaderCooldown()
            await asyncio.sleep(
                max(0, self.settings.reader_rpc_interval_seconds - (time.monotonic() - self.last_request))
            )
            self.last_request = time.monotonic()
            try:
                return await action()
            except errors.FloodWaitError as error:
                await self.storage.set_cooldown(kind, error.seconds)
                raise


class TelegramReader:
    def __init__(self, client: Any, storage: Ingestion, settings: Settings, rpc: ReaderRPC | None = None) -> None:
        self.client = client
        self.storage = storage
        self.settings = settings
        self.rpc = rpc or ReaderRPC(storage, settings)
        self.sources: dict[int, SourceInfo] = {}
        self.entities: dict[int, Any] = {}

    async def refresh(self) -> None:
        sources = await self.storage.active_sources()
        self.sources = {source.chat_id: source for source in sources}
        self.entities = {key: value for key, value in self.entities.items() if key in self.sources}
        await self.storage.heartbeat("reader_sources", str(len(self.sources)))

    async def on_message(self, event: Any) -> None:
        source = self.sources.get(event.chat_id)
        if source is None:
            return
        try:
            await self.storage.ingest(source.id, snapshot(source, event.message))
        except Exception as error:
            # Позиция истории не меняется: сообщение восстановится при catch-up.
            logging.error("NewMessage %s: %s", source.id, type(error).__name__)

    async def collect_history(self, source: SourceInfo) -> None:
        cursor, ready = await self.storage.history_state(source.id)
        if not ready:
            return
        try:
            entity = self.entities.get(source.chat_id)
            if entity is None:
                reference = (
                    types.InputPeerChannel(int(str(source.chat_id)[4:]), source.access_hash)
                    if source.access_hash is not None and str(source.chat_id).startswith("-100")
                    else "@" + source.username
                    if source.username
                    else source.chat_id
                )
                entity = await self.rpc.call("resolve", lambda: self.client.get_entity(reference))
                if (
                    not isinstance(entity, (types.Chat, types.Channel))
                    or getattr(entity, "broadcast", False)
                    or (isinstance(entity, types.Channel) and not entity.megagroup)
                    or utils.get_peer_id(entity) != source.chat_id
                ):
                    raise ValueError("Источник не является группой.")
                if getattr(entity, "left", False) or getattr(entity, "deactivated", False):
                    raise errors.ChannelPrivateError(request=None)
                self.entities[source.chat_id] = entity
            if cursor is None:
                messages = await self.rpc.call(
                    "history",
                    lambda: self.client.get_messages(entity, limit=self.settings.reader_initial_history_limit),
                )
                messages = sorted(messages, key=lambda item: item.id)
            else:

                async def batch():
                    return [
                        message
                        async for message in self.client.iter_messages(
                            entity, min_id=cursor, reverse=True, limit=self.settings.reader_history_batch_size
                        )
                    ]

                messages = await self.rpc.call("history", batch)
            for message in messages:
                await self.storage.ingest(source.id, snapshot(source, message), history=True)
            await self.storage.source_success(source.id)
        except ReaderCooldown:
            return
        except (errors.ChannelPrivateError, errors.UserBannedInChannelError) as error:
            await self.storage.source_error(source.id, error, unavailable=True)
        except (errors.RPCError, OSError, ValueError) as error:
            await self.storage.source_error(source.id, error)

    async def history_loop(self) -> None:
        next_refresh = 0.0
        while True:
            if time.monotonic() >= next_refresh:
                await self.refresh()
                next_refresh = time.monotonic() + self.settings.reader_refresh_seconds
            for source in tuple(self.sources.values()):
                await self.collect_history(source)
            await self.storage.heartbeat("reader")
            await asyncio.sleep(self.settings.reader_poll_seconds)


class ReaderHub:
    def __init__(self) -> None:
        self.reader: TelegramReader | None = None

    async def resolve(self, username: str) -> Any:
        if self.reader is None:
            raise ReaderAuthorizationError("Читатель не подключён. Используйте явный запуск --run.")
        return await self.reader.rpc.call("resolve", lambda: self.reader.client.get_entity("@" + username))

    async def refresh(self) -> None:
        if self.reader is not None:
            await self.reader.refresh()


async def run_reader(database: Database, settings: Settings, *, hub: ReaderHub | None = None) -> None:
    if not settings.telegram_api_id or not settings.telegram_api_hash.get_secret_value():
        raise ReaderAuthorizationError("Укажите TELEGRAM_API_ID и TELEGRAM_API_HASH.")
    session = await asyncio.to_thread(load_session, settings.telegram_session_paths[0])
    client = ReadOnlyTelegramClient(
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
            logging.error("Сессия не авторизована. Первый вход выполняется отдельно через login.py.")
            raise ReaderAuthorizationError("Первый вход выполняется отдельно через login.py.")
        storage = Ingestion(database, settings)
        await storage.seed_keywords()
        reader = TelegramReader(client, storage, settings)
        await reader.refresh()
        if hub is not None:
            hub.reader = reader
        client.add_event_handler(reader.on_message, events.NewMessage())
        try:
            await reader.history_loop()
        finally:
            if hub is not None and hub.reader is reader:
                hub.reader = None
            client.remove_event_handler(reader.on_message)
    finally:
        await client.disconnect()
