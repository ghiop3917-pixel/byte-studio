import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from session_config import secure_session, session_path


class SessionConfigTests(unittest.TestCase):
    def test_explicit_path_has_priority(self):
        with patch.dict(os.environ, {"TELEGRAM_LEADS_SESSION": "custom.session"}):
            self.assertEqual(session_path(), Path("custom.session").resolve())

    def test_platform_defaults(self):
        with patch.dict(os.environ, {}, clear=True), patch("pathlib.Path.home", return_value=Path.cwd()):
            expected = (
                Path.home()
                / ("AppData/Local/TelegramLeads" if os.name == "nt" else ".local/state/telegram-leads")
                / "monitor.session"
            )
            self.assertEqual(session_path(), expected)
        key = "LOCALAPPDATA" if os.name == "nt" else "XDG_STATE_HOME"
        with (
            patch.dict(os.environ, {key: str(Path.cwd())}, clear=True),
            patch("pathlib.Path.home", return_value=Path.cwd()),
        ):
            self.assertEqual(
                session_path(),
                Path.cwd() / ("TelegramLeads" if os.name == "nt" else "telegram-leads") / "monitor.session",
            )

    @unittest.skipUnless(os.name == "posix", "Права POSIX не применяются на Windows")
    def test_private_directory_and_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "private" / "monitor.session"
            secure_session(path)
            path.touch(mode=0o666)
            secure_session(path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    @unittest.skipUnless(os.name == "posix", "Права POSIX не применяются на Windows")
    def test_existing_default_directory_is_restricted(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "default" / "monitor.session"
            path.parent.mkdir()
            path.parent.chmod(0o777)
            path.touch()
            with patch.dict(os.environ, {}, clear=True), patch("session_config.session_path", return_value=path):
                secure_session(path)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    @unittest.skipUnless(os.name == "posix", "Права POSIX не применяются на Windows")
    def test_explicit_directory_keeps_permissions_and_warns(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "shared" / "monitor.session"
            path.parent.mkdir()
            path.parent.chmod(0o777)
            path.touch()
            for cli in (False, True):
                with self.subTest(cli=cli), patch.dict(os.environ, {"TELEGRAM_LEADS_SESSION": str(path)}):
                    with self.assertLogs(level="WARNING") as logs:
                        secure_session(path, explicit=True if cli else None)
                    self.assertIn("доступен на запись", logs.output[0])
                    self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o777)
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
