import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from browser_support import find_browser, require_browser


class BrowserDiscoveryTests(unittest.TestCase):
    def test_environment_has_priority(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "chrome"
            path.touch()
            with patch.dict(os.environ, {"CHROME_PATH": str(path)}), patch("browser_support.shutil.which") as which:
                self.assertEqual(find_browser(), str(path))
                which.assert_not_called()

    def test_invalid_environment_falls_back_to_path_in_order(self):
        with (
            patch.dict(os.environ, {"CHROME_PATH": "/missing/browser"}),
            patch(
                "browser_support.shutil.which",
                side_effect=lambda name: "/local/chromium" if name == "chromium" else None,
            ) as which,
        ):
            self.assertEqual(find_browser(), "/local/chromium")
            self.assertEqual([call.args[0] for call in which.call_args_list], ["chrome", "chromium"])

    def test_standard_windows_edge_path(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "Microsoft/Edge/Application/msedge.exe"
            path.parent.mkdir(parents=True)
            path.touch()
            with (
                patch.dict(os.environ, {"PROGRAMFILES": folder}, clear=True),
                patch("browser_support.sys.platform", "win32"),
                patch("browser_support.shutil.which", return_value=None),
            ):
                self.assertEqual(find_browser(), str(path))

    def test_missing_browser_skips_with_reason(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("browser_support.sys.platform", "linux"),
            patch("browser_support.shutil.which", return_value=None),
        ):
            self.assertIsNone(find_browser())
            with self.assertRaisesRegex(unittest.SkipTest, "CHROME_PATH"):
                require_browser()
