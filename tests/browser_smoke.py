"""Ручной запуск браузерной проверки на отдельной временной SQLite."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tempfile
import threading
from http.server import ThreadingHTTPServer

from browser_support import require_browser
from playwright.sync_api import expect, sync_playwright

from crm import CRM
from crm_api import CRMApp, Handler
from reliability import migrate, open_database


def main():
    with tempfile.TemporaryDirectory(prefix="byte-web-") as directory:
        path = Path(directory) / "test.sqlite3"
        db = open_database(path)
        migrate(db)
        crm = CRM(db, 9)
        crm.create(
            {"title": "Проверка сайта кафе", "original_text": "Нужен лендинг с меню и формой <script>alert(1)</script>"}
        )
        db.close()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.app = CRMApp(path, 9, "test-browser-token-" + "x" * 32)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        errors = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=require_browser(), headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.emulate_media(reduced_motion="reduce")
                page.goto(base, wait_until="networkidle")
                page.locator("[data-enter-language=ru]").click()
                previews = Path(__file__).resolve().parents[1] / "docs" / "previews"
                previews.mkdir(exist_ok=True)
                page.screenshot(path=str(previews / "studio-desktop.png"), full_page=True)
                page.locator("#lead-task").fill("Нужен сайт для кафе")
                page.locator("#lead-contact").fill("@test_customer")
                page.locator("#lead-budget").fill("50 000 RUB")
                page.locator(".brief-fallback summary").click()
                page.locator("#prepare-brief").click()
                assert "Нужен сайт для кафе" in page.locator("#brief-text").input_value()
                page.locator("[name=consent]").check()
                page.locator("#send-request").click()
                expect(page.locator(".form-status")).to_contain_text("сохранена")
                for width in (1440, 390):
                    page.set_viewport_size({"width": width, "height": 900})
                    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                page.screenshot(path=str(previews / "studio-mobile.png"), full_page=True)
                page.goto(base + "/demo.html")
                page.locator("#booking-name").fill("Тест")
                page.locator("#booking-form button").click()
                expect(page.locator("#booking-result")).to_contain_text("реальная запись не создана")
                page.locator("#demo-title").fill("Тест демо")
                page.locator("#demo-lead-form button").click()
                expect(page.locator(".demo-row")).to_have_count(3)
                page.goto(base + "/crm")
                page.locator("#token").fill(server.app.token)
                page.locator("#auth button").click()
                page.locator(".tile").filter(
                    has=page.get_by_role("heading", name="Проверка сайта кафе", exact=True)
                ).get_by_role("button", name="Открыть →", exact=True).click()
                page.get_by_role("heading", name="Проверка сайта кафе", exact=True).wait_for()
                calculator = page.locator("form").filter(
                    has=page.get_by_role("heading", name="Расчёт цены по твоим данным", exact=True)
                )
                calculator.locator("[name=hours]").fill("10")
                calculator.locator("[name=hourly_rate]").fill("1000")
                calculator.locator("[name=currency]").fill("RUB")
                calculator.get_by_role("button", name="Сохранить", exact=True).click()
                expect(page.get_by_role("button", name="Записать расчёт в карточку", exact=True)).to_be_visible()
                page.get_by_role("button", name="Записать расчёт в карточку", exact=True).click()
                expect(page.locator("#card")).to_contain_text("Рекомендуемая: 10000.00")
                page.get_by_role("button", name="Черновик повторного отклика", exact=True).click()
                page.get_by_role("heading", name="Черновик #1 · draft", exact=True).wait_for()
                page.locator("#close").click()
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.screenshot(path=str(previews / "crm-desktop.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 900})
                for view in ("board", "reviews", "reminders", "clients", "runtime", "integrations", "backups", "stats"):
                    page.locator("[data-view=" + view + "]").click()
                    expect(page.locator("#notice")).to_have_text("")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Переполнение CRM"
                assert not errors, errors
                browser.close()
            db = open_database(path)
            assert db.execute("SELECT count(*) FROM drafts").fetchone()[0] == 1
            assert db.execute("SELECT count(*) FROM conversations").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM web_intakes").fetchone()[0] == 1
            assert db.execute("SELECT count(*) FROM notification_queue").fetchone()[0] == 1
            db.close()
            print("Браузер: сайт 1440/390, заявка в CRM, два демо, вход, карточка, черновик, 8 разделов — OK")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    main()
