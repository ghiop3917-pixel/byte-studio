"""Проверка сайта владельца и интеграции короткой заявки с CRM."""

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
    with tempfile.TemporaryDirectory(prefix="byte-import-") as folder:
        db_path = Path(folder) / "test.sqlite3"
        db = open_database(db_path)
        migrate(db)
        db.close()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.app = CRMApp(db_path, 9, "x" * 40)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f"http://127.0.0.1:{server.server_port}"
        errors = []
        console_errors = []
        failed = []
        previews = Path("docs/previews")
        previews.mkdir(exist_ok=True)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=require_browser(), headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
                page.on("response", lambda r: failed.append((r.status, r.url)) if r.status >= 400 else None)
                page.goto(base, wait_until="networkidle")
                page.locator("[data-enter-language=ru]").click()
                expect(page.locator("body")).not_to_have_class("language-pending")
                page.wait_for_timeout(3000)
                page.screenshot(path=str(previews / "imported-hero.png"))
                assert page.locator(".project-card").count() == 4
                page.locator(".project-card").first.click()
                expect(page.locator("#project-dialog")).to_be_visible()
                page.locator("#project-dialog .dialog-close").click()
                form = page.locator("#contact-form")
                expect(form.locator("input[name=name]")).to_have_count(0)
                expect(form.locator("select[name=type]")).to_have_count(0)
                expect(form.locator("input[name=contact]")).to_have_count(1)
                form.locator("[name=contact]").fill("@archive_customer")
                form.locator("[name=description]").fill("Нужен сайт для компании с каталогом")
                form.locator("[name=budget]").fill("60000 рублей")
                form.locator("[name=consent]").check()
                page.locator("#contact").scroll_into_view_if_needed()
                page.wait_for_timeout(1000)
                page.screenshot(path=str(previews / "imported-form.png"))
                page.locator("#send-request").click()
                expect(form.locator(".form-status")).to_contain_text("сохранена")
                expect(page.locator("#send-request")).to_be_disabled()
                page.locator("[data-language=en]").click()
                expect(page.locator("#send-request")).to_contain_text("Request sent")
                for width in [1920, 1440, 1024, 768, 390, 360]:
                    page.set_viewport_size({"width": width, "height": 900})
                    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), (
                        f"Horizontal overflow {width}"
                    )
                page.emulate_media(reduced_motion="reduce")
                page.reload(wait_until="networkidle")
                page.locator("[data-enter-language=en]").click()
                page.locator("#contact").scroll_into_view_if_needed()
                expect(page.locator("#send-request")).to_contain_text("Send request")
                page.screenshot(path=str(previews / "imported-mobile-form.png"))
                page.route(
                    "**/public/intake",
                    lambda route: route.fulfill(
                        status=503, content_type="application/json", body='{"error":"Unavailable"}'
                    ),
                )
                form.locator("[name=contact]").fill("+79991234567")
                form.locator("[name=description]").fill("Please build a website for my company")
                form.locator("[name=consent]").check()
                page.locator("#send-request").click()
                expect(form.locator(".form-status")).to_contain_text("not saved")
                expect(page.locator("#send-request")).to_be_enabled()
                assert not errors, errors
                assert not [item for item in failed if item[0] != 503], failed
                assert not [e for e in console_errors if "Content Security Policy" in e], console_errors
                browser.close()
            db = open_database(db_path)
            assert db.execute("SELECT count(*) FROM web_intakes").fetchone()[0] == 1
            assert db.execute("SELECT count(*) FROM notification_queue").fetchone()[0] == 1
            assert db.execute("SELECT name FROM clients").fetchone()[0] is None
            row = db.execute("SELECT original_text FROM leads").fetchone()[0]
            assert "60000 рублей" in row
            db.close()
            print("Imported website QA OK: 4 projects, RU/EN, short form, CRM, failure state, six sizes")
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == "__main__":
    main()
