"""Долговечные сообщения, задачи и исходящие уведомления."""

import json
import logging
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

from bot import TelegramError, handle_update, message_link

MAX_ATTEMPTS = 5
LEASE_SECONDS = 120


def open_database(path):
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=10000")
    return db


@contextmanager
def transaction(db, immediate=True):
    if db.in_transaction:
        savepoint = "nested_" + uuid.uuid4().hex
        db.execute(f"SAVEPOINT {savepoint}")
        try:
            yield
        except BaseException:
            db.execute(f"ROLLBACK TO {savepoint}")
            db.execute(f"RELEASE {savepoint}")
            raise
        else:
            db.execute(f"RELEASE {savepoint}")
        return
    db.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield
    except BaseException:
        db.rollback()
        raise
    else:
        db.commit()


def migrate(db):
    # Резервная копия создаётся до первого изменения существующей схемы.
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='core_migrations'").fetchone():
        from crm_migrations import migrate_crm
        from analysis_migrations import migrate_analysis
        migrate_crm(db)
        migrate_analysis(db)
        from web_migrations import migrate_web
        migrate_web(db)
        from operations_migrations import migrate_operations
        migrate_operations(db)
        return
    path = Path(db.execute("PRAGMA database_list").fetchone()[2])
    if path.is_file():
        folder = path.parent / "backups"
        folder.mkdir(exist_ok=True)
        backup = folder / f"before-core-{time.time_ns()}.sqlite3"
        with closing(sqlite3.connect(backup)) as destination:
            db.backup(destination)
            if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Резервная копия не прошла проверку.")
    statements = [
        "CREATE TABLE IF NOT EXISTS delivered (fingerprint TEXT PRIMARY KEY, sent_at INTEGER NOT NULL)",
        "CREATE TABLE IF NOT EXISTS cursors (chat_id INTEGER PRIMARY KEY, message_id INTEGER NOT NULL)",
        """CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY, recipient INTEGER NOT NULL, title TEXT NOT NULL,
            source TEXT NOT NULL, url TEXT, saved INTEGER NOT NULL DEFAULT 0)""",
        """CREATE TABLE raw_messages (
            id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
            source TEXT NOT NULL, url TEXT, original_text TEXT NOT NULL,
            published_at TEXT, author_id INTEGER, author_name TEXT, language TEXT,
            received_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            payload TEXT NOT NULL, result TEXT, UNIQUE(chat_id,message_id))""",
        """CREATE TABLE processing_queue (
            id INTEGER PRIMARY KEY, raw_id INTEGER NOT NULL UNIQUE REFERENCES raw_messages(id),
            status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            due_at REAL NOT NULL DEFAULT 0, lease_until REAL, token TEXT,
            last_attempt REAL, error TEXT)""",
        """CREATE TABLE lead_messages (
            raw_id INTEGER NOT NULL REFERENCES raw_messages(id),
            recipient INTEGER NOT NULL, lead_id INTEGER NOT NULL REFERENCES leads(id),
            PRIMARY KEY(raw_id,recipient))""",
        """CREATE TABLE notification_queue (
            id INTEGER PRIMARY KEY, lead_id INTEGER NOT NULL REFERENCES leads(id),
            recipient INTEGER NOT NULL, payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            due_at REAL NOT NULL DEFAULT 0, lease_until REAL, token TEXT,
            last_attempt REAL, error TEXT, telegram_message_id INTEGER,
            UNIQUE(lead_id,recipient))""",
        """CREATE TABLE source_health (
            username TEXT PRIMARY KEY, chat_id INTEGER, status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0, due_at REAL NOT NULL DEFAULT 0,
            last_success REAL, error TEXT)""",
        """CREATE TABLE core_events (
            id INTEGER PRIMARY KEY, created_at REAL NOT NULL, component TEXT NOT NULL,
            item_id TEXT, error TEXT NOT NULL)""",
        """CREATE TABLE core_heartbeat (
            component TEXT PRIMARY KEY, updated_at REAL NOT NULL, detail TEXT)""",
        """CREATE TABLE core_locks (
            name TEXT PRIMARY KEY, token TEXT NOT NULL, lease_until REAL NOT NULL)""",
        "CREATE INDEX processing_due ON processing_queue(status,due_at)",
        "CREATE INDEX notification_due ON notification_queue(status,due_at)",
        "CREATE INDEX messages_status ON raw_messages(status,received_at)",
        "CREATE INDEX source_due ON source_health(status,due_at)",
        "CREATE TABLE core_migrations (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)",
    ]
    with transaction(db):
        for statement in statements:
            db.execute(statement)
        db.execute("INSERT INTO core_migrations VALUES (1,?)", (time.time(),))
    from crm_migrations import migrate_crm
    migrate_crm(db, backup_required=False)
    from analysis_migrations import migrate_analysis
    migrate_analysis(db, backup_required=False)
    from web_migrations import migrate_web
    migrate_web(db, backup_required=False)
    from operations_migrations import migrate_operations
    migrate_operations(db, backup_required=False)


