"""Версия 5: наблюдаемость, задания обслуживания и предложения качества."""
import sqlite3
import time
from pathlib import Path
from contextlib import closing
from reliability import transaction


def migrate_operations(db, backup_required=True):
    if not db.execute('SELECT 1 FROM core_migrations WHERE version=3').fetchone():
        return
    if db.execute('SELECT 1 FROM core_migrations WHERE version=5').fetchone():
        return
    path=Path(db.execute('PRAGMA database_list').fetchone()[2])
    if backup_required and path.is_file():
        folder=path.parent/'backups';folder.mkdir(exist_ok=True)
        with closing(sqlite3.connect(folder/f'before-operations-{time.time_ns()}.sqlite3')) as copy:
            db.backup(copy)
            if copy.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise RuntimeError('Резервная копия перед этапом 7 повреждена.')
    with transaction(db):
        if db.execute('SELECT 1 FROM core_migrations WHERE version=5').fetchone():return
        statements=[
            '''CREATE TABLE quality_suggestions (
                id INTEGER PRIMARY KEY,owner_id INTEGER NOT NULL,kind TEXT NOT NULL,
                fingerprint TEXT NOT NULL,title TEXT NOT NULL,evidence TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
                created_at REAL NOT NULL,decided_at REAL,decision_note TEXT,
                UNIQUE(owner_id,fingerprint))''',
            '''CREATE TABLE maintenance_jobs (
                name TEXT PRIMARY KEY,owner_id INTEGER NOT NULL,status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,next_run REAL NOT NULL,
                started_at REAL,finished_at REAL,last_success REAL,lease_until REAL,token TEXT,error TEXT,result TEXT)''',
            '''CREATE TABLE suggestion_decisions (
                id INTEGER PRIMARY KEY,suggestion_id INTEGER NOT NULL REFERENCES quality_suggestions(id),
                owner_id INTEGER NOT NULL,decision TEXT NOT NULL,note TEXT,created_at REAL NOT NULL)''',
            '''CREATE TABLE maintenance_runs (
                id INTEGER PRIMARY KEY,job_name TEXT NOT NULL REFERENCES maintenance_jobs(name),
                owner_id INTEGER NOT NULL,started_at REAL NOT NULL,finished_at REAL,
                status TEXT NOT NULL,error TEXT,result TEXT)''',
            '''CREATE TABLE system_snapshots (
                id INTEGER PRIMARY KEY,owner_id INTEGER NOT NULL,kind TEXT NOT NULL,
                created_at REAL NOT NULL,report TEXT NOT NULL)''',
            '''CREATE TABLE backup_records (
                id INTEGER PRIMARY KEY,owner_id INTEGER NOT NULL,path TEXT NOT NULL UNIQUE,
                created_at REAL NOT NULL,size INTEGER NOT NULL,sha256 TEXT NOT NULL,
                integrity TEXT NOT NULL,job_name TEXT)''',
            'CREATE INDEX suggestions_owner_status ON quality_suggestions(owner_id,status,id)',
            'CREATE INDEX suggestion_decision_history ON suggestion_decisions(suggestion_id,id)',
            'CREATE INDEX maintenance_due ON maintenance_jobs(status,next_run)',
            'CREATE INDEX runs_owner_started ON maintenance_runs(owner_id,started_at)',
            'CREATE INDEX snapshots_owner_kind ON system_snapshots(owner_id,kind,created_at)',
            'CREATE INDEX backups_owner_created ON backup_records(owner_id,created_at)',
            'CREATE INDEX analysis_owner_raw_latest ON message_analyses(recipient,raw_id,id)',
            'CREATE INDEX lead_messages_owner_lead ON lead_messages(recipient,lead_id)',
            'CREATE INDEX raw_received_chat ON raw_messages(received_at,chat_id)',
        ]
        for statement in statements:db.execute(statement)
        db.execute('INSERT INTO core_migrations VALUES (5,?)',(time.time(),))
