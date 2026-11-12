import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from maintenance import run_tick
from reliability import migrate, open_database
from web_intake import RateLimited, submit


class IntakeRetentionTests(unittest.TestCase):
    def test_rejected_attempts_stop_growing_at_fifty(self):
        db = open_database(":memory:")
        self.addCleanup(db.close)
        migrate(db)
        for _ in range(50):
            with self.assertRaises(ValueError):
                submit(db, 9, {}, "client")
        for _ in range(3):
            with self.assertRaises(RateLimited):
                submit(db, 9, {}, "client")
        self.assertEqual(db.execute("SELECT count(*) FROM web_intake_attempts").fetchone()[0], 50)
        self.assertEqual(db.execute("SELECT count(*) FROM leads").fetchone()[0], 0)

    def test_health_job_deletes_only_expired_attempts_after_backup(self):
        now = 2_000_000_000
        cutoff = now - 7 * 86400
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "monitor.sqlite3"
            db = open_database(path)
            migrate(db)
            db.executemany(
                "INSERT INTO web_intake_attempts(client_hash,created_at,outcome) VALUES (?,?,?)",
                [("old", cutoff - 1, "rejected"), ("boundary", cutoff, "accepted"), ("recent", now, "rejected")],
            )
            # Из существующих заданий запускается health; новых типов заданий нет.
            db.execute("INSERT INTO maintenance_jobs(name,owner_id,status,next_run) VALUES ('health:9',9,'pending',0)")
            db.close()
            with patch("maintenance.time.time", return_value=now):
                self.assertEqual(run_tick(path, 9, now), {"job": "health", "status": "completed"})
            db = open_database(path)
            try:
                self.assertEqual(
                    [r[0] for r in db.execute("SELECT client_hash FROM web_intake_attempts ORDER BY id")],
                    ["boundary", "recent"],
                )
                self.assertEqual(db.execute("SELECT count(*) FROM notification_queue").fetchone()[0], 0)
                self.assertEqual(
                    db.execute(
                        "SELECT integrity FROM backup_records WHERE job_name='intake_attempts_cleanup'"
                    ).fetchone()[0],
                    "ok",
                )
            finally:
                db.close()
