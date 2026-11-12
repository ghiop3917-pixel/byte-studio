"""Браузерная проверка новых концепций и упрощённой формы."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tempfile
import threading
from http.server import ThreadingHTTPServer

from browser_support import require_browser
from playwright.sync_api import expect, sync_playwright

from crm_api import CRMApp, Handler
from reliability import migrate, open_database


def main():
    with tempfile.TemporaryDirectory(prefix="byte-concepts-") as temp:
        path = Path(temp) / "test.sqlite3"
        db = open_database(path)
        migrate(db)
        db.close()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.app = CRMApp(path, 9, "x" * 40)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        errors = []
        previews = Path("docs/previews")
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=require_browser(), headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(base + "/forma.html", wait_until="networkidle")
                page.wait_for_timeout(1600)
                page.screenshot(path=str(previews / "forma-desktop.png"))
                page.get_by_role("button", name="Тихие пространства", exact=True).click()
                expect(page.locator("[data-room-type=living]")).to_be_hidden()
                page.locator("[data-space=quiet]").click()
                expect(page.locator("#space-dialog")).to_be_visible()
                page.locator("#space-close").click()
                page.set_viewport_size({"width": 390, "height": 850})
                page.goto(base + "/forma.html", wait_until="networkidle")
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                page.screenshot(path=str(previews / "forma-mobile.png"))
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.goto(base + "/nexus.html", wait_until="networkidle")
                expect(page.locator(".nexus-project")).to_have_count(4)
                page.screenshot(path=str(previews / "nexus-desktop.png"))
                page.locator("#nexus-search").fill("Telegram")
                expect(page.locator(".nexus-project")).to_have_count(1)
                page.locator("#nexus-search").fill("")
                page.locator("#new-project").click()
                page.locator("#nexus-title").fill("Тестовый проект")
                page.locator("#nexus-project-form button").click()
                expect(page.locator(".nexus-project")).to_have_count(5)
                page.locator(".nexus-project").first.locator("select").select_option("done")
                expect(page.locator("#stat-done")).to_have_text("2")
                page.locator("[data-nexus-view=activity]").click()
                expect(page.locator("#nexus-activity")).to_contain_text("Тестовый проект")
                page.locator("[data-nexus-view=overview]").click()
                expect(page.locator(".nexus-chart")).to_be_visible()
                page.locator("[data-nexus-view=projects]").click()
                for width in [1024, 768, 390, 360]:
                    page.set_viewport_size({"width": width, "height": 850})
                    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), width
                page.screenshot(path=str(previews / "nexus-mobile.png"))
                page.emulate_media(reduced_motion="reduce")
                page.goto(base, wait_until="networkidle")
                page.locator("[data-enter-language=ru]").click()
                expect(page.locator("#contact-form input[name=contact]")).to_have_count(1)
                expect(page.locator("#contact-form input[name=name]")).to_have_count(0)
                page.locator("#lead-contact").fill("@contact_only")
                page.locator("#lead-task").fill("Нужен красивый сайт для компании")
                page.locator("#lead-budget").fill("70000 рублей")
                page.locator("[name=consent]").check()
                page.locator("#send-request").click()
                expect(page.locator(".form-status")).to_contain_text("сохранена")
                assert not errors, errors
                browser.close()
            db = open_database(path)
            assert db.execute("SELECT count(*) FROM web_intakes").fetchone()[0] == 1
            assert db.execute("SELECT count(*) FROM leads").fetchone()[0] == 1
            assert db.execute("SELECT name FROM clients").fetchone()[0] is None
            db.close()
            print("Concepts QA OK: Forma, Nexus, поиск, новый проект, статусы, мобильная версия, простая заявка в CRM")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    main()
