"""Загрузка существующей Telethon-сессии только для чтения, без изменения прав."""

import sqlite3
from contextlib import closing
from pathlib import Path

from telethon.crypto import AuthKey
from telethon.sessions import MemorySession


def load_session(path: Path) -> MemorySession:
    path = path.expanduser().resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        row = db.execute("SELECT dc_id,server_address,port,auth_key FROM sessions").fetchone()
    if not row or not row[3]:
        raise ValueError("Сессия не содержит сохранённой авторизации.")
    session = MemorySession()
    session.set_dc(*row[:3])
    session.auth_key = AuthKey(row[3])
    return session