def event(db, component, item_id, error):
    # Только тип/код ошибки. Токены и содержимое сетевых исключений не журналируются.
    db.execute("INSERT INTO core_events(created_at,component,item_id,error) VALUES (?,?,?,?)",
               (time.time(), component, str(item_id), error))
    logging.warning("%s, %s: %s", component, item_id, error)


def heartbeat(db, component, detail=""):
    db.execute(
        "INSERT INTO core_heartbeat VALUES (?,?,?) ON CONFLICT(component) "
        "DO UPDATE SET updated_at=excluded.updated_at,detail=excluded.detail",
        (component, time.time(), detail),
    )


def acquire_lock(db, name):
    token = uuid.uuid4().hex
    now = time.time()
    with transaction(db):
        db.execute(
            "INSERT INTO core_locks VALUES (?,?,?) ON CONFLICT(name) DO UPDATE "
            "SET token=excluded.token,lease_until=excluded.lease_until "
            "WHERE core_locks.lease_until < ?",
            (name, token, now + LEASE_SECONDS, now),
        )
        row = db.execute("SELECT token FROM core_locks WHERE name=?", (name,)).fetchone()
    if row["token"] != token:
        raise RuntimeError("Другой экземпляр мониторинга уже работает.")
    return token


def renew_lock(db, name, token):
    updated = db.execute(
        "UPDATE core_locks SET lease_until=? WHERE name=? AND token=? AND lease_until>?",
        (time.time() + LEASE_SECONDS, name, token, time.time()),
    ).rowcount
    if not updated:
        raise RuntimeError("Потеряна блокировка мониторинга.")


def ingest(db, message, recipient):
    text = message.get("text") or message.get("caption") or ""
    author = message.get("from", {})
    chat = message["chat"]
    language = "ru" if any("а" <= char.casefold() <= "я" for char in text) else (
        "en" if any("a" <= char.casefold() <= "z" for char in text) else None
    )
    payload = json.dumps({"message": message, "recipient": recipient}, ensure_ascii=False)
    with transaction(db):
        db.execute(
            """INSERT INTO raw_messages (
                chat_id,message_id,source,url,original_text,published_at,author_id,
                author_name,language,received_at,payload) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(chat_id,message_id) DO NOTHING""",
            (chat["id"], message["message_id"], chat.get("title", str(chat["id"])),
             message_link(message), text, message.get("date"), author.get("id"),
             author.get("name"), language, time.time(), payload),
        )
        raw_id = db.execute("SELECT id FROM raw_messages WHERE chat_id=? AND message_id=?",
                            (chat["id"], message["message_id"])).fetchone()["id"]
        db.execute("INSERT OR IGNORE INTO processing_queue(raw_id) VALUES (?)", (raw_id,))
        db.execute(
            "INSERT INTO cursors VALUES (?,?) ON CONFLICT(chat_id) DO UPDATE "
            "SET message_id=MAX(cursors.message_id,excluded.message_id)",
            (chat["id"], message["message_id"]),
        )
    return raw_id


