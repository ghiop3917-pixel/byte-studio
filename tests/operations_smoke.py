"""Проверка панелей этапа 7 на отдельной базе, без Telegram-отправок."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tempfile
import threading
from http.server import ThreadingHTTPServer

from browser_support import require_browser
from playwright.sync_api import expect, sync_playwright

from crm_api import CRMApp, Handler
from maintenance import run_tick
from quality import Quality
from reliability import heartbeat, ingest, migrate, open_database, process_one


def main():
    with tempfile.TemporaryDirectory(prefix="byte-operations-") as folder:
        path = Path(folder) / "test.sqlite3"
        db = open_database(path)
        migrate(db)
        for index in range(1, 6):
            ingest(
                db,
                {
                    "chat": {"id": -1001, "title": "Тестовый бизнес-чат", "type": "supergroup"},
                    "message_id": index,
                    "text": "Нужен сайт для магазина, заказ " + str(index),
                    "from": {"id": 100 + index},
                },
                9,
            )
            process_one(db, lambda _: ("dev",))
            Quality(db, 9).feedback(
                index, "approved" if index == 1 else "rejected", "Реклама / рассылка" if index > 1 else None
            )
        for component in ("monitor", "processing", "notifications"):
            heartbeat(db, component)
        db.close()
        for _ in range(3):
            run_tick(path, 9)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.app = CRMApp(path, 9, "x" * 40)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        errors = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=require_browser(), headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(f"http://127.0.0.1:{server.server_port}/crm")
                page.locator("#token").fill(server.app.token)
                page.locator("#auth button").click()
                page.locator("[data-view=quality]").click()
                expect(page.locator(".quality-source")).to_have_count(1)
                expect(page.locator(".quality-source")).to_contain_text("Мусор: 4")
                page.screenshot(path="docs/previews/stage7-quality.png", full_page=True)
                page.get_by_role("button", name="Оценить #5", exact=True).click()
                page.get_by_role("button", name="Подходит", exact=True).click()
                expect(page.locator("#card")).to_contain_text("Последняя оценка: Подходит")
                page.locator("#close").click()
                page.locator("#refresh").click()
                expect(page.locator(".quality-source")).to_contain_text("Мусор: 3")
                page.locator("[data-view=suggestions]").click()
                page.get_by_role("button", name="Поддержать предложение", exact=True).first.click()
                expect(page.locator("#content")).to_contain_text("Поддержано; изменения не применены")
                page.locator("[data-view=operations]").click()
                page.get_by_role("button", name="Проверить копию", exact=True).click()
                expect(page.locator("#content")).to_contain_text("Контрольная сумма и целостность подтверждены")
                page.get_by_role("button", name="Запустить сейчас", exact=True).first.click()
                expect(page.locator("#content")).to_contain_text("Задание поставлено в очередь")
                run_tick(path, 9)
                page.locator("#refresh").click()
                expect(page.get_by_role("button", name="Проверить копию", exact=True)).to_have_count(2)
                page.set_viewport_size({"width": 390, "height": 900})
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                page.screenshot(path="docs/previews/stage7-operations-mobile.png", full_page=True)
                assert not errors, errors
                browser.close()
            db = open_database(path)
            assert db.execute("SELECT count(*) FROM notification_queue WHERE status='pending'").fetchone()[0] == 5
            assert db.execute("SELECT count(*) FROM notification_queue").fetchone()[0] == 5
            assert db.execute("SELECT count(*) FROM suggestion_decisions").fetchone()[0] == 1
            db.close()
            print("Stage 7 UI OK: source quality, feedback, suggestions, backup verify, manual jobs, mobile")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    main()
