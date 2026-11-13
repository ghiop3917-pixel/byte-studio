"""Компании: вымышленные публичные карточки, MockTransport, без сети/рабочей SQLite."""

import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, SendDocument, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as BotUser
from sqlalchemy import select

from app.bot.accounts import AccountError, Accounts
from app.bot.handlers.companies import render_card
from app.bot.keyboards import CompanyData, keyboard
from app.bot.router import create_router
from app.companies.cli import main
from app.companies.fetch import PageUnavailable, PublicPages, blocked, find_browser, robots_allowed
from app.companies.parsers import canonical, city_slug, contacts, niche_matches, parse_page, phone, search_url
from app.companies.service import Companies, csv_bytes, prospect
from app.config import Settings
from app.db.models import CompanyRecord, CompanyScan, CompanySnapshot, Lead, User, utc_now
from app.db.session import Database

FIRM = "70000000000123"
URL = "https://2gis.ru/moscow/firm/" + FIRM
NAME = "Китай Авто <Тест>"
DESCRIPTION = "Компания по привозу автомобилей из Китая под заказ"


def firm(**kwargs):
    value = {
        "id": FIRM,
        "name": NAME,
        "name_ex": {"extension": DESCRIPTION},
        "city_alias": "moscow",
        "address_name": "Тестовая улица, 1",
        "adm_div": [{"type": "city", "name": "Москва"}],
        "org": {"id": "70000000000001"},
        "contact_groups": [
            {
                "contacts": [
                    {"type": "phone", "value": "89160000000"},
                    {"type": "website", "url": "https://t.me/fictional_company"},
                    {"type": "website", "url": "https://max.ru/u/fictional_company"},
                ]
            }
        ],
    }
    value.update(kwargs)
    return value


def html(record=None):
    encoded = json.dumps(record or firm(), ensure_ascii=False)
    return "<html><script>window.initial=JSON.parse(" + repr(encoded) + ");</script></html>"


def company():
    return parse_page("2gis", "Москва", URL, html())[0][0]


