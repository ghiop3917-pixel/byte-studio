"""Совместимая CRM-миграция существующей базы."""

import sqlite3
import time
from contextlib import closing
from pathlib import Path

from reliability import transaction


def migrate_crm(db, backup_required=True):
    if db.execute("SELECT 1 FROM core_migrations WHERE version=2").fetchone():
        return
    path = Path(db.execute("PRAGMA database_list").fetchone()[2])
    if backup_required and path.is_file():
        folder = path.parent / "backups"
        folder.mkdir(exist_ok=True)
        with closing(sqlite3.connect(folder / f"before-crm-{time.time_ns()}.sqlite3")) as copy:
            db.backup(copy)
            if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Резервная копия CRM повреждена.")
    statements = [
        """CREATE TABLE clients (
            id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL,
            kind TEXT NOT NULL DEFAULT 'person' CHECK(kind IN ('person','company')),
            platform TEXT, public_id TEXT, name TEXT, contact TEXT,
            company_id INTEGER REFERENCES clients(id), created_at REAL NOT NULL,
            UNIQUE(owner_id,platform,public_id))""",
        """CREATE TABLE sources (
            id INTEGER PRIMARY KEY, platform TEXT NOT NULL, external_id TEXT NOT NULL,
            name TEXT NOT NULL, url TEXT, UNIQUE(platform,external_id))""",
        """CREATE TABLE lead_sources (
            id INTEGER PRIMARY KEY, lead_id INTEGER NOT NULL REFERENCES leads(id),
            source_id INTEGER NOT NULL REFERENCES sources(id),
            raw_id INTEGER REFERENCES raw_messages(id), publication_url TEXT,
            external_message_id TEXT, UNIQUE(lead_id,source_id,external_message_id))""",
        """CREATE TABLE crm_message_keys (
            owner_id INTEGER NOT NULL, platform TEXT NOT NULL, source_key TEXT NOT NULL,
            message_key TEXT NOT NULL, lead_id INTEGER NOT NULL REFERENCES leads(id),
            PRIMARY KEY(owner_id,platform,source_key,message_key))""",
        """CREATE TABLE conversations (
            id INTEGER PRIMARY KEY, lead_id INTEGER NOT NULL REFERENCES leads(id),
            client_id INTEGER REFERENCES clients(id),
            direction TEXT NOT NULL CHECK(direction IN ('incoming','outgoing','system')),
            text TEXT NOT NULL, occurred_at REAL NOT NULL,
            provenance TEXT NOT NULL, external_key TEXT UNIQUE, created_at REAL NOT NULL)""",
        """CREATE TABLE drafts (
            id INTEGER PRIMARY KEY, lead_id INTEGER NOT NULL REFERENCES leads(id),
            text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft'
            CHECK(status IN ('draft','approved','rejected','sent')),
            created_at REAL NOT NULL, updated_at REAL NOT NULL,
            sent_conversation_id INTEGER UNIQUE REFERENCES conversations(id))""",
        """CREATE TABLE deals (
            id INTEGER PRIMARY KEY, lead_id INTEGER NOT NULL REFERENCES leads(id),
            title TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('open','won','lost')),
            actual_revenue TEXT, currency TEXT, created_at REAL NOT NULL, closed_at REAL)""",
        """CREATE TABLE activities (
            id INTEGER PRIMARY KEY, lead_id INTEGER NOT NULL REFERENCES leads(id),
            actor TEXT NOT NULL, kind TEXT NOT NULL, details TEXT NOT NULL, created_at REAL NOT NULL)""",
        """CREATE TABLE notes (
            id INTEGER PRIMARY KEY, lead_id INTEGER NOT NULL REFERENCES leads(id),
            text TEXT NOT NULL, created_at REAL NOT NULL)""",
        """CREATE TABLE reminders (
            id INTEGER PRIMARY KEY, lead_id INTEGER NOT NULL REFERENCES leads(id),
            text TEXT NOT NULL, due_at REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','completed','cancelled')),
            created_at REAL NOT NULL, updated_at REAL NOT NULL)""",
        """CREATE TABLE lead_feedback (
            id INTEGER PRIMARY KEY, lead_id INTEGER NOT NULL REFERENCES leads(id),
            decision TEXT NOT NULL CHECK(decision IN ('approved','rejected','needs_review')),
            reason TEXT, created_at REAL NOT NULL)""",
        """CREATE TABLE lead_categories (
            lead_id INTEGER NOT NULL REFERENCES leads(id), category TEXT NOT NULL,
            PRIMARY KEY(lead_id,category))""",
        """CREATE TABLE lead_merges (
            id INTEGER PRIMARY KEY, target_id INTEGER NOT NULL REFERENCES leads(id),
            secondary_id INTEGER NOT NULL REFERENCES leads(id), owner_id INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL, split_at REAL)""",
        """CREATE TABLE lead_merge_items (
            merge_id INTEGER NOT NULL REFERENCES lead_merges(id),
            lead_id INTEGER NOT NULL REFERENCES leads(id), previous_parent INTEGER REFERENCES leads(id),
            PRIMARY KEY(merge_id,lead_id))""",
        """CREATE TABLE crm_api_requests (
            owner_id INTEGER NOT NULL, request_key TEXT NOT NULL, request_hash TEXT NOT NULL,
            content_type TEXT NOT NULL, response BLOB NOT NULL, created_at REAL NOT NULL,
            PRIMARY KEY(owner_id,request_key))""",
    ]
    columns = {
        "platform": "TEXT NOT NULL DEFAULT 'telegram'",
        "original_text": "TEXT", "task_description": "TEXT",
        "discovered_at": "REAL", "updated_at": "REAL",
        "client_id": "INTEGER REFERENCES clients(id)",
        "status": "TEXT NOT NULL DEFAULT 'new'",
        "priority": "TEXT NOT NULL DEFAULT 'normal'",
        "score": "INTEGER CHECK(score IS NULL OR (score>=0 AND score<=100))",
        "confirmed_budget": "TEXT", "estimated_budget_min": "TEXT", "estimated_budget_max": "TEXT",
        "recommended_price": "TEXT", "minimum_price": "TEXT", "desired_price": "TEXT",
        "currency": "TEXT", "estimated_timeline": "TEXT",
        "merged_into": "INTEGER REFERENCES leads(id)",
    }
    indexes = [
        "CREATE INDEX crm_lead_filter ON leads(recipient,merged_into,status,platform)",
        "CREATE INDEX crm_lead_client ON leads(client_id)",
        "CREATE INDEX crm_lead_source ON lead_sources(lead_id)",
        "CREATE INDEX crm_activity_time ON activities(lead_id,created_at)",
        "CREATE INDEX crm_conversation_time ON conversations(lead_id,occurred_at)",
        "CREATE INDEX crm_reminders_due ON reminders(status,due_at)",
        "CREATE INDEX crm_categories ON lead_categories(category,lead_id)",
        "CREATE INDEX crm_deals ON deals(lead_id,status)",
    ]
    with transaction(db):
        if db.execute("SELECT 1 FROM core_migrations WHERE version=2").fetchone():
            return
        for statement in statements:
            db.execute(statement)
        for name, definition in columns.items():
            db.execute(f"ALTER TABLE leads ADD COLUMN {name} {definition}")
        for statement in indexes:
            db.execute(statement)
        # Старые краткие карточки остаются карточками с неизвестным оригиналом и датой.
        from crm import hydrate_legacy
        hydrate_legacy(db)
        db.execute("INSERT INTO core_migrations VALUES (2,?)", (time.time(),))
