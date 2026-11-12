"""Поиск и очередь вступлений. Сеть вне транзакций; никаких сообщений в чужие чаты."""

import asyncio
import json
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from telethon import errors, functions, types, utils

from app.bot import texts
from app.bot.accounts import AccountError
from app.config import Settings
from app.db.models import (
    ChatCandidate,
    DiscoveryRun,
    JoinAttempt,
    JoinJob,
    Lead,
    RawMessage,
    ReaderState,
    SourceChat,
    User,
    utc_now,
)
from app.db.repo import Repository
from app.db.session import Database
from app.pipeline.ingestion import Ingestion
from app.services_sources import AdminSources
from app.userbot.client import ReaderAuthorizationError, ReaderCooldown, ReaderHub
from app.utils.links import parse_chat_reference

LINK = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/(?:\+[A-Za-z0-9_-]+|joinchat/[A-Za-z0-9_-]+|[A-Za-z][A-Za-z0-9_]{3,31})(?:/\d+)*",
    re.IGNORECASE,
)


def writable_group(chat: Any, *, supergroup: bool = False) -> bool:
    if not isinstance(chat, (types.Chat, types.Channel)):
        return False
    if supergroup and not isinstance(chat, types.Channel):
        return False
    rights = getattr(chat, "default_banned_rights", None)
    return not (
        getattr(chat, "broadcast", False)
        or (isinstance(chat, types.Channel) and not chat.megagroup)
        or getattr(chat, "scam", False)
        or getattr(chat, "fake", False)
        or getattr(chat, "deactivated", False)
        or (rights and (rights.send_messages or getattr(rights, "send_plain", False)))
    )


def can_participate(permissions: Any) -> bool:
    """Реальный ParticipantPermissions Telethon не имеет свойства send_messages."""
    if permissions is None or getattr(permissions, "has_left", True):
        return False
    if permissions.is_admin or permissions.has_default_permissions:
        return True
    participant = getattr(permissions, "participant", None)
    rights = getattr(participant, "banned_rights", None)
    return bool(
        rights is not None
        and not (rights.view_messages or rights.send_messages or getattr(rights, "send_plain", False))
    )


@dataclass(frozen=True)
class JoinClaim:
    id: int
    candidate_id: int
    token: str
    attempts: int
    chat_id: int | None
    access_hash: int | None
    username: str | None
    invite_hash: str | None


