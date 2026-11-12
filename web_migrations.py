"""Версия 4: журнал заявок сайта и защита от повторов."""

import sqlite3
import time
from contextlib import closing
from pathlib import Path

from reliability import transaction


def migrate_web(db, backup_required=True):
    if db.execute("SELECT 1 FROM core_migrations WHERE version=4").fetchone():
        return
    path = Path(db.execute("PRAGMA database_list").fetchone()[2])
    if backup_required and path.is_file():
        folder = path.parent / "backups"
        folder.mkdir(exist_ok=True)
        with closing(sqlite3.connect(folder / f"before-web-{time.time_ns()}.sqlite3")) as copy:
            db.backup(copy)
            if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Резервная копия не прошла проверку.")
    with transaction(db):
        if db.execute("SELECT 1 FROM core_migrations WHERE version=4").fetchone():
            return
        db.execute("""CREATE TABLE web_intakes (
            id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL, request_id TEXT NOT NULL,
            request_hash TEXT NOT NULL, lead_id INTEGER NOT NULL REFERENCES leads(id),
            created_at REAL NOT NULL, UNIQUE(owner_id,request_id))""")
        db.execute("""CREATE TABLE web_intake_attempts (
            id INTEGER PRIMARY KEY, client_hash TEXT NOT NULL, created_at REAL NOT NULL,
            outcome TEXT NOT NULL)""")
        db.execute("CREATE INDEX web_attempts_rate ON web_intake_attempts(client_hash,created_at)")
        db.execute("INSERT INTO core_migrations VALUES (4,?)", (time.time(),))
