"""Браузерные проверки запускаются явно; обычный discover остаётся офлайн."""

import importlib
import os
import unittest

from browser_support import require_browser


@unittest.skipUnless(os.environ.get("RUN_BROWSER_SMOKE") == "1", "Нужен RUN_BROWSER_SMOKE=1 и локальный браузер")
class BrowserSmokeTests(unittest.TestCase):
    def setUp(self):
        require_browser()

    def test_browser_smoke(self):
        importlib.import_module("browser_smoke").main()

    def test_concepts_smoke(self):
        importlib.import_module("concepts_smoke").main()

    def test_imported_site_smoke(self):
        importlib.import_module("imported_site_smoke").main()

    def test_operations_smoke(self):
        importlib.import_module("operations_smoke").main()

    def test_premium_site_smoke(self):
        importlib.import_module("premium_site_smoke").main()
