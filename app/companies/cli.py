"""Разовый явный сбор в CSV без открытия monitor.sqlite3."""

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from app.companies.fetch import PageUnavailable, PublicPages
from app.companies.parsers import CITY, PROVIDERS, parse_page, search_url
from app.companies.service import csv_bytes, prospect
from app.config import get_settings


async def collect(settings, provider: str, city: str, limit: int):
    pages = PublicPages(settings)
    companies = {}
    issues = []
    try:
        for query in settings.company_queries:
            url = search_url(provider, city, query)
            try:
                html = await pages.fetch(provider, url)
                _, links = await asyncio.to_thread(parse_page, provider, city, url, html)
                if provider == "google" and not links:
                    html = await pages.render(provider, url)
                    _, links = await asyncio.to_thread(parse_page, provider, city, url, html)
                for link in links[:limit]:
                    if len(companies) >= limit:
                        break
                    if link in companies:
                        continue
                    detail = await pages.fetch(provider, link)
                    records, _ = await asyncio.to_thread(parse_page, provider, city, link, detail)
                    if not records and provider != "2gis":
                        detail = await pages.render(provider, link)
                        records, _ = await asyncio.to_thread(parse_page, provider, city, link, detail)
                    for record in records:
                        companies[link] = record
                if len(companies) >= limit:
                    break
            except PageUnavailable as error:
                issues.append(error.code)
                break
    finally:
        await pages.close()
    return tuple(companies.values()), tuple(issues)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Публичные компании, явное чтение, без рабочей БД")
    parser.add_argument("--live", action="store_true", help="Разрешить ограниченные GET к публичным картам")
    parser.add_argument("--provider", choices=PROVIDERS, default="2gis")
    parser.add_argument("--city", choices=CITY, default="Москва")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not args.live:
        print("Сбор не запускался. Для явного запуска укажите --live.")
        return 0
    settings = get_settings()
    if not 1 <= args.limit <= settings.company_cards_per_query:
        parser.error("Лимит должен быть от 1 до COMPANY_CARDS_PER_QUERY.")
    if args.output and args.output.exists():
        parser.error("Файл уже существует. Укажите новый путь: перезаписи не будет.")
    records, issues = asyncio.run(collect(settings, args.provider, args.city, args.limit))
    selected = [record for record in records if prospect(record)]
    if args.output:
        rows = [
            {**asdict(record), **{key: "; ".join(getattr(record, key)) for key in ("phones", "telegram", "max_links")}}
            for record in selected
        ]
        # Только новый явно указанный файл. Рабочая SQLite и сессия не открываются.
        with args.output.open("xb") as stream:
            stream.write(csv_bytes(rows))
    print(
        json.dumps(
            {
                "provider": args.provider,
                "city": args.city,
                "parsed_cards": len(records),
                "eligible": len(selected),
                "with_website": sum(bool(record.websites) for record in records),
                "issues": issues,
            },
            ensure_ascii=False,
        )
    )
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