def recover(db):
    now = time.time()
    with transaction(db):
        db.execute(
            "UPDATE raw_messages SET status='pending' WHERE id IN "
            "(SELECT raw_id FROM processing_queue WHERE status='processing' AND lease_until<?)", (now,)
        )
        db.execute(
            "UPDATE processing_queue SET status=CASE WHEN attempts>=? THEN 'failed' ELSE 'pending' END,"
            "token=NULL,lease_until=NULL,error='lease_expired' WHERE status='processing' AND lease_until<?",
            (MAX_ATTEMPTS, now),
        )
        db.execute(
            "UPDATE raw_messages SET status='failed' WHERE id IN "
            "(SELECT raw_id FROM processing_queue WHERE status='failed')"
        )
        db.execute(
            "UPDATE notification_queue SET status='ambiguous',token=NULL,lease_until=NULL,"
            "error='delivery_interrupted' WHERE status='processing' AND lease_until<?", (now,)
        )


def claim(db, table):
    if table not in {"processing_queue", "notification_queue"}:
        raise ValueError("Неизвестная очередь.")
    now = time.time()
    with transaction(db):
        row = db.execute(
            f"SELECT * FROM {table} WHERE status IN ('pending','retry_wait') "
            "AND due_at<=? AND attempts<? ORDER BY id LIMIT 1", (now, MAX_ATTEMPTS)
        ).fetchone()
        if row is None:
            return None
        token = uuid.uuid4().hex
        db.execute(
            f"UPDATE {table} SET status='processing',attempts=attempts+1,last_attempt=?,"
            "lease_until=?,token=? WHERE id=?",
            (now, now + LEASE_SECONDS, token, row["id"]),
        )
        if table == "processing_queue":
            db.execute("UPDATE raw_messages SET status='processing' WHERE id=?", (row["raw_id"],))
        return db.execute(f"SELECT * FROM {table} WHERE id=?", (row["id"],)).fetchone()


def retry(db, table, job, error, delay=0):
    state = "failed" if job["attempts"] >= MAX_ATTEMPTS else "retry_wait"
    due = time.time() + max(delay, min(30 * 2 ** (job["attempts"] - 1), 3600))
    with transaction(db):
        updated = db.execute(
            f"UPDATE {table} SET status=?,due_at=?,error=?,token=NULL,lease_until=NULL "
            "WHERE id=? AND token=?", (state, due, error, job["id"], job["token"]),
        ).rowcount
        if not updated:
            return
        if table == "processing_queue":
            db.execute("UPDATE raw_messages SET status=? WHERE id=?", (state, job["raw_id"]))
        event(db, table, job["id"], error)


class NotificationCapture:
    def __init__(self):
        self.payload = None

    def send(self, recipient, text, link=None, **options):
        self.payload = {"chat_id": recipient, "text": text, "link": link, **options}


