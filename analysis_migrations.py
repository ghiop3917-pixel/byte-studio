"""Версия 3 — анализ, очередь проверки и обратная связь."""

import sqlite3
import time
from contextlib import closing
from pathlib import Path

from reliability import transaction


def migrate_analysis(db, backup_required=True):
    if not db.execute("SELECT 1 FROM core_migrations WHERE version=2").fetchone():
        return
    if db.execute("SELECT 1 FROM core_migrations WHERE version=3").fetchone():
        return
    path = Path(db.execute("PRAGMA database_list").fetchone()[2])
    if backup_required and path.is_file():
        folder = path.parent / "backups"
        folder.mkdir(exist_ok=True)
        with closing(sqlite3.connect(folder / f"before-analysis-{time.time_ns()}.sqlite3")) as copy:
            db.backup(copy)
            if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Резервная копия анализа повреждена.")
    with transaction(db):
        if db.execute("SELECT 1 FROM core_migrations WHERE version=3").fetchone():
            return
        db.execute("ALTER TABLE leads ADD COLUMN score_origin TEXT")
        db.execute("UPDATE leads SET score_origin='manual' WHERE score IS NOT NULL")
        db.execute("""CREATE TABLE message_analyses (
            id INTEGER PRIMARY KEY, raw_id INTEGER NOT NULL REFERENCES raw_messages(id),
            recipient INTEGER NOT NULL, version TEXT NOT NULL, context_key TEXT NOT NULL,
            intent TEXT NOT NULL, score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 100),
            bucket TEXT NOT NULL, language TEXT, result TEXT NOT NULL, created_at REAL NOT NULL,
            UNIQUE(raw_id,recipient,version,context_key))""")
        db.execute("""CREATE TABLE message_reviews (
            raw_id INTEGER NOT NULL REFERENCES raw_messages(id), recipient INTEGER NOT NULL,
            analysis_id INTEGER NOT NULL REFERENCES message_analyses(id),
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
            reason TEXT, decided_at REAL, PRIMARY KEY(raw_id,recipient))""")
        db.execute("""CREATE TABLE review_events (
            id INTEGER PRIMARY KEY, raw_id INTEGER NOT NULL REFERENCES raw_messages(id),
            recipient INTEGER NOT NULL, decision TEXT NOT NULL, reason TEXT, created_at REAL NOT NULL)""")
        db.execute("""CREATE TABLE bot_feedback_receipts (
            callback_id TEXT PRIMARY KEY, recipient INTEGER NOT NULL, response TEXT NOT NULL)""")
        db.execute("CREATE INDEX analysis_owner_bucket ON message_analyses(recipient,bucket,id)")
        db.execute("CREATE INDEX reviews_owner_status ON message_reviews(recipient,status,raw_id)")
        db.execute("INSERT INTO core_migrations VALUES (3,?)", (time.time(),))