class Growth:
    def __init__(self, database: Database, settings: Settings, hub: ReaderHub) -> None:
        self.database, self.settings, self.hub = database, settings, hub
        self.storage = Ingestion(database, settings)

    async def _state(self, session, key: str, detail: str) -> None:
        row = await session.get(ReaderState, key)
        if row is None:
            session.add(ReaderState(key=key, detail=detail, updated_at=utc_now()))
        else:
            row.detail, row.updated_at = detail, utc_now()

    def reader(self):
        if self.hub.reader is None:
            raise ReaderAuthorizationError(texts.READER_OFFLINE)
        return self.hub.reader

    async def rpc(self, kind: str, action):
        return await self.reader().rpc.call(kind, action)

    async def request_discovery(self, tg_id: int, slug: str) -> None:
        async with self.database.session() as session:
            await AdminSources(self.database, self.settings)._admin(session, tg_id)
            if slug not in self.settings.discovery_queries or not self.settings.discovery_queries[slug]:
                raise AccountError(texts.CATEGORY_UNKNOWN)
            await self._enqueue_discovery(session, slug)

    async def _enqueue_discovery(self, session, slug: str) -> None:
        run = await session.get(DiscoveryRun, slug)
        if run is None:
            session.add(DiscoveryRun(slug=slug))
        elif run.status in {"completed", "failed"}:
            run.status, run.query_index, run.attempts = "pending", 0, 0
            run.due_at, run.error = utc_now(), None

    async def schedule_discovery(self) -> None:
        async with self.database.session() as session:
            for slug, queries in self.settings.discovery_queries.items():
                if not queries:
                    continue
                run = await session.get(DiscoveryRun, slug)
                if run is None or (
                    run.status == "completed"
                    and run.updated_at + timedelta(seconds=self.settings.discovery_interval_seconds) <= utc_now()
                ):
                    await self._enqueue_discovery(session, slug)
            # Failed не запускается снова бесконечно: только ручной /discover.

    async def save_candidate(self, chat, slug: str, participants: int, messages: int) -> int:
        chat_id = utils.get_peer_id(chat)
        async with self.database.session() as session:
            source = await session.scalar(select(SourceChat).where(SourceChat.tg_chat_id == chat_id))
            row = await session.scalar(select(ChatCandidate).where(ChatCandidate.tg_chat_id == chat_id))
            # Username служит только предварительным указателем; после resolve — постоянный ID.
            if row is None and getattr(chat, "username", None):
                row = await session.scalar(
                    select(ChatCandidate).where(
                        ChatCandidate.tg_chat_id.is_(None),
                        func.lower(ChatCandidate.username) == chat.username.casefold(),
                    )
                )
            if row is None:
                row = ChatCandidate(tg_chat_id=chat_id, title=chat.title, status="pending")
                session.add(row)
            row.tg_chat_id, row.access_hash = chat_id, getattr(chat, "access_hash", None)
            row.username, row.title = getattr(chat, "username", None), chat.title
            row.participants = participants
            row.msgs_per_day = Decimal(messages) / self.settings.activity_window_days
            row.category_hint = slug
            if source is not None and source.is_active:
                row.status = "joined"
            await session.flush()
            if self.settings.auto_join and row.status == "pending":
                row.status = "approved"
                await self._enqueue_join(session, row, None)
            return row.id

    async def discover_one(self) -> bool:
        if self.hub.reader is None:
            return False
        async with self.database.session() as session:
            run = await session.scalar(
                select(DiscoveryRun)
                .where(
                    DiscoveryRun.status.in_(("pending", "retry_wait", "processing")),
                    DiscoveryRun.due_at <= utc_now(),
                )
                .order_by(DiscoveryRun.updated_at)
                .limit(1)
            )
            if run is None:
                return False
            slug, index = run.slug, run.query_index
            queries = self.settings.discovery_queries.get(slug, ())
            if index >= len(queries):
                run.status, run.updated_at = "completed", utc_now()
                return True
            run.status = "processing"
        try:
            client = self.reader().client
            result = await self.rpc(
                "search",
                lambda: client(
                    functions.contacts.SearchRequest(q=queries[index], limit=self.settings.discovery_search_limit)
                ),
            )
            for chat in result.chats:
                if not writable_group(chat, supergroup=True) or not getattr(chat, "username", None):
                    continue
                participants = getattr(chat, "participants_count", None)
                if participants is None:
                    try:
                        full = await self.rpc(
                            "resolve", lambda chat=chat: client(functions.channels.GetFullChannelRequest(chat))
                        )
                    except (errors.ChannelPrivateError, errors.UserBannedInChannelError):
                        continue
                    participants = getattr(full.full_chat, "participants_count", None)
                if participants is None or participants < self.settings.min_chat_participants:
                    continue
                try:
                    messages = await self.rpc(
                        "history",
                        lambda chat=chat: client.get_messages(chat, limit=self.settings.activity_sample_limit),
                    )
                except (errors.ChannelPrivateError, errors.UserBannedInChannelError):
                    continue
                cutoff = utc_now() - timedelta(days=self.settings.activity_window_days)
                recent = [
                    m
                    for m in messages
                    if getattr(m, "date", None)
                    and m.date >= cutoff
                    and not getattr(m, "action", None)
                    and not getattr(getattr(m, "sender", None), "bot", False)
                ]
                authors = {m.from_id.user_id for m in recent if isinstance(getattr(m, "from_id", None), types.PeerUser)}
                if (
                    len(recent) >= self.settings.min_active_messages
                    and len(authors) >= self.settings.min_unique_authors
                ):
                    await self.save_candidate(chat, slug, participants, len(recent))
            async with self.database.session() as session:
                run = await session.get(DiscoveryRun, slug)
                run.query_index, run.attempts, run.error = index + 1, 0, None
                run.status = "completed" if run.query_index >= len(queries) else "pending"
                run.updated_at = run.due_at = utc_now()
            return True
        except (ReaderCooldown, ReaderAuthorizationError):
            async with self.database.session() as session:
                run = await session.get(DiscoveryRun, slug)
                run.status = "retry_wait"
                run.due_at = utc_now() + timedelta(seconds=self.settings.growth_poll_seconds)
            return False
        except (errors.RPCError, OSError, ValueError) as error:
            async with self.database.session() as session:
                run = await session.get(DiscoveryRun, slug)
                run.attempts += 1
                run.error = type(error).__name__
                run.status = "failed" if run.attempts >= self.settings.discovery_max_attempts else "retry_wait"
                run.due_at = utc_now() + timedelta(
                    seconds=min(
                        self.settings.retry_initial_seconds * 2 ** (run.attempts - 1), self.settings.retry_max_seconds
                    )
                )
                run.updated_at = utc_now()
            return True

    async def collect_links(self) -> int:
        async with self.database.session() as session:
            marker = await session.get(ReaderState, "candidate_links")
            cursor = int(marker.detail) if marker else 0
            rows = (
                await session.scalars(
                    select(RawMessage)
                    .where(RawMessage.id > cursor)
                    .order_by(RawMessage.id)
                    .limit(self.settings.link_scan_batch)
                )
            ).all()
            for raw in rows:
                for match in list(LINK.finditer(raw.original_text))[: self.settings.links_per_message]:
                    ref = parse_chat_reference(match.group())
                    if ref.username:
                        if ref.username.casefold() in {"joinchat", "share", "proxy", "socks", "iv"}:
                            continue
                        existing = await session.scalar(
                            select(ChatCandidate.id).where(
                                func.lower(ChatCandidate.username) == ref.username.casefold()
                            )
                        )
                    else:
                        existing = await session.scalar(
                            select(ChatCandidate.id).where(ChatCandidate.invite_hash == ref.invite_hash)
                        )
                    if existing is None:
                        session.add(
                            ChatCandidate(
                                username=ref.username,
                                invite_hash=ref.invite_hash,
                                title=ref.username or texts.INVITE_PENDING_TITLE,
                                status="pending",
                            )
                        )
                        await session.flush()
                await self._state(session, "candidate_links", str(raw.id))
            return len(rows)

    async def _enqueue_join(self, session, candidate: ChatCandidate, actor: int | None) -> None:
        job = await session.scalar(select(JoinJob).where(JoinJob.candidate_id == candidate.id))
        if job is None:
            session.add(JoinJob(candidate_id=candidate.id, approved_by=actor))
        elif actor is not None and job.status == "completed" and job.error == "approval_revoked":
            # Новое явное одобрение после отзыва роли; сетевых попыток здесь ещё не было.
            job.status, job.error, job.approved_by = "pending", None, actor
            job.attempts, job.due_at = 0, utc_now()
        # Терминальные/неоднозначные запросы не перезапускаются повторным callback.

    async def decide(self, actor: int, candidate_id: int, approve: bool) -> str:
        async with self.database.session() as session:
            await AdminSources(self.database, self.settings)._admin(session, actor)
            row = await session.get(ChatCandidate, candidate_id)
            if row is None:
                raise AccountError(texts.CANDIDATE_MISSING)
            job = await session.scalar(select(JoinJob).where(JoinJob.candidate_id == row.id))
            if job is not None and job.status == "processing":
                raise AccountError(texts.CANDIDATE_BUSY)
            if row.status not in {"pending", "approved"}:
                return row.status
            if approve:
                row.status = "approved"
                await self._enqueue_join(session, row, actor)
            else:
                row.status = "rejected"
                if job is not None:
                    job.status, job.error = "completed", "owner_rejected"
            return row.status

    async def candidates(self, actor: int, after: int = 0) -> tuple[dict, ...]:
        async with self.database.session() as session:
            await AdminSources(self.database, self.settings)._admin(session, actor)
            rows = (
                await session.scalars(
                    select(ChatCandidate)
                    .where(ChatCandidate.id > after)
                    .order_by(ChatCandidate.id)
                    .limit(self.settings.candidate_page_size)
                )
            ).all()
            return tuple(
                {
                    "id": row.id,
                    "title": row.title,
                    "status": row.status,
                    "error": row.error,
                    "participants": row.participants,
                    "msgs_per_day": row.msgs_per_day,
                    "category": row.category_hint,
                }
                for row in rows
            )

    async def recover_joins(self) -> int:
        async with self.database.session() as session:
            rows = (
                await session.scalars(
                    select(JoinJob).where(JoinJob.status == "processing", JoinJob.lease_until < utc_now())
                )
            ).all()
            for job in rows:
                job.status, job.error, job.token, job.lease_until = (
                    "ambiguous",
                    "lease_expired_manual_check",
                    None,
                    None,
                )
                row = await session.get(ChatCandidate, job.candidate_id)
                row.status, row.error = "failed", job.error
                attempt = await session.scalar(
                    select(JoinAttempt).where(JoinAttempt.job_id == job.id).order_by(JoinAttempt.id.desc()).limit(1)
                )
                if attempt:
                    attempt.outcome = "ambiguous"
            return len(rows)

    async def claim_join(self) -> JoinClaim | None:
        if self.settings.dry_run or self.hub.reader is None:
            return None
        for kind in ("join", "resolve", "dialogs", "history"):
            if await self.storage.cooldown(kind) > utc_now():
                return None
        async with self.database.session() as session:
            stopped = await session.get(ReaderState, "join_stopped")
            if stopped and stopped.detail:
                return None
            pause = await session.get(ReaderState, "join_pause")
            if pause and pause.detail > utc_now().isoformat():
                return None
            job = await session.scalar(
                select(JoinJob)
                .join(ChatCandidate)
                .where(
                    JoinJob.status.in_(("pending", "retry_wait")),
                    JoinJob.due_at <= utc_now(),
                    JoinJob.attempts < self.settings.join_max_attempts,
                    ChatCandidate.status == "approved",
                )
                .order_by(JoinJob.id)
                .limit(1)
            )
            if job is None:
                return None
            row = await session.get(ChatCandidate, job.candidate_id)
            if job.approved_by is None and not self.settings.auto_join:
                return None
            if job.approved_by is not None:
                owner = await session.scalar(select(User).where(User.tg_id == job.approved_by))
                if owner is None or not owner.is_admin or owner.is_banned:
                    job.status, job.error = "completed", "approval_revoked"
                    row.status, row.error = "pending", "approval_revoked"
                    return None
            job.status, job.token = "processing", uuid.uuid4().hex
            job.attempts += 1
            job.lease_until = utc_now() + timedelta(seconds=self.settings.join_lease_seconds)
            return JoinClaim(
                job.id, row.id, job.token, job.attempts, row.tg_chat_id, row.access_hash, row.username, row.invite_hash
            )

    async def finish(
        self, claim: JoinClaim, status: str, error: str | None = None, *, retry: bool = False, ambiguous: bool = False
    ) -> None:
        async with self.database.session() as session:
            job = await session.get(JoinJob, claim.id)
            if job.token != claim.token or job.status != "processing":
                return
            row = await session.get(ChatCandidate, claim.candidate_id)
            if retry and job.attempts < self.settings.join_max_attempts:
                job.status, row.status = "retry_wait", "approved"
                job.due_at = utc_now() + timedelta(
                    seconds=min(
                        self.settings.retry_initial_seconds * 2 ** max(0, job.attempts - 1),
                        self.settings.retry_max_seconds,
                    )
                )
            else:
                row.status = status
                job.status = "ambiguous" if ambiguous else "failed" if status == "failed" else "completed"
            job.error = row.error = error
            job.token = job.lease_until = None
            attempt = await session.scalar(select(JoinAttempt).where(JoinAttempt.token == claim.token))
            if attempt:
                attempt.outcome = error or status

    async def start_attempt(self, claim: JoinClaim, account_chats: int) -> bool:
        now = utc_now()
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        async with self.database.session() as session:
            job = await session.get(JoinJob, claim.id)
            if job.token != claim.token or job.status != "processing":
                return False
            pause = await session.get(ReaderState, "join_pause")
            stopped = await session.get(ReaderState, "join_stopped")
            if (pause and pause.detail > now.isoformat()) or (stopped and stopped.detail):
                job.status, job.token, job.lease_until = "retry_wait", None, None
                job.attempts -= 1
                job.due_at = now + timedelta(seconds=self.settings.join_pause_max_seconds)
                return False
            if job.approved_by is not None:
                owner = await session.scalar(select(User).where(User.tg_id == job.approved_by))
                if owner is None or not owner.is_admin or owner.is_banned:
                    job.status, job.error, job.token, job.lease_until = "completed", "approval_revoked", None, None
                    candidate = await session.get(ChatCandidate, claim.candidate_id)
                    candidate.status, candidate.error = "pending", "approval_revoked"
                    return False
            today = await session.scalar(
                select(func.count()).select_from(JoinAttempt).where(JoinAttempt.started_at >= midnight)
            )
            if account_chats >= self.settings.max_chats:
                await self._state(session, "join_stopped", "max_chats")
                job.status, job.error, job.token, job.lease_until = "pending", "max_chats", None, None
                job.attempts -= 1
                return False
            if today >= self.settings.daily_join_limit:
                job.status, job.token, job.lease_until = "retry_wait", None, None
                job.due_at = midnight + timedelta(days=1)
                job.attempts -= 1
                job.error = "daily_limit"
                return False
            session.add(JoinAttempt(job_id=job.id, token=claim.token, started_at=now))
            delay = self.settings.join_pause_min_seconds + secrets.randbelow(
                self.settings.join_pause_max_seconds - self.settings.join_pause_min_seconds + 1
            )
            await self._state(session, "join_pause", (now + timedelta(seconds=delay)).isoformat())
            return True

    async def resolve_claim(self, claim: JoinClaim):
        client = self.reader().client
        if claim.invite_hash:
            invite = await self.rpc(
                "resolve", lambda: client(functions.messages.CheckChatInviteRequest(claim.invite_hash))
            )
            if getattr(invite, "subscription_pricing", None) is not None:
                raise ValueError("paid_invite_forbidden")
            chat = getattr(invite, "chat", None)
            if chat is None:
                if (
                    not getattr(invite, "megagroup", False)
                    or getattr(invite, "broadcast", False)
                    or getattr(invite, "scam", False)
                    or getattr(invite, "fake", False)
                ):
                    raise ValueError("invite_not_group")
                return None
            return chat
        reference = (
            types.InputPeerChannel(int(str(claim.chat_id)[4:]), claim.access_hash)
            if claim.chat_id and claim.access_hash is not None and str(claim.chat_id).startswith("-100")
            else "@" + claim.username
            if claim.username
            else claim.chat_id
        )
        chat = await self.rpc("resolve", lambda: client.get_entity(reference))
        if claim.chat_id is not None and utils.get_peer_id(chat) != claim.chat_id:
            raise ValueError("identity_changed")
        return chat

    async def activate(self, claim: JoinClaim, chat, *, actor: int | None = None) -> None:
        if not writable_group(chat) or getattr(chat, "left", False):
            await self.finish(claim, "failed", "not_writable_group")
            return
        # Ограничение или признаки капчи требуют ручной проверки, не автоматического обхода.
        client = self.reader().client
        permissions = await self.rpc("resolve", lambda: client.get_permissions(chat, "me"))
        messages = await self.rpc(
            "history", lambda: client.get_messages(chat, limit=self.settings.activity_sample_limit)
        )
        captcha = not can_participate(permissions) or any(
            getattr(getattr(m, "sender", None), "bot", False)
            and any(
                word.casefold() in (getattr(m, "raw_text", "") or "").casefold() for word in self.settings.captcha_words
            )
            for m in messages
        )
        chat_id = utils.get_peer_id(chat)
        async with self.database.session() as session:
            job = await session.get(JoinJob, claim.id)
            if job.status != "processing" or job.token != claim.token:
                return
            approver = actor if actor is not None else job.approved_by
            if approver is not None:
                await AdminSources(self.database, self.settings)._admin(session, approver)
            elif not self.settings.auto_join:
                raise AccountError(texts.ADMIN_ONLY)
            row = await session.get(ChatCandidate, claim.candidate_id)
            other = await session.scalar(
                select(ChatCandidate.id).where(ChatCandidate.tg_chat_id == chat_id, ChatCandidate.id != row.id)
            )
            if other is not None:
                row.error = f"duplicate_candidate:{other}"
            else:
                row.tg_chat_id = chat_id
            row.title, row.username, row.access_hash = (
                chat.title,
                getattr(chat, "username", None),
                getattr(chat, "access_hash", None),
            )
            if not captcha:
                source = await Repository(session, self.settings).upsert_source(
                    chat_id, chat.title, username=row.username, access_hash=row.access_hash
                )
                # Повторно обнаруженный источник с ручным removed не включается автоматически.
                if source.status != "removed":
                    source.is_active, source.status = True, "active"
        await self.finish(claim, "needs_captcha" if captcha else "joined", "manual_captcha_check" if captcha else None)
        await self.hub.refresh()

    async def join_one(self) -> bool:
        claim = await self.claim_join()
        if claim is None:
            return False
        sent = False
        try:
            client = self.reader().client
            chat = await self.resolve_claim(claim)
            if chat is not None and not writable_group(chat):
                raise ValueError("not_group")
            # Уже участник: проверяем доступ и подключаем без повторного вступления.
            if chat is not None and not getattr(chat, "left", False):
                await self.activate(claim, chat)
                return True
            dialogs = await self.rpc("dialogs", lambda: client.get_dialogs(limit=self.settings.dialog_scan_limit))
            if len(dialogs) >= self.settings.dialog_scan_limit:
                await self.storage.heartbeat("join_stopped", "dialog_scan_incomplete")
                await self.finish(claim, "failed", "dialog_scan_incomplete")
                return True
            count = sum(
                isinstance(d.entity, (types.Chat, types.Channel, types.ChatForbidden, types.ChannelForbidden))
                for d in dialogs
            )
            if not await self.start_attempt(claim, count):
                return False
            request = (
                functions.messages.ImportChatInviteRequest(claim.invite_hash)
                if claim.invite_hash
                else functions.channels.JoinChannelRequest(chat)
            )
            # Сначала commit попытки; затем сеть. Тайм-аут не повторяем автоматически.
            sent = True
            try:
                result = await self.rpc("join", lambda: client(request))
            except errors.UserAlreadyParticipantError:
                result = None
            if claim.invite_hash and result is None:
                chat = await self.resolve_claim(claim)
                if chat is None:
                    await self.finish(claim, "failed", "membership_unverified", ambiguous=True)
                    return True
            elif claim.invite_hash:
                joined = [c for c in result.chats if writable_group(c)]
                if len(joined) != 1:
                    await self.finish(claim, "failed", "join_result_unverified", ambiguous=True)
                    return True
                chat = joined[0]
            else:
                chat = await self.rpc("resolve", lambda: client.get_entity(chat))
            await self.activate(claim, chat)
        except errors.InviteRequestSentError:
            await self.finish(claim, "pending_approval", "InviteRequestSentError")
        except errors.ChannelsTooMuchError:
            await self.storage.heartbeat("join_stopped", "ChannelsTooMuchError")
            await self.finish(claim, "failed", "ChannelsTooMuchError")
        except errors.FloodWaitError as error:
            await self.storage.set_cooldown("join", error.seconds)
            await self.finish(claim, "failed", "FloodWaitError", retry=True)
        except ReaderCooldown:
            await self.finish(claim, "failed", "cooldown", retry=True)
        except ReaderAuthorizationError:
            await self.finish(claim, "failed", "reader_offline", retry=True)
        except AccountError:
            await self.finish(claim, "failed", "approval_revoked", ambiguous=sent)
        except (
            errors.InviteHashExpiredError,
            errors.InviteHashInvalidError,
            errors.ChannelPrivateError,
            errors.UsernameNotOccupiedError,
            errors.UserBannedInChannelError,
            ValueError,
        ) as error:
            await self.finish(claim, "failed", type(error).__name__)
        except (OSError, errors.RPCError) as error:
            await self.finish(claim, "failed", type(error).__name__, retry=not sent, ambiguous=sent)
        return True

    async def verify_candidate(self, actor: int, candidate_id: int) -> str:
        """Только чтение членства/капчи: ни JoinChannel, ни ImportInvite здесь нет."""
        async with self.database.session() as session:
            await AdminSources(self.database, self.settings)._admin(session, actor)
            row = await session.get(ChatCandidate, candidate_id)
            job = await session.scalar(select(JoinJob).where(JoinJob.candidate_id == candidate_id))
            if row is None or job is None:
                raise AccountError(texts.CANDIDATE_MISSING)
            if job.status == "processing":
                raise AccountError(texts.CANDIDATE_BUSY)
            if row.status not in {"pending_approval", "needs_captcha", "failed"}:
                return row.status
            previous_status, previous_error = row.status, row.error
            was_ambiguous = job.status == "ambiguous"
            job.status, job.token = "processing", uuid.uuid4().hex
            job.lease_until = utc_now() + timedelta(seconds=self.settings.join_lease_seconds)
            claim = JoinClaim(
                job.id, row.id, job.token, job.attempts, row.tg_chat_id, row.access_hash, row.username, row.invite_hash
            )
        try:
            chat = await self.resolve_claim(claim)
            if chat is None or getattr(chat, "left", False):
                await self.finish(claim, previous_status, previous_error, ambiguous=was_ambiguous)
                return previous_status
            # Пока выполнялась сеть, роль могла быть отозвана. Повторная проверка перед записью.
            await AdminSources(self.database, self.settings).require_admin(actor)
            await self.activate(claim, chat, actor=actor)
            async with self.database.session() as session:
                return (await session.get(ChatCandidate, candidate_id)).status
        except (errors.RPCError, OSError, ValueError, ReaderCooldown, ReaderAuthorizationError, AccountError):
            await self.finish(claim, previous_status, previous_error, ambiguous=was_ambiguous)
            raise AccountError(texts.CANDIDATE_CHECK_WAIT) from None

    async def review_sources(self) -> None:
        async with self.database.session() as session:
            marker = await session.get(ReaderState, "source_review")
            if marker and marker.updated_at + timedelta(days=self.settings.source_review_interval_days) > utc_now():
                return
            cutoff = utc_now() - timedelta(days=self.settings.source_review_days)
            rows = (
                await session.execute(
                    select(SourceChat.id, SourceChat.title, func.count(Lead.id))
                    .outerjoin(Lead, (Lead.source_chat_id == SourceChat.id) & (Lead.created_at >= cutoff))
                    .where(SourceChat.is_active.is_(True))
                    .group_by(SourceChat.id, SourceChat.title)
                )
            ).all()
            # Только рекомендация. Нет leave/delete/outbox. Новые lh_leads ещё не наполняются.
            report = {
                "days": self.settings.source_review_days,
                "no_leads": [{"id": identity, "title": title} for identity, title, total in rows if not total],
                "limitations": "only_lh_leads; classifier_not_active; zero_not_proof_of_bad_source",
            }
            await self._state(session, "source_review", json.dumps(report, ensure_ascii=False))
            unavailable = (await session.scalars(select(SourceChat).where(SourceChat.status == "unavailable"))).all()
            await self._state(session, "source_alerts", json.dumps([row.id for row in unavailable]))

    async def loop(self) -> None:
        while True:
            await self.recover_joins()
            await self.collect_links()
            await self.schedule_discovery()
            await self.review_sources()
            await self.discover_one()
            await self.join_one()
            await self.storage.heartbeat("growth")
            await asyncio.sleep(self.settings.growth_poll_seconds)
