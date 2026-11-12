"""Этап 3б: FakeClient, временная SQLite в памяти, никаких запросов Telegram."""

import asyncio
import json
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, EditMessageReplyMarkup, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as BotUser
from sqlalchemy import func, select
from telethon import errors, functions, types, utils
from telethon.tl.custom.participantpermissions import ParticipantPermissions

from app.bot.accounts import AccountError, Accounts
from app.bot.keyboards import CandidateData, CandidatesPageData
from app.bot.router import create_router
from app.config import Settings
from app.db.models import (
    ChatCandidate,
    DiscoveryRun,
    JoinAttempt,
    JoinJob,
    ReaderState,
    SourceChat,
    User,
    utc_now,
)
from app.db.repo import Repository
from app.db.session import Database
from app.discovery import Growth, can_participate, writable_group
from app.pipeline.ingestion import IncomingMessage, Ingestion
from app.userbot.client import ReaderHub, ReaderRPC


def group(number=1234, **kwargs):
    fields = dict(
        id=number,
        title="Бизнес <Москва>",
        photo=types.ChatPhotoEmpty(),
        date=utc_now(),
        megagroup=True,
        broadcast=False,
        username=f"business_{number}",
        access_hash=17,
        participants_count=500,
        left=True,
    )
    fields.update(kwargs)
    return types.Channel(**fields)


def sample(count=10, authors=5, old=False):
    return [
        SimpleNamespace(
            id=i + 1,
            date=utc_now() - timedelta(days=8 if old else 0),
            from_id=types.PeerUser(i % authors + 1),
            sender=None,
            action=None,
            raw_text="Обсуждение проекта",
        )
        for i in range(count)
    ]


class FakeClient:
    def __init__(self):
        self.chat = group()
        self.search = [self.chat]
        self.messages = sample()
        self.calls = []
        self.failure = None
        self.dialogs = []
        self.permissions = True
        self.joined = False

    async def __call__(self, request):
        self.calls.append(request)
        if isinstance(request, functions.contacts.SearchRequest):
            return SimpleNamespace(chats=self.search)
        if isinstance(request, functions.channels.GetFullChannelRequest):
            return SimpleNamespace(full_chat=SimpleNamespace(participants_count=500))
        if isinstance(request, functions.messages.CheckChatInviteRequest):
            return SimpleNamespace(chat=self.entity() if self.joined else None, megagroup=True, broadcast=False)
        if isinstance(request, (functions.channels.JoinChannelRequest, functions.messages.ImportChatInviteRequest)):
            if self.failure:
                raise self.failure
            self.joined = True
            return SimpleNamespace(chats=[self.entity()])
        raise AssertionError(type(request).__name__)

    def entity(self):
        if self.joined:
            return group(self.chat.id, left=False)
        return self.chat

    async def get_entity(self, reference):
        return self.entity()

    async def get_messages(self, entity, **kwargs):
        return self.messages

    async def get_dialogs(self, **kwargs):
        return self.dialogs

    async def get_permissions(self, entity, who):
        if self.permissions:
            participant = types.ChannelParticipant(user_id=9, date=utc_now())
        else:
            participant = types.ChannelParticipantBanned(
                peer=types.PeerUser(9),
                kicked_by=1,
                date=utc_now(),
                banned_rights=types.ChatBannedRights(until_date=None, send_messages=True),
                left=False,
            )
        return ParticipantPermissions(participant, chat=False)


class GrowthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cfg = Settings(
            _env_file=None,
            database_url="sqlite+aiosqlite:///:memory:",
            admin_ids=[9],
            telegram_session_paths=[Path("fake.session")],
            reader_rpc_interval_seconds=0,
            discovery_queries={"web_dev": ("фриланс сайты",)},
        )
        self.db = Database(self.cfg, testing=True, read_only=False)
        await self.db.create_test_schema()
        await Accounts(self.db, self.cfg).register(9, None)
        self.storage = Ingestion(self.db, self.cfg)
        self.client = FakeClient()
        self.hub = ReaderHub()
        self.hub.reader = SimpleNamespace(
            client=self.client, rpc=ReaderRPC(self.storage, self.cfg), refresh=AsyncMock()
        )
        self.growth = Growth(self.db, self.cfg, self.hub)

    async def asyncTearDown(self):
        await self.db.close()

    async def candidate(self, *, private=False):
        async with self.db.session() as session:
            row = ChatCandidate(
                title="Кандидат",
                tg_chat_id=None if private else utils.get_peer_id(self.client.chat),
                username=None if private else self.client.chat.username,
                access_hash=None if private else self.client.chat.access_hash,
                invite_hash="Private_hash" if private else None,
            )
            session.add(row)
            await session.flush()
            return row.id

    async def row(self, cls, identity):
        async with self.db.session() as session:
            return await session.get(cls, identity)

    async def job(self, candidate):
        async with self.db.session() as session:
            return await session.scalar(select(JoinJob).where(JoinJob.candidate_id == candidate))

    async def clear_pause(self):
        async with self.db.session() as session:
            state = await session.get(ReaderState, "join_pause")
            if state:
                state.detail = (utc_now() - timedelta(seconds=1)).isoformat()
            for job in (await session.scalars(select(JoinJob))).all():
                job.due_at = utc_now() - timedelta(seconds=1)

    async def test_real_telethon_permission_objects(self):
        self.assertTrue(can_participate(await self.client.get_permissions(None, "me")))
        self.client.permissions = False
        self.assertFalse(can_participate(await self.client.get_permissions(None, "me")))
        self.assertFalse(
            can_participate(ParticipantPermissions(types.ChannelParticipantLeft(peer=types.PeerUser(9)), chat=False))
        )

    async def test_search_active_groups_and_stable_id_dedup(self):
        await self.growth.request_discovery(9, "web_dev")
        self.assertTrue(await self.growth.discover_one())
        async with self.db.session() as session:
            rows = (await session.scalars(select(ChatCandidate))).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].tg_chat_id, utils.get_peer_id(self.client.chat))
            self.assertEqual(rows[0].status, "pending")
            self.assertIsNone(await session.scalar(select(JoinJob)))
        self.client.chat.username = "renamed_group"
        await self.growth.request_discovery(9, "web_dev")
        await self.growth.discover_one()
        async with self.db.session() as session:
            self.assertEqual(await session.scalar(select(func.count()).select_from(ChatCandidate)), 1)
        self.assertTrue(all(isinstance(r, functions.contacts.SearchRequest) for r in self.client.calls))
        self.assertEqual(self.client.calls[0].limit, 50)

    async def test_discovery_rejects_channels_scam_fake_small_readonly(self):
        rights = types.ChatBannedRights(until_date=None, send_messages=True)
        self.client.search = [
            group(1, broadcast=True, megagroup=False),
            group(2, scam=True),
            group(3, fake=True),
            group(4, participants_count=200),
            group(5, default_banned_rights=rights),
        ]
        await self.growth.request_discovery(9, "web_dev")
        await self.growth.discover_one()
        self.assertEqual(await self.growth.candidates(9), ())
        self.assertFalse(writable_group(types.User(id=5)))

    async def test_discovery_checks_activity_age_and_unique_authors(self):
        for messages in (sample(old=True), sample(count=9), sample(authors=1)):
            self.client.messages = messages
            await self.growth.request_discovery(9, "web_dev")
            await self.growth.discover_one()
            self.assertEqual(await self.growth.candidates(9), ())

    async def test_missing_participant_count_uses_full_channel(self):
        self.client.chat.participants_count = None
        await self.growth.request_discovery(9, "web_dev")
        await self.growth.discover_one()
        self.assertTrue(any(isinstance(r, functions.channels.GetFullChannelRequest) for r in self.client.calls))
        self.assertEqual(len(await self.growth.candidates(9)), 1)

    async def test_discovery_errors_bounded_and_daily_schedule(self):
        self.client.get_messages = AsyncMock(side_effect=OSError())
        await self.growth.request_discovery(9, "web_dev")
        for _ in range(self.cfg.discovery_max_attempts):
            await self.growth.discover_one()
            async with self.db.session() as session:
                (await session.get(DiscoveryRun, "web_dev")).due_at = utc_now() - timedelta(seconds=1)
        self.assertEqual((await self.row(DiscoveryRun, "web_dev")).status, "failed")
        await self.growth.schedule_discovery()
        self.assertEqual((await self.row(DiscoveryRun, "web_dev")).status, "failed")
        await self.growth.request_discovery(9, "web_dev")
        self.client.get_messages = AsyncMock(return_value=sample())
        await self.growth.discover_one()
        await self.growth.schedule_discovery()
        self.assertEqual((await self.row(DiscoveryRun, "web_dev")).status, "completed")
        async with self.db.session() as session:
            (await session.get(DiscoveryRun, "web_dev")).updated_at = utc_now() - timedelta(days=2)
        await self.growth.schedule_discovery()
        self.assertEqual((await self.row(DiscoveryRun, "web_dev")).status, "pending")

    async def test_links_are_queued_once_without_resolve_or_join(self):
        async with self.db.session() as session:
            source = await Repository(session, self.cfg).upsert_source(-10099999, "Источник")
            source_id = source.id
        text = "Ссылка https://t.me/business_1234/55 и t.me/+Private_hash, снова t.me/business_1234"
        await self.storage.ingest(source_id, IncomingMessage(-10099999, 1, text))
        self.assertEqual(await self.growth.collect_links(), 1)
        self.assertEqual(await self.growth.collect_links(), 0)
        rows = await self.growth.candidates(9)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["status"] == "pending" for row in rows))
        self.assertEqual(self.client.calls, [])
        # Resolve позже обновляет предварительного кандидата вместо второго username-дубля.
        await self.growth.save_candidate(self.client.chat, "web_dev", 500, 10)
        self.assertEqual(len(await self.growth.candidates(9)), 2)

    async def test_admin_decision_is_idempotent_and_dry_run_blocks_join(self):
        candidate = await self.candidate()
        with self.assertRaises(AccountError):
            await self.growth.decide(10, candidate, True)
        await self.growth.decide(9, candidate, True)
        await self.growth.decide(9, candidate, True)
        self.assertFalse(await self.growth.join_one())
        self.assertEqual(self.client.calls, [])
        async with self.db.session() as session:
            self.assertEqual(await session.scalar(select(func.count()).select_from(JoinJob)), 1)
        self.assertEqual(await self.growth.decide(9, candidate, False), "rejected")
        self.assertEqual(await self.growth.decide(9, candidate, True), "rejected")

    async def test_auto_join_is_opt_in_only_for_qualified_discovery(self):
        self.cfg.auto_join = True
        candidate = await self.growth.save_candidate(self.client.chat, "web_dev", 500, 10)
        self.assertEqual((await self.row(ChatCandidate, candidate)).status, "approved")
        self.assertIsNone((await self.job(candidate)).approved_by)
        self.assertFalse(await self.growth.join_one())  # DRY_RUN всё ещё true.

    async def test_public_join_activates_source_and_persists_pause(self):
        self.cfg.dry_run = False
        candidate = await self.candidate()
        await self.growth.decide(9, candidate, True)
        self.assertTrue(await self.growth.join_one())
        self.assertEqual((await self.row(ChatCandidate, candidate)).status, "joined")
        self.assertEqual((await self.job(candidate)).status, "completed")
        self.assertEqual(len(await self.storage.active_sources()), 1)
        self.hub.reader.refresh.assert_awaited_once()
        async with self.db.session() as session:
            self.assertEqual(await session.scalar(select(func.count()).select_from(JoinAttempt)), 1)
            pause = await session.get(ReaderState, "join_pause")
            self.assertGreater(pause.detail, utc_now().isoformat())
        self.assertEqual(sum(isinstance(r, functions.channels.JoinChannelRequest) for r in self.client.calls), 1)
        self.assertFalse(await self.growth.join_one())

    async def test_private_join_and_already_member(self):
        self.cfg.dry_run = False
        candidate = await self.candidate(private=True)
        await self.growth.decide(9, candidate, True)
        self.assertTrue(await self.growth.join_one())
        self.assertEqual((await self.row(ChatCandidate, candidate)).status, "joined")
        self.assertTrue(any(isinstance(r, functions.messages.ImportChatInviteRequest) for r in self.client.calls))

    async def test_existing_member_does_not_spend_join_quota(self):
        self.cfg.dry_run = False
        self.client.joined = True
        candidate = await self.candidate()
        await self.growth.decide(9, candidate, True)
        await self.growth.join_one()
        async with self.db.session() as session:
            self.assertEqual(await session.scalar(select(func.count()).select_from(JoinAttempt)), 0)
        self.assertEqual(self.client.calls, [])

    async def test_daily_limit_and_max_chats_are_persistent(self):
        self.cfg.dry_run = False
        self.cfg.daily_join_limit = 0
        candidate = await self.candidate()
        await self.growth.decide(9, candidate, True)
        self.assertFalse(await self.growth.join_one())
        self.assertEqual((await self.job(candidate)).error, "daily_limit")
        self.assertFalse(any(isinstance(r, functions.channels.JoinChannelRequest) for r in self.client.calls))
        await self.clear_pause()
        self.cfg.daily_join_limit = 10
        self.cfg.max_chats = 1
        self.client.dialogs = [SimpleNamespace(entity=group(9999))]
        self.assertFalse(await self.growth.join_one())
        self.assertEqual((await self.row(ReaderState, "join_stopped")).detail, "max_chats")
        self.assertEqual((await self.job(candidate)).attempts, 0)
        self.assertIsNone(await Growth(self.db, self.cfg, self.hub).claim_join())

    async def test_incomplete_dialog_scan_fails_closed(self):
        self.cfg.dry_run = False
        self.cfg.dialog_scan_limit = 1
        self.client.dialogs = [SimpleNamespace(entity=types.User(id=4))]
        candidate = await self.candidate()
        await self.growth.decide(9, candidate, True)
        await self.growth.join_one()
        self.assertEqual((await self.row(ReaderState, "join_stopped")).detail, "dialog_scan_incomplete")
        self.assertFalse(any(isinstance(r, functions.channels.JoinChannelRequest) for r in self.client.calls))

    async def test_telegram_join_errors_are_terminal_or_explicit_retry(self):
        self.cfg.dry_run = False
        for index, (error, status) in enumerate(
            (
                (errors.InviteRequestSentError(request=None), "pending_approval"),
                (errors.InviteHashExpiredError(request=None), "failed"),
                (errors.InviteHashInvalidError(request=None), "failed"),
                (errors.ChannelPrivateError(request=None), "failed"),
                (errors.UsernameNotOccupiedError(request=None), "failed"),
                (errors.UserBannedInChannelError(request=None), "failed"),
            ),
            1,
        ):
            self.client.chat = group(1000 + index)
            self.client.failure = error
            candidate = await self.candidate()
            await self.growth.decide(9, candidate, True)
            await self.clear_pause()
            await self.growth.join_one()
            row = await self.row(ChatCandidate, candidate)
            self.assertEqual((row.status, row.error), (status, type(error).__name__))
            self.assertEqual(await self.growth.decide(9, candidate, True), status)

    async def test_floodwait_prevents_further_rpc_until_due(self):
        self.cfg.dry_run = False
        self.client.failure = errors.FloodWaitError(request=None, capture=60)
        candidate = await self.candidate()
        await self.growth.decide(9, candidate, True)
        await self.growth.join_one()
        self.assertEqual((await self.job(candidate)).status, "retry_wait")
        await self.clear_pause()
        calls = len(self.client.calls)
        self.assertFalse(await self.growth.join_one())
        self.assertEqual(len(self.client.calls), calls)
        self.assertGreater(await self.storage.cooldown("join"), utc_now())

    async def test_channels_too_much_stops_all_jobs(self):
        self.cfg.dry_run = False
        self.client.failure = errors.ChannelsTooMuchError(request=None)
        candidate = await self.candidate()
        await self.growth.decide(9, candidate, True)
        await self.growth.join_one()
        self.assertEqual((await self.row(ReaderState, "join_stopped")).detail, "ChannelsTooMuchError")
        self.assertIsNone(await self.growth.claim_join())

    async def test_timeout_and_expired_lease_never_repeat_join(self):
        self.cfg.dry_run = False
        self.client.failure = TimeoutError()
        candidate = await self.candidate()
        await self.growth.decide(9, candidate, True)
        await self.growth.join_one()
        self.assertEqual((await self.job(candidate)).status, "ambiguous")
        await self.clear_pause()
        self.assertFalse(await Growth(self.db, self.cfg, self.hub).join_one())
        self.client.chat = group(5678)
        second = await self.candidate()
        await self.growth.decide(9, second, True)
        claim = await self.growth.claim_join()
        async with self.db.session() as session:
            (await session.get(JoinJob, claim.id)).lease_until = utc_now() - timedelta(seconds=1)
        self.assertEqual(await self.growth.recover_joins(), 1)
        self.assertEqual((await self.job(second)).status, "ambiguous")
        await self.growth.finish(claim, "joined")
        self.assertEqual((await self.job(second)).status, "ambiguous")

    async def test_resolve_network_errors_have_bounded_attempts(self):
        self.cfg.dry_run = False
        self.client.get_entity = AsyncMock(side_effect=OSError())
        candidate = await self.candidate()
        await self.growth.decide(9, candidate, True)
        for _ in range(self.cfg.join_max_attempts):
            await self.growth.join_one()
            await self.clear_pause()
        self.assertEqual((await self.job(candidate)).status, "failed")
        self.assertFalse(await self.growth.join_one())

    async def test_captcha_or_restricted_account_not_activated(self):
        self.cfg.dry_run = False
        self.client.permissions = False
        candidate = await self.candidate()
        await self.growth.decide(9, candidate, True)
        await self.growth.join_one()
        self.assertEqual((await self.row(ChatCandidate, candidate)).status, "needs_captcha")
        self.assertEqual(await self.storage.active_sources(), ())

    async def test_reassigned_username_and_paid_invites_fail_closed(self):
        self.cfg.dry_run = False
        candidate = await self.candidate()
        self.client.get_entity = AsyncMock(return_value=group(99999))
        await self.growth.decide(9, candidate, True)
        await self.growth.join_one()
        self.assertEqual((await self.row(ChatCandidate, candidate)).status, "failed")
        self.assertEqual(self.client.calls, [])

    async def test_manual_check_after_captcha_does_not_join_again(self):
        self.cfg.dry_run = False
        self.client.permissions = False
        candidate = await self.candidate()
        await self.growth.decide(9, candidate, True)
        await self.growth.join_one()
        joins = sum(isinstance(r, functions.channels.JoinChannelRequest) for r in self.client.calls)
        self.client.permissions = True
        self.assertEqual(await self.growth.verify_candidate(9, candidate), "joined")
        self.assertEqual(sum(isinstance(r, functions.channels.JoinChannelRequest) for r in self.client.calls), joins)
        self.assertEqual(len(await self.storage.active_sources()), 1)

    async def test_manual_check_pending_approval_is_read_only(self):
        self.cfg.dry_run = False
        self.client.failure = errors.InviteRequestSentError(request=None)
        candidate = await self.candidate(private=True)
        await self.growth.decide(9, candidate, True)
        await self.growth.join_one()
        before = len([r for r in self.client.calls if isinstance(r, functions.messages.ImportChatInviteRequest)])
        self.assertEqual(await self.growth.verify_candidate(9, candidate), "pending_approval")
        self.client.joined, self.client.failure = True, None
        self.assertEqual(await self.growth.verify_candidate(9, candidate), "joined")
        self.assertEqual(
            len([r for r in self.client.calls if isinstance(r, functions.messages.ImportChatInviteRequest)]), before
        )

    async def test_paid_private_invite_is_not_imported(self):
        self.cfg.dry_run = False
        candidate = await self.candidate(private=True)
        await self.growth.decide(9, candidate, True)
        original = self.client.__class__.__call__

        async def paid(client, request):
            if isinstance(request, functions.messages.CheckChatInviteRequest):
                return SimpleNamespace(subscription_pricing=object())
            return await original(client, request)

        from unittest.mock import patch

        with patch.object(FakeClient, "__call__", new=paid):
            await self.growth.join_one()
        self.assertEqual((await self.row(ChatCandidate, candidate)).status, "failed")
        self.assertFalse(any(isinstance(r, functions.messages.ImportChatInviteRequest) for r in self.client.calls))

    async def test_already_participant_error_counts_as_success(self):
        self.cfg.dry_run = False
        candidate = await self.candidate()
        await self.growth.decide(9, candidate, True)
        original = self.client.__class__.__call__

        async def already(client, request):
            if isinstance(request, functions.channels.JoinChannelRequest):
                client.joined = True
                raise errors.UserAlreadyParticipantError(request=None)
            return await original(client, request)

        from unittest.mock import patch

        with patch.object(FakeClient, "__call__", new=already):
            await self.growth.join_one()
        self.assertEqual((await self.row(ChatCandidate, candidate)).status, "joined")

    async def test_role_revocation_blocks_queued_join(self):
        self.cfg.dry_run = False
        candidate = await self.candidate()
        await self.growth.decide(9, candidate, True)
        async with self.db.session() as session:
            (await session.scalar(select(User).where(User.tg_id == 9))).is_admin = False
        self.assertFalse(await self.growth.join_one())
        self.assertEqual(self.client.calls, [])
        self.assertEqual((await self.job(candidate)).error, "approval_revoked")

    async def test_global_pause_checked_again_before_network_mutation(self):
        self.cfg.dry_run = False
        candidate = await self.candidate()
        await self.growth.decide(9, candidate, True)
        claim = await self.growth.claim_join()
        await self.storage.heartbeat("join_pause", (utc_now() + timedelta(minutes=1)).isoformat())
        self.assertFalse(await self.growth.start_attempt(claim, 0))
        self.assertEqual((await self.job(candidate)).attempts, 0)
        self.assertEqual(self.client.calls, [])

    async def test_weekly_report_does_not_disable_or_delete_sources(self):
        async with self.db.session() as session:
            await Repository(session, self.cfg).upsert_source(-1009999, "Бизнес")
        await self.growth.review_sources()
        report = await self.row(ReaderState, "source_review")
        self.assertEqual(len(json.loads(report.detail)["no_leads"]), 1)
        before = report.updated_at
        await self.growth.review_sources()
        self.assertEqual((await self.row(ReaderState, "source_review")).updated_at, before)
        self.assertEqual(len(await self.storage.active_sources()), 1)
        self.assertEqual(self.client.calls, [])

    async def test_loop_stops_cleanly_and_persists_heartbeat(self):
        self.growth.discover_one = AsyncMock(return_value=False)
        self.growth.join_one = AsyncMock(return_value=False)
        task = asyncio.create_task(self.growth.loop())
        for _ in range(100):
            await asyncio.sleep(0.01)
            if await self.row(ReaderState, "growth"):
                break
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNotNone(await self.row(ReaderState, "growth"))

    async def test_dispatcher_admin_commands_callbacks_pagination(self):
        calls = []

        class Transport(BaseSession):
            async def close(self):
                pass

            async def make_request(self, bot, method, timeout=None):
                calls.append(method)
                if isinstance(method, SendMessage):
                    return Message(
                        message_id=len(calls),
                        date=utc_now(),
                        chat=Chat(id=int(method.chat_id), type="private"),
                        text=method.text,
                    )
                if isinstance(method, (AnswerCallbackQuery, EditMessageReplyMarkup)):
                    return True
                raise AssertionError(type(method).__name__)

            async def stream_content(self, *args, **kwargs):
                raise AssertionError("network forbidden")
                yield b""

        bot = Bot("123456789:TEST", session=Transport())
        dp = Dispatcher(reader_hub=self.hub)
        dp.include_router(create_router(self.db, self.cfg))
        sequence = 0

        async def command(text, actor=9):
            nonlocal sequence
            sequence += 1
            msg = Message(
                message_id=sequence,
                date=utc_now(),
                chat=Chat(id=actor, type="private"),
                from_user=BotUser(id=actor, is_bot=False, first_name="Тест"),
                text=text,
            )
            await dp.feed_update(bot, Update(update_id=sequence, message=msg))

        async def callback(data, actor=9, chat_id=None):
            nonlocal sequence
            sequence += 1
            msg = Message(
                message_id=1,
                date=utc_now(),
                chat=Chat(id=chat_id or actor, type="private"),
                from_user=BotUser(id=bot.id, is_bot=True, first_name="Бот"),
                text="Кандидаты",
            )
            cb = CallbackQuery(
                id=str(sequence),
                from_user=BotUser(id=actor, is_bot=False, first_name="Тест"),
                message=msg,
                data=data,
                chat_instance="test",
            )
            await dp.feed_update(bot, Update(update_id=sequence, callback_query=cb))

        candidate = await self.candidate()
        try:
            await command("/discover web_dev", actor=10)
            self.assertIsNone(await self.row(DiscoveryRun, "web_dev"))
            await command("/discover web_dev")
            self.assertIsNotNone(await self.row(DiscoveryRun, "web_dev"))
            await command("/candidates")
            cards = [c for c in calls if isinstance(c, SendMessage) and c.reply_markup]
            self.assertEqual(len(cards), 1)
            await callback(CandidateData(candidate_id=candidate, approve=True).pack(), actor=10)
            self.assertEqual((await self.row(ChatCandidate, candidate)).status, "pending")
            await callback(CandidateData(candidate_id=candidate, approve=True).pack())
            self.assertEqual((await self.row(ChatCandidate, candidate)).status, "approved")
            await callback(CandidatesPageData(after=0).pack())
            await command("/source_review")
            await command("/join_resume")
            async with self.db.session() as session:
                source = await Repository(session, self.cfg).upsert_source(-1009999, "Бизнес")
                source_id = source.id
            await command(f"/chat_geo {source_id} Москва")
            self.assertEqual((await self.row(SourceChat, source_id)).geo, "Москва")
            self.assertTrue(all(c.chat_id in (9, 10) for c in calls if isinstance(c, SendMessage)))
        finally:
            await dp.storage.close()
            await bot.session.close()


if __name__ == "__main__":
    unittest.main()