def process_one(db, direction_loader):
    job = claim(db, "processing_queue")
    if job is None:
        return False
    try:
        with transaction(db):
            current = db.execute("SELECT token FROM processing_queue WHERE id=?", (job["id"],)).fetchone()
            if current["token"] != job["token"]:
                return True
            raw = db.execute("SELECT * FROM raw_messages WHERE id=?", (job["raw_id"],)).fetchone()
            payload = json.loads(raw["payload"])
            recipient = payload["recipient"]
            message = payload["message"]
            directions = direction_loader(recipient)
            from quality import apply_score, store_analysis, update_review
            analysis_id, analysis = store_analysis(db, raw, recipient, directions)
            update_review(db, raw["id"], recipient, analysis_id, analysis)
            review = db.execute("SELECT * FROM message_reviews WHERE raw_id=? AND recipient=?",
                                (raw["id"], recipient)).fetchone()
            approved = review is not None and review["status"] == "approved"
            rejected = review is not None and review["status"] == "rejected"
            categories = analysis["categories"]
            if approved and not categories:
                previous = db.execute("SELECT result FROM message_analyses WHERE id=?",
                                      (review["analysis_id"],)).fetchone()
                categories = json.loads(previous["result"])["categories"]
                analysis = {**analysis, "categories": categories}
            capture = NotificationCapture()
            existing = db.execute("SELECT lead_id FROM lead_messages WHERE raw_id=? AND recipient=?",
                                  (raw["id"], recipient)).fetchone()
            if existing is None:
                existing = db.execute(
                    "SELECT lead_id FROM crm_message_keys WHERE owner_id=? AND platform='telegram' "
                    "AND source_key=? AND message_key=?",
                    (recipient, str(raw["chat_id"]), str(raw["message_id"])),
                ).fetchone()
                if existing:
                    db.execute("INSERT OR IGNORE INTO lead_messages VALUES (?,?,?)",
                               (raw["id"], recipient, existing["lead_id"]))
                    from crm import sync_telegram_lead
                    sync_telegram_lead(db, raw, recipient, existing["lead_id"], categories, {})
            if categories and not rejected and (approved or analysis["bucket"] == "lead") and existing is None:
                handle_update(capture, db, {"message": message}, recipient, {raw["chat_id"]}, "",
                              directions=directions, record_leads=True,
                              identity=f"{raw['chat_id']}:{raw['message_id']}:{recipient}",
                              manage_transaction=False, analysis=analysis)
                if capture.payload:
                    lead_id = capture.payload["lead_id"]
                    db.execute("INSERT INTO lead_messages VALUES (?,?,?)", (raw["id"], recipient, lead_id))
                    from crm import sync_telegram_lead
                    sync_telegram_lead(db, raw, recipient, lead_id, categories, capture.payload)
                    apply_score(db, lead_id, analysis)
                    db.execute(
                        "INSERT OR IGNORE INTO notification_queue(lead_id,recipient,payload) VALUES (?,?,?)",
                        (lead_id, recipient, json.dumps(capture.payload, ensure_ascii=False)),
                    )
            if existing:
                apply_score(db, existing["lead_id"], analysis)
            result = json.dumps(analysis, ensure_ascii=False)
            db.execute("UPDATE raw_messages SET status='completed',result=? WHERE id=?", (result, raw["id"]))
            db.execute(
                "UPDATE processing_queue SET status='completed',token=NULL,lease_until=NULL,error=NULL "
                "WHERE id=? AND token=?", (job["id"], job["token"]),
            )
        return True
    except Exception as error:
        retry(db, "processing_queue", job, type(error).__name__)
        return True


def finish_notification(db, job, result=None, error=None):
    with transaction(db):
        if error is None:
            db.execute(
                "UPDATE notification_queue SET status='completed',telegram_message_id=?,token=NULL,"
                "lease_until=NULL,error=NULL WHERE id=? AND token=?",
                (result["message_id"], job["id"], job["token"]),
            )
        elif isinstance(error, TelegramError) and error.code == 429:
            # Сервер явно отказал из-за частоты. Такую попытку можно повторять.
            pass
        else:
            ambiguous = not isinstance(error, TelegramError) or error.code == 0 or error.code >= 500
            state = "ambiguous" if ambiguous else "failed"
            code = f"Telegram:{error.code}" if isinstance(error, TelegramError) else type(error).__name__
            db.execute(
                "UPDATE notification_queue SET status=?,error=?,token=NULL,lease_until=NULL "
                "WHERE id=? AND token=?", (state, code, job["id"], job["token"]),
            )
            event(db, "notification_queue", job["id"], code)
    if isinstance(error, TelegramError) and error.code == 429:
        retry(db, "notification_queue", job, "Telegram:429", error.retry_after)


def reprocess(db, raw_id):
    with transaction(db):
        if not db.execute("SELECT 1 FROM raw_messages WHERE id=?", (raw_id,)).fetchone():
            raise ValueError("Сообщение не найдено.")
        queue = db.execute("SELECT status FROM processing_queue WHERE raw_id=?", (raw_id,)).fetchone()
        if queue["status"] == "processing":
            raise ValueError("Сообщение уже обрабатывается.")
        db.execute("UPDATE processing_queue SET status='pending',attempts=0,due_at=0,error=NULL WHERE raw_id=?",
                   (raw_id,))
        db.execute("UPDATE raw_messages SET status='pending' WHERE id=?", (raw_id,))


def retry_notification(db, notification_id):
    with transaction(db):
        updated = db.execute(
            "UPDATE notification_queue SET status='pending',attempts=0,due_at=0,error=NULL "
            "WHERE id=? AND status IN ('ambiguous','failed')", (notification_id,)
        ).rowcount
        if not updated:
            raise ValueError("Уведомление не найдено или уже находится в очереди.")
        event(db, "notification_queue", notification_id, "manual_retry")