class ParserTests(unittest.TestCase):
    def test_real_2gis_state_shape_and_contacts(self):
        records, _ = parse_page("2gis", "Москва", URL, html())
        self.assertEqual(len(records), 1)
        row = records[0]
        self.assertEqual((row.name, row.description, row.phones), (NAME, DESCRIPTION, ("+79160000000",)))
        self.assertEqual(row.telegram, ("https://t.me/fictional_company",))
        self.assertEqual(row.max_links, ("https://max.ru/u/fictional_company",))
        self.assertTrue(prospect(row))

    def test_website_social_and_missing_contact_are_distinct(self):
        own = firm()
        own["contact_groups"][0]["contacts"].append({"type": "website", "url": "https://dealer.example"})
        records, _ = parse_page("2gis", "Москва", URL, html(own))
        self.assertFalse(prospect(records[0]))
        self.assertEqual(records[0].website_state, "listed")
        missing = firm()
        missing.pop("contact_groups")
        self.assertEqual(parse_page("2gis", "Москва", URL, html(missing))[0], ())
        masked = firm(contact_groups=[{"contacts": [{"type": "phone", "text": "8-916-000..."}]}])
        self.assertFalse(prospect(parse_page("2gis", "Москва", URL, html(masked))[0][0]))
        unresolved = firm(
            contact_groups=[{"contacts": [{"type": "website", "value": "https://link.2gis.ru/redirect"}]}]
        )
        self.assertEqual(parse_page("2gis", "Москва", URL, html(unresolved))[0], ())

    def test_wrong_city_and_not_china_import_do_not_pass(self):
        for record in (
            firm(adm_div=[{"type": "city", "name": "Кострома"}]),
            firm(name="Просто автосалон", name_ex={"extension": "Продажа легковых автомобилей"}),
        ):
            value = parse_page("2gis", "Москва", URL, html(record))[0][0]
            self.assertFalse(prospect(value))
        self.assertEqual(parse_page("2gis", "Ярославль", URL, html())[0][0].city, "")

    def test_recommended_company_not_mistaken_for_own(self):
        wrapper = {"records": [firm(), firm(id="70000000000456", name="Другой дилер")]}
        rows, _ = parse_page("2gis", "Москва", URL, html(wrapper))
        self.assertEqual([row.external_id for row in rows], [FIRM])

    def test_search_links_and_canonical_provider_boundaries(self):
        _, links = parse_page(
            "2gis",
            "Москва",
            search_url("2gis", "Москва", "авто из Китая"),
            '<a href="/firm/' + FIRM + '">Дилер</a><a href="https://evil.test/firm/1">x</a>',
        )
        self.assertEqual(links, (URL,))
        self.assertIsNone(canonical("2gis", "https://evil.test/moscow/firm/12", URL))
        self.assertIsNone(canonical("2gis", "https://2gis.ru@evil.test/moscow/firm/12", URL))
        self.assertEqual(canonical("yandex", "https://yandex.ru/maps/org/test/123/", "")[0], "123")
        self.assertEqual(canonical("yandex", "https://yandex.ru/maps/org/123/", "")[0], "123")

    def test_yandex_company_metadata(self):
        value = {
            "CompanyMetaData": {
                "id": "123",
                "name": NAME,
                "description": DESCRIPTION,
                "address": "Москва, Тестовая улица, 1",
                "Phones": [{"formatted": "+7 (916) 000-00-00"}],
                "Links": [{"href": "https://max.ru/u/test"}],
            }
        }
        rows, _ = parse_page("yandex", "Москва", "https://yandex.ru/maps/org/test/123/", html(value))
        self.assertTrue(prospect(rows[0]))
        self.assertEqual(rows[0].max_links, ("https://max.ru/u/test",))
        value["CompanyMetaData"]["url"] = "https://dealer.example"
        self.assertFalse(
            prospect(parse_page("yandex", "Москва", "https://yandex.ru/maps/org/test/123/", html(value))[0][0])
        )

    def test_google_rendered_contact_fields_and_authority(self):
        url = "https://www.google.com/maps/place/Test/data=!1sChIJ_test"
        markup = (
            '<h1>Авто из Китая под заказ</h1><button jsaction="pane.rating.category">Автосалон</button>'
            '<button data-item-id="phone:tel:+79160000000"></button>'
            '<button data-item-id="address" aria-label="Адрес: Москва, Тестовая, 1"></button>'
            '<a href="https://t.me/fictional_company">Telegram</a>'
        )
        rows, _ = parse_page("google", "Москва", url, markup)
        self.assertTrue(prospect(rows[0]))
        rows, _ = parse_page(
            "google", "Москва", url, markup + '<a data-item-id="authority" href="https://dealer.example">Сайт</a>'
        )
        self.assertFalse(prospect(rows[0]))
        self.assertEqual(parse_page("google", "Москва", url, "<h1>Компания</h1>")[0], ())
        rows, _ = parse_page(
            "google",
            "Москва",
            url,
            markup
            + '<a data-item-id="authority" href="https://www.google.com/url?q=https%3A%2F%2Fdealer.example">Сайт</a>',
        )
        self.assertFalse(prospect(rows[0]))

    def test_google_published_metadata_description_fallback(self):
        url = "https://www.google.com/maps/place/Test/data=!1sChIJ_test"
        markup = (
            "<meta property='og:description' content='Компания по привозу авто из Китая'>"
            "<h1>Тестовый дилер</h1><button data-item-id='phone:tel:+79160000000'></button>"
            "<button data-item-id='address' aria-label='Адрес: Москва, Тестовая, 1'></button>"
        )
        rows, _ = parse_page("google", "Москва", url, markup)
        self.assertTrue(prospect(rows[0]))
        self.assertEqual(rows[0].description, "Компания по привозу авто из Китая")

    def test_region_is_not_the_selected_city(self):
        from app.companies.parsers import city_matches

        self.assertFalse(city_matches("Москва", "Moscow Oblast, Khimki"))
        self.assertFalse(city_matches("Ярославль", "Yaroslavl Oblast, Rybinsk"))
        self.assertTrue(city_matches("Ярославль", "Yaroslavl, Yaroslavl Oblast"))

    def test_chrome_path_from_settings_can_be_used(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "chrome.exe"
            path.write_bytes(b"test")
            cfg = Settings(_env_file=None, CHROME_PATH=str(path))
            self.assertEqual(find_browser(cfg.company_chrome_path), str(path))

    def test_arbitrary_cities_and_niches_are_not_a_whitelist(self):
        self.assertEqual(city_slug("Казань"), "kazan")
        self.assertEqual(city_slug("москва"), "moscow")
        self.assertEqual(city_slug("Санкт-Петербург"), "spb")
        self.assertIn("/kazan/search/", search_url("2gis", "Казань", "стоматологии"))
        self.assertIn("text=", search_url("yandex", "Тверь", "цветочные магазины"))
        for niche, text in (
            ("стоматологии", "Стоматологическая клиника"),
            ("ремонт телефонов", "Ремонт мобильных телефонов"),
            ("салоны красоты", "Парикмахерская"),
            ("магазины цветов", "Цветы"),
        ):
            self.assertTrue(niche_matches(niche, text), (niche, text))
        self.assertFalse(niche_matches("стоматологии", "Автосервис"))
        source = firm(
            name="Тестовая клиника",
            name_ex={"extension": "Стоматологическая клиника"},
            city_alias="kazan",
            adm_div=[{"type": "city", "name": "Казань"}],
        )
        rows, _ = parse_page("2gis", "Казань", "https://2gis.ru/kazan/firm/" + FIRM, html(source), niche="стоматологии")
        self.assertTrue(prospect(rows[0]))
        self.assertEqual(
            Settings(_env_file=None, company_cities=["Казань", "Тверь"]).company_cities, ("Казань", "Тверь")
        )

    def test_safe_json_and_phone(self):
        self.assertEqual(phone("8 (916) 000-00-00"), "+79160000000")
        self.assertIsNone(phone("показать телефон"))
        self.assertEqual(parse_page("2gis", "Москва", URL, "<script>JSON.parse('__import__(\"os\")');</script>")[0], ())
        tg, mx, web = contacts(
            ["javascript:alert(1)", "https://t.me/test", "https://max.ru/u/test", "https://max.ru.evil.test"]
        )
        self.assertEqual((len(tg), len(mx), len(web)), (1, 1, 1))

    def test_robots_longest_rule_specific_agent_and_captcha(self):
        rules = "User-agent: *\nDisallow: /maps/\nAllow: /maps/search/\nDisallow: /maps/search/*?secret=*"
        self.assertTrue(robots_allowed(rules, "https://www.google.com/maps/search/test"))
        self.assertFalse(robots_allowed(rules, "https://www.google.com/maps/search/test?secret=x"))
        self.assertFalse(robots_allowed(rules, "https://www.google.com/maps/private"))
        specific = "User-agent: ByteStudioCompanyResearch\nDisallow:\nUser-agent: *\nDisallow: /"
        self.assertTrue(robots_allowed(specific, URL))
        self.assertTrue(blocked("<title>403</title>"))
        self.assertFalse(blocked("<title>Дилер</title><script>var captchaOption=false</script>"))


class FetchTests(unittest.IsolatedAsyncioTestCase):
    def cfg(self, **kwargs):
        return Settings(_env_file=None, **kwargs)

    async def test_fetch_uses_public_host_and_robots_without_auth(self):
        seen = []

        def handle(request):
            seen.append(request)
            return httpx.Response(200, text="User-agent: *\nAllow: /" if request.url.path == "/robots.txt" else html())

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            pages = PublicPages(self.cfg(), client)
            with patch("app.companies.fetch.asyncio.sleep", new=AsyncMock()):
                self.assertIn("JSON.parse", await pages.fetch("2gis", URL))
            self.assertEqual(len(seen), 2)
            self.assertTrue(all("authorization" not in request.headers for request in seen))
            with self.assertRaises(PageUnavailable):
                await pages.fetch("2gis", "http://127.0.0.1/private")

    async def test_robots_blocks_before_card_request(self):
        seen = []

        def handle(request):
            seen.append(str(request.url))
            return httpx.Response(200, text="User-agent: *\nDisallow: /")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            pages = PublicPages(self.cfg(), client)
            with self.assertRaises(PageUnavailable) as error:
                await pages.fetch("2gis", URL)
            self.assertEqual(error.exception.code, "robots_disallowed")
            self.assertEqual(len(seen), 1)

    async def test_403_429_redirect_and_response_size(self):
        for status, code, headers, body in (
            (403, "access_blocked", {}, ""),
            (429, "http_429", {"Retry-After": "60"}, ""),
            (302, "url_not_allowed", {"Location": "https://evil.test"}, ""),
            (200, "page_too_large", {}, "x" * 11000),
        ):
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request, status=status, headers=headers, body=body: (
                        httpx.Response(200, text="User-agent: *\nAllow: /")
                        if request.url.path == "/robots.txt"
                        else httpx.Response(status, headers=headers, text=body)
                    )
                )
            ) as client:
                pages = PublicPages(self.cfg(company_max_page_bytes=10000), client)
                with (
                    patch("app.companies.fetch.asyncio.sleep", new=AsyncMock()),
                    self.assertRaises(PageUnavailable) as error,
                ):
                    await pages.fetch("2gis", URL)
                self.assertEqual(error.exception.code, code)

    async def test_http_date_retry_after_is_respected(self):
        from email.utils import format_datetime

        until = utc_now() + timedelta(minutes=3)

        def response(request):
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nAllow: /")
            return httpx.Response(429, headers={"Retry-After": format_datetime(until, usegmt=True)})

        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            pages = PublicPages(self.cfg(), client)
            with (
                patch("app.companies.fetch.asyncio.sleep", new=AsyncMock()),
                self.assertRaises(PageUnavailable) as error,
            ):
                await pages.fetch("2gis", URL)
            self.assertGreater(error.exception.retry_after, 170)

    async def test_independent_hosts_can_fetch_in_parallel(self):
        entered = set()
        both = asyncio.Event()

        async def response(request):
            entered.add(request.url.host)
            if len(entered) == 2:
                both.set()
            await asyncio.wait_for(both.wait(), 1)
            return httpx.Response(200, text="ok")

        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            pages = PublicPages(self.cfg(), client)
            with patch("app.companies.fetch.asyncio.sleep", new=AsyncMock()):
                await asyncio.gather(pages._get("2gis", URL), pages._get("yandex", "https://yandex.ru/maps/"))
            self.assertEqual(set(pages.last), {"2gis.ru", "yandex.ru"})

    async def test_browser_opt_in_and_no_captcha_bypass(self):
        renderer = AsyncMock(return_value="<title>CAPTCHA</title>")
        cfg = self.cfg()
        pages = PublicPages(cfg, renderer=renderer)
        pages.robots["2gis.ru"] = "User-agent: *\nAllow: /"
        with self.assertRaises(PageUnavailable):
            await pages.render("2gis", URL)
        renderer.assert_not_awaited()
        cfg.company_render_enabled = True
        with self.assertRaises(PageUnavailable) as error:
            await pages.render("2gis", URL)
        self.assertEqual(error.exception.code, "captcha_or_consent")

    async def test_cli_does_not_run_network_by_default(self):
        with patch("app.companies.cli.collect", new=AsyncMock()) as run:
            self.assertEqual(main([]), 0)
            run.assert_not_awaited()


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cfg = Settings(
            _env_file=None,
            database_url="sqlite+aiosqlite:///:memory:",
            admin_ids=[9],
            telegram_session_paths=[Path("fake.session")],
            company_monitor_enabled=True,
            company_max_pages=1,
            company_queries=("авто из Китая",),
        )
        self.db = Database(self.cfg, read_only=False, testing=True)
        await self.db.create_test_schema()
        self.accounts = Accounts(self.db, self.cfg)
        await self.accounts.register(9, None)
        await self.accounts.register(10, None)
        self.pages = SimpleNamespace(fetch=AsyncMock(), render=AsyncMock(), close=AsyncMock())
        self.companies = Companies(self.db, self.cfg, self.pages)

    async def asyncTearDown(self):
        await self.db.close()

    async def rows(self, cls):
        async with self.db.session() as session:
            return (await session.scalars(select(cls).order_by(cls.id))).all()

    async def test_sources_upsert_history_and_no_fake_order(self):
        row = company()
        identity = await self.companies.save(row)
        self.assertEqual(await self.companies.save(row), identity)
        self.assertEqual(len(await self.rows(CompanySnapshot)), 1)
        await self.companies.save(replace(row, description=row.description + " Новое описание"))
        self.assertEqual(len(await self.rows(CompanyRecord)), 1)
        self.assertEqual(len(await self.rows(CompanySnapshot)), 2)
        self.assertEqual(await self.rows(Lead), [])
        results = await self.companies.records(9)
        self.assertEqual(results[0]["phones"], "+79160000000")

    async def test_duplicate_cross_source_with_website_suppresses_candidate(self):
        await self.companies.save(company())
        other = replace(
            company(), provider="yandex", external_id="123", source_url="https://yandex.ru/maps/org/test/123/"
        )
        await self.companies.save(other)
        self.assertEqual(len(await self.companies.records(9)), 1)
        self.assertEqual(len(await self.rows(CompanyRecord)), 2)
        await self.companies.save(replace(other, website_state="listed", websites=("https://dealer.example",)))
        self.assertEqual(await self.companies.records(9), ())
        self.assertEqual(len(await self.rows(CompanySnapshot)), 3)

    async def test_admin_only_and_banned_role(self):
        with self.assertRaises(AccountError):
            await self.companies.enqueue(10)
        with self.assertRaises(AccountError):
            await self.companies.records(10)
        with self.assertRaises(AccountError):
            await self.companies.status(10)
        async with self.db.session() as session:
            (await session.scalar(select(User).where(User.tg_id == 9))).is_banned = True
        with self.assertRaises(AccountError):
            await self.companies.enqueue(9)

    async def test_default_scope_and_idempotent_queue(self):
        self.assertEqual(await self.companies.enqueue(9), 6)
        self.assertEqual(await self.companies.enqueue(9), 0)
        jobs = await self.rows(CompanyScan)
        self.assertEqual({row.city for row in jobs}, {"Москва", "Ярославль"})
        self.assertEqual({row.provider for row in jobs}, {"2gis", "yandex", "google"})
        self.cfg.company_monitor_enabled = False
        self.assertFalse(await self.companies.step())
        self.pages.fetch.assert_not_awaited()

    async def test_durable_step_and_cursor_after_restart(self):
        self.pages.fetch.side_effect = lambda provider, url: (
            html() if "/firm/" in url else '<a href="/firm/' + FIRM + '">Дилер</a>'
        )
        await self.companies.enqueue(9)
        self.assertTrue(await self.companies.step())
        self.assertEqual((await self.rows(CompanyScan))[0].link_index, 0)
        restarted = Companies(self.db, self.cfg, self.pages)
        await restarted.step()
        self.assertEqual(len(await restarted.records(9)), 1)
        job = (await self.rows(CompanyScan))[0]
        self.assertEqual(job.status, "completed")
        self.assertIn(URL, json.loads(job.seen))
        self.assertEqual(len(await self.rows(CompanySnapshot)), 1)

    async def test_blocked_source_does_not_stop_other_sources(self):
        await self.companies.enqueue(9)
        self.pages.fetch.side_effect = PageUnavailable("access_blocked")
        await self.companies.step()
        rows = await self.rows(CompanyScan)
        self.assertTrue(all(row.status == "paused" for row in rows if row.provider == "2gis"))
        self.assertTrue(all(row.status == "pending" for row in rows if row.provider != "2gis"))

    async def test_rate_limit_delays_whole_provider(self):
        await self.companies.enqueue(9)
        self.pages.fetch.side_effect = PageUnavailable("http_429", retry_after=60)
        await self.companies.step()
        self.assertTrue(
            all(
                row.due_at > utc_now() + timedelta(seconds=55)
                for row in await self.rows(CompanyScan)
                if row.provider == "2gis"
            )
        )

    async def test_missing_card_skipped_without_false_prospect(self):
        await self.companies.enqueue(9)
        self.pages.fetch.side_effect = lambda provider, url: (
            "<html>Неизвестная карточка</html>" if "/firm/" in url else '<a href="/firm/' + FIRM + '">Дилер</a>'
        )
        await self.companies.step()
        await self.companies.step()
        job = (await self.rows(CompanyScan))[0]
        self.assertEqual(job.skipped, 1)
        self.assertIn("contact_data_unavailable", job.issues)
        self.assertEqual(await self.companies.records(9), ())

    async def test_csv_and_card_are_safe(self):
        await self.companies.save(company())
        row = (await self.companies.records(9))[0]
        data = csv_bytes([{**row, "name": '=IMPORTXML("secret")'}]).decode("utf-8-sig")
        self.assertIn("'=IMPORTXML", data)
        text = render_card(
            {**row, "description": "<&" * 5000, "telegram": "&" * 5000, "max_links": "&" * 5000}, self.cfg
        )
        self.assertLessEqual(len(text), 4096)
        self.assertNotIn("<Тест>", text)
        self.assertIn("&lt;", text)

    async def test_different_niches_and_owners_have_separate_jobs(self):
        self.assertEqual(await self.companies.enqueue(9, "Казань", "стоматологии"), 3)
        self.assertEqual(await self.companies.enqueue(9, "Казань", "автосервисы"), 3)
        self.assertEqual(await self.companies.enqueue(9, "Тверь", "цветы"), 3)
        jobs = await self.rows(CompanyScan)
        self.assertEqual(len(jobs), 9)
        self.assertEqual({row.niche for row in jobs}, {"стоматологии", "автосервисы", "цветы"})
        self.assertEqual(json.loads(jobs[0].queries), ["стоматологии"])
        self.assertTrue(self.companies.wakeup.is_set())

    async def test_results_do_not_mix_between_city_and_niche(self):
        await self.companies.enqueue(9, "Казань", "стоматологии")
        await self.companies.enqueue(9, "Казань", "автосервисы")
        jobs = await self.rows(CompanyScan)
        doctor = replace(company(), name="Клиника", description="Стоматологическая клиника", city="Казань")
        auto = replace(
            company(),
            external_id="other",
            name="Гараж",
            description="Автосервис",
            city="Казань",
            phones=("+79161111111",),
        )
        await self.companies.save(doctor, jobs[0].id)
        await self.companies.save(auto, jobs[3].id)
        self.assertEqual(
            [row["name"] for row in await self.companies.records(9, city="Казань", niche="стоматологии")], ["Клиника"]
        )
        self.assertEqual(
            [row["name"] for row in await self.companies.records(9, city="Казань", niche="автосервисы")], ["Гараж"]
        )
        self.assertEqual(await self.companies.records(9, city="Тверь", niche="стоматологии"), ())

    async def test_fresh_search_cache_and_explicit_refresh(self):
        await self.companies.enqueue(9, "Казань", "стоматологии")
        async with self.db.session() as session:
            for row in (await session.scalars(select(CompanyScan))).all():
                row.status = "completed"
        self.assertEqual(await self.companies.enqueue(9, "Казань", "стоматологии"), 0)
        self.assertEqual(await self.companies.enqueue(9, "Казань", "стоматологии", refresh=True), 3)
        recent = await self.companies.recent(9)
        self.assertEqual(len(recent), 1)
        self.assertEqual(await self.companies.scope(9, recent[0]["id"]), ("Казань", "стоматологии"))
        with self.assertRaises(AccountError):
            await self.companies.scope(10, recent[0]["id"])

    async def test_first_results_wait_is_signalled_not_database_polling(self):
        await self.companies.enqueue(9, "Казань", "стоматологии")
        job = (await self.rows(CompanyScan))[0]
        self.companies.running = True
        task = asyncio.create_task(self.companies.wait_for_results(9, "Казань", "стоматологии", 1))
        await asyncio.sleep(0.01)
        await self.companies.save(
            replace(company(), name="Клиника", description="Стоматологическая клиника", city="Казань"), job.id
        )
        rows = await task
        self.assertEqual(rows[0]["name"], "Клиника")
        self.companies.running = False

    async def test_loop_runs_providers_concurrently_and_stops(self):
        entered = set()
        ready = asyncio.Event()

        async def step(provider):
            entered.add(provider)
            if len(entered) == 3:
                ready.set()
            await ready.wait()
            return False

        self.companies.step = step
        task = asyncio.create_task(self.companies.loop())
        try:
            await asyncio.wait_for(ready.wait(), 1)
            self.assertTrue(self.companies.running)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(self.companies.running)

    async def test_actual_dispatcher_owner_buttons_and_export(self):
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
                if isinstance(method, (AnswerCallbackQuery, SendDocument)):
                    return True
                raise AssertionError(type(method).__name__)

            async def stream_content(self, *args, **kwargs):
                raise AssertionError("network forbidden")
                yield b""

        bot = Bot("123456789:TEST", session=Transport())
        dp = Dispatcher()
        dp.include_router(create_router(self.db, self.cfg))
        await self.companies.save(company())
        view = await self.accounts.view(9)
        self.assertTrue(
            any(
                button.callback_data.startswith("company:")
                for row in keyboard("home", view, self.cfg).inline_keyboard
                for button in row
            )
        )

        async def callback(action, owner=9):
            msg = Message(message_id=1, date=utc_now(), chat=Chat(id=owner, type="private"), text="Меню")
            cb = CallbackQuery(
                id=action + str(owner),
                from_user=BotUser(id=owner, is_bot=False, first_name="Тест"),
                message=msg,
                data=CompanyData(action=action, after=0).pack(),
                chat_instance="test",
            )
            await dp.feed_update(bot, Update(update_id=len(calls) + 1, callback_query=cb))

        try:
            await callback("home", 10)
            self.assertFalse(any(isinstance(call, SendMessage) for call in calls))
            await callback("home")
            await callback("scan")

            async def typed(text):
                message = Message(
                    message_id=len(calls) + 1,
                    date=utc_now(),
                    chat=Chat(id=9, type="private"),
                    from_user=BotUser(id=9, is_bot=False, first_name="Тест"),
                    text=text,
                )
                await dp.feed_update(bot, Update(update_id=len(calls) + 1, message=message))

            await typed("Москва")
            await typed("авто из Китая")
            job = (await self.rows(CompanyScan))[0]
            await self.companies.save(company(), job.id)
            await callback("list")
            await callback("export")
            self.assertTrue(any(isinstance(call, SendDocument) for call in calls))
            self.assertTrue(all(call.chat_id == 9 for call in calls if isinstance(call, (SendMessage, SendDocument))))
        finally:
            await dp.storage.close()
            await bot.session.close()


if __name__ == "__main__":
    unittest.main()
