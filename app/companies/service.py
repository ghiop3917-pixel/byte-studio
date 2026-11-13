"""Компании — отдельная книга потенциальных клиентов, без ложных входящих заказов."""

import asyncio
import csv
import hashlib
import io
import json
from datetime import timedelta

import httpx
from sqlalchemy import select

from app.bot import texts
from app.bot.accounts import AccountError
from app.companies.fetch import PageUnavailable, PublicPages
from app.companies.parsers import PROVIDERS, Company, canonical, niche_matches, parse_page, search_url, search_value
from app.config import Settings
from app.db.models import CompanyRecord, CompanyScan, CompanySearchResult, CompanySnapshot, utc_now
from app.db.session import Database
from app.services_sources import AdminSources


def prospect(company: Company) -> bool:
    return bool(
        company.in_scope
        and bool(company.city)
        and company.name.strip()
        and company.description.strip()
        and company.phones
        and company.website_state == "not_listed"
        and not company.websites
    )


def identity(company: Company) -> str | None:
    if not company.phones:
        return None
    return hashlib.sha256(
        json.dumps(
            [company.city.casefold(), company.name.strip().casefold(), company.phones[0]], ensure_ascii=False
        ).encode()
    ).hexdigest()


def csv_bytes(rows: list[dict]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    columns = (
        "name",
        "description",
        "city",
        "address",
        "phones",
        "telegram",
        "max_links",
        "source_url",
        "website_state",
    )
    writer.writerow(columns)
    for row in rows:
        values = []
        for key in columns:
            value = str(row.get(key, "") or "")
            if value.lstrip().startswith(("=", "+", "-", "@")):
                value = "'" + value
            values.append(value)
        writer.writerow(values)
    return stream.getvalue().encode("utf-8-sig")


class Companies:
    def __init__(self, database: Database, settings: Settings, pages: PublicPages | None = None):
        self.database, self.settings = database, settings
        self.pages = pages or PublicPages(settings)
        self.owned = pages is None
        self.wakeup = asyncio.Event()
        self.progress = asyncio.Event()
        self.running = False

    async def admin(self, session, actor):
        await AdminSources(self.database, self.settings)._admin(session, actor)

    async def enqueue(
        self, actor: int, city: str | None = None, niche: str | None = None, *, refresh: bool = False
    ) -> int:
        try:
            cities = (search_value(city, 64),) if city is not None else self.settings.company_cities
            sector = search_value(niche, 100) if niche is not None else ""
        except ValueError:
            raise AccountError(texts.COMPANIES_BAD_SCOPE) from None
        async with self.database.session() as session:
            await self.admin(session, actor)
            count = 0
            requested_at = utc_now()
            for provider in PROVIDERS:
                for selected_city in cities:
                    row = await session.scalar(
                        select(CompanyScan).where(
                            CompanyScan.provider == provider,
                            CompanyScan.city == selected_city,
                            CompanyScan.niche == sector,
                            CompanyScan.requested_by == actor,
                        )
                    )
                    queries = (sector,) if sector else self.settings.company_queries
                    if row is None:
                        session.add(
                            CompanyScan(
                                provider=provider,
                                city=selected_city,
                                niche=sector,
                                queries=json.dumps(queries, ensure_ascii=False),
                                requested_by=actor,
                                requested_at=requested_at,
                            )
                        )
                        count += 1
                    elif row.status in {"completed", "failed", "paused"}:
                        fresh = (
                            row.status == "completed"
                            and row.updated_at + timedelta(seconds=self.settings.company_cache_seconds) > utc_now()
                        )
                        if fresh and not refresh:
                            continue
                        row.status, row.query_index, row.page, row.links, row.link_index, row.seen = (
                            "pending",
                            0,
                            1,
                            "[]",
                            0,
                            "[]",
                        )
                        row.attempts, row.due_at, row.error, row.requested_at = 0, utc_now(), None, requested_at
                        row.queries = json.dumps(queries, ensure_ascii=False)
                        count += 1
            self.wakeup.set()
            return count

    async def save(self, company: Company, scan_id: int | None = None) -> int | None:
        if not company.city:
            return None
        async with self.database.session() as session:
            row = await session.scalar(
                select(CompanyRecord).where(
                    CompanyRecord.provider == company.provider, CompanyRecord.external_id == company.external_id
                )
            )
            key = identity(company)
            fingerprint = company.fingerprint()
            duplicate = (
                await session.scalar(
                    select(CompanyRecord.id)
                    .where(CompanyRecord.identity_key == key)
                    .order_by(CompanyRecord.id)
                    .limit(1)
                )
                if key
                else None
            )
            if row is None:
                row = CompanyRecord(provider=company.provider, external_id=company.external_id)
                session.add(row)
            changed = row.fingerprint != fingerprint
            for field in ("name", "description", "city", "address", "source_url", "website_state", "in_scope"):
                setattr(row, field, getattr(company, field))
            for field in ("phones", "telegram", "max_links", "websites"):
                setattr(row, field, json.dumps(getattr(company, field), ensure_ascii=False))
            row.phone = company.phones[0] if company.phones else None
            row.identity_key = key
            row.fingerprint, row.checked_at = fingerprint, utc_now()
            row.possible_duplicate_of = duplicate if duplicate and duplicate != row.id else None
            if scan_id is not None:
                row.in_scope = bool(company.city)
            await session.flush()
            if scan_id is not None:
                relation = await session.scalar(
                    select(CompanySearchResult).where(
                        CompanySearchResult.scan_id == scan_id, CompanySearchResult.record_id == row.id
                    )
                )
                if relation is None:
                    session.add(CompanySearchResult(scan_id=scan_id, record_id=row.id, matched=company.in_scope))
                else:
                    relation.matched, relation.seen_at = company.in_scope, utc_now()
            if changed:
                session.add(CompanySnapshot(record_id=row.id, fingerprint=fingerprint, payload=company.payload()))
            self.progress.set()
            return row.id

    async def records(
        self, actor: int, after: int = 0, *, limit: int | None = None, city: str | None = None, niche: str | None = None
    ) -> tuple[dict, ...]:
        async with self.database.session() as session:
            await self.admin(session, actor)
            # Последовательная выдача с проверкой повторов и отрицательного свидетельства других карт.
            selected = (
                select(CompanySearchResult.record_id)
                .join(CompanyScan, CompanyScan.id == CompanySearchResult.scan_id)
                .where(
                    CompanyScan.requested_by == actor,
                    CompanyScan.city == city,
                    CompanyScan.niche == niche,
                    CompanySearchResult.matched.is_(True),
                )
            )
            scoped_ids = (
                set((await session.scalars(selected)).all()) if city is not None and niche is not None else None
            )
            scoped = CompanyRecord.id.in_(selected) if city is not None and niche is not None else CompanyRecord.id > 0
            rows = (
                await session.scalars(
                    select(CompanyRecord)
                    .where(
                        scoped,
                        CompanyRecord.id > after,
                        CompanyRecord.in_scope.is_(True),
                        CompanyRecord.website_state == "not_listed",
                        CompanyRecord.phone.is_not(None),
                    )
                    .order_by(CompanyRecord.id)
                    .limit(self.settings.company_import_limit)
                )
            ).all()
            result = []
            for row in rows:
                if niche and not niche_matches(niche, row.name + " " + row.description):
                    continue
                same = (
                    await session.scalars(
                        select(CompanyRecord)
                        .where(CompanyRecord.identity_key == row.identity_key)
                        .order_by(CompanyRecord.id)
                    )
                ).all()
                if any(other.website_state == "listed" for other in same):
                    continue
                valid = [
                    other
                    for other in same
                    if other.in_scope
                    and other.website_state == "not_listed"
                    and other.phone
                    and other.description.strip()
                    and (scoped_ids is None or other.id in scoped_ids)
                    and (not niche or niche_matches(niche, other.name + " " + other.description))
                ]
                if not valid or valid[0].id != row.id or not row.description.strip():
                    continue
                result.append(
                    {
                        "id": row.id,
                        "name": row.name,
                        "description": row.description,
                        "city": row.city,
                        "address": row.address,
                        "phones": "; ".join(json.loads(row.phones)),
                        "telegram": "; ".join(json.loads(row.telegram)),
                        "max_links": "; ".join(json.loads(row.max_links)),
                        "source_url": row.source_url,
                        "provider": row.provider,
                        "website_state": row.website_state,
                        "checked_at": row.checked_at.isoformat(),
                        "duplicates": len(same) - 1,
                    }
                )
                if len(result) >= (limit or self.settings.company_page_size):
                    break
            return tuple(result)

    async def status(self, actor: int, city: str | None = None, niche: str | None = None) -> tuple[dict, ...]:
        async with self.database.session() as session:
            await self.admin(session, actor)
            query = select(CompanyScan).where(CompanyScan.requested_by == actor)
            if city is not None:
                query = query.where(CompanyScan.city == city)
            if niche is not None:
                query = query.where(CompanyScan.niche == niche)
            rows = (await session.scalars(query.order_by(CompanyScan.id))).all()
            return tuple(
                {
                    "provider": row.provider,
                    "city": row.city,
                    "niche": row.niche,
                    "status": row.status,
                    "skipped": row.skipped,
                    "error": row.error or "—",
                }
                for row in rows
            )

    async def wait_for_results(self, actor: int, city: str, niche: str, timeout: float) -> tuple[dict, ...]:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            self.progress.clear()
            rows = await self.records(actor, city=city, niche=niche)
            if rows:
                return rows
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0 or not self.running:
                return ()
            try:
                await asyncio.wait_for(self.progress.wait(), timeout=remaining)
            except TimeoutError:
                return ()

    async def recent(self, actor: int) -> tuple[dict, ...]:
        async with self.database.session() as session:
            await self.admin(session, actor)
            rows = (
                await session.scalars(
                    select(CompanyScan)
                    .where(CompanyScan.requested_by == actor, CompanyScan.niche != "")
                    .order_by(CompanyScan.requested_at.desc(), CompanyScan.id)
                    .limit(30)
                )
            ).all()
            seen = set()
            result = []
            for row in rows:
                key = (row.city, row.niche)
                if key not in seen:
                    result.append({"id": row.id, "city": row.city, "niche": row.niche})
                    seen.add(key)
                if len(result) >= 5:
                    break
            return tuple(result)

    async def scope(self, actor: int, scan_id: int) -> tuple[str, str]:
        async with self.database.session() as session:
            await self.admin(session, actor)
            row = await session.get(CompanyScan, scan_id)
            if row is None or row.requested_by != actor:
                raise AccountError(texts.ADMIN_ONLY)
            return row.city, row.niche

    async def step(self, provider_filter: str | None = None) -> bool:
        if not self.settings.company_monitor_enabled:
            return False
        async with self.database.session() as session:
            row = await session.scalar(
                select(CompanyScan)
                .where(
                    CompanyScan.status.in_(("pending", "processing", "retry_wait")),
                    CompanyScan.due_at <= utc_now(),
                    CompanyScan.attempts < self.settings.company_max_attempts,
                    CompanyScan.provider == provider_filter if provider_filter else CompanyScan.id > 0,
                )
                .order_by(CompanyScan.requested_at.desc(), CompanyScan.id)
                .limit(1)
            )
            if row is None:
                return False
            try:
                await self.admin(session, row.requested_by)
            except AccountError:
                row.status, row.error = "paused", "owner_revoked"
                return True
            queries = json.loads(row.queries) or list(self.settings.company_queries)
            niche = row.niche or None
            if row.query_index >= len(queries):
                row.status = "completed"
                return True
            row.status = "processing"
            row.attempts += 1
            scan_id, provider, city, index, page, link_index = (
                row.id,
                row.provider,
                row.city,
                row.query_index,
                row.page,
                row.link_index,
            )
            links, seen = json.loads(row.links), json.loads(row.seen)
        try:
            url = links[link_index] if link_index < len(links) else search_url(provider, city, queries[index], page)
            html = await self.pages.fetch(provider, url)
            found, discovered = await asyncio.to_thread(parse_page, provider, city, url, html, niche=niche)
            is_card = canonical(provider, url, url) is not None
            if not found and ((provider == "google" and not discovered) or (is_card and provider != "2gis")):
                html = await self.pages.render(provider, url)
                found, discovered = await asyncio.to_thread(parse_page, provider, city, url, html, niche=niche)
            for company in found:
                await self.save(company, scan_id)
            async with self.database.session() as session:
                row = await session.get(CompanyScan, scan_id)
                if is_card:
                    if not found:
                        row.skipped += 1
                        row.issues = json.dumps(
                            [
                                *json.loads(row.issues),
                                {"url": url, "error": "contact_data_unavailable", "at": utc_now().isoformat()},
                            ]
                        )
                    row.link_index += 1
                    row.seen = json.dumps([*seen, url])
                else:
                    new = [value for value in discovered if value not in seen][: self.settings.company_cards_per_query]
                    if new:
                        row.links, row.link_index = json.dumps(new), 0
                    else:
                        # Без выдачи и без явного признака «ничего не найдено» не объявляем успех.
                        if not discovered and not re_empty(html):
                            raise PageUnavailable("page_schema_unknown")
                        row.page = self.settings.company_max_pages + 1
                if is_card and row.link_index >= len(links):
                    row.page += 1
                    row.links = "[]"
                    row.link_index = 0
                if row.page > self.settings.company_max_pages or (
                    provider != "2gis" and is_card and row.link_index == 0
                ):
                    row.query_index += 1
                    row.page = 1
                    row.links = "[]"
                row.status = "completed" if row.query_index >= len(queries) else "pending"
                row.attempts, row.error, row.updated_at = 0, None, utc_now()
            return True
        except PageUnavailable as error:
            async with self.database.session() as session:
                row = await session.get(CompanyScan, scan_id)
                row.error = error.code
                if error.code == "http_429" and row.attempts < self.settings.company_max_attempts:
                    row.status = "retry_wait"
                    until = utc_now() + timedelta(
                        seconds=max(error.retry_after, self.settings.company_request_interval_seconds)
                    )
                    peers = (await session.scalars(select(CompanyScan).where(CompanyScan.provider == provider))).all()
                    for peer in peers:
                        peer.due_at = until
                else:
                    row.status = "paused"
                    if error.code in {"access_blocked", "captcha_or_consent", "robots_disallowed"}:
                        peers = (
                            await session.scalars(
                                select(CompanyScan).where(
                                    CompanyScan.provider == provider,
                                    CompanyScan.status.in_(("pending", "processing", "retry_wait")),
                                )
                            )
                        ).all()
                        for peer in peers:
                            peer.status, peer.error = "paused", error.code
            return True
        except (httpx.HTTPError, OSError, TimeoutError, ValueError, TypeError, AttributeError) as error:
            async with self.database.session() as session:
                row = await session.get(CompanyScan, scan_id)
                row.status = "failed" if row.attempts >= self.settings.company_max_attempts else "retry_wait"
                row.error = type(error).__name__
                row.due_at = utc_now() + timedelta(
                    seconds=min(
                        self.settings.retry_initial_seconds * 2 ** (row.attempts - 1), self.settings.retry_max_seconds
                    )
                )
            return True

    async def loop(self):
        self.running = True
        try:
            while True:
                self.wakeup.clear()
                results = await asyncio.gather(*(self.step(provider) for provider in PROVIDERS))
                if not any(results):
                    try:
                        await asyncio.wait_for(self.wakeup.wait(), timeout=self.settings.company_poll_seconds)
                    except TimeoutError:
                        pass
        finally:
            self.running = False
            if self.owned:
                await self.pages.close()


def re_empty(html: str) -> bool:
    return any(value in html.casefold() for value in ("ничего не найдено", "no results found", "нет результатов"))
