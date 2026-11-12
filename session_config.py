"""Единый переносимый путь Telegram-сессии."""

import logging
import os
import stat
from pathlib import Path


def session_path():
    explicit = os.environ.get("TELEGRAM_LEADS_SESSION")
    if explicit:
        return Path(explicit).expanduser().resolve()
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "TelegramLeads" / "monitor.session"
    root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")).expanduser()
    return root / "telegram-leads" / "monitor.session"


def secure_session(path, *, explicit=None):
    """Ограничение доступа к сессии на POSIX без изменения авторизации."""
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        if explicit is None:
            explicit = bool(os.environ.get("TELEGRAM_LEADS_SESSION")) or path != session_path().resolve()
        if explicit:
            if stat.S_IMODE(path.parent.stat().st_mode) & 0o022:
                logging.warning(
                    "Каталог явно заданной Telegram-сессии доступен на запись группе или всем: %s", path.parent
                )
        else:
            os.chmod(path.parent, 0o700)
        if path.exists():
            os.chmod(path, 0o600)
    return path
