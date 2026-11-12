"""Ротация удаляет только явно выбранные временные тестовые файлы."""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backup_rotation import rotate_backups


class BackupRotationTests(unittest.TestCase):
    def test_dry_run_and_apply_keep_fourteen_per_type(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = sqlite3.connect(":memory:")
            self.addCleanup(db.close)
            db.execute("CREATE TABLE backup_records(path,job_name)")
            for number in range(16):
                names = [
                    f"before-core-{number}.sqlite3",
                    f"before-web-{number}.sqlite3",
                    f"scheduled-{number}-abcdef.sqlite3",
                    f"scheduled-{number}-123abc.sqlite3",
                ]
                for name in names:
                    path = root / name
                    path.write_bytes(b"test-copy")
                    os.utime(path, (number + 1, number + 1))
                db.execute("INSERT INTO backup_records VALUES (?,?)", (names[2], "backup:9"))
                db.execute("INSERT INTO backup_records VALUES (?,?)", (names[3], "intake_attempts_cleanup"))
            for name in ("unknown.sqlite3", "scheduled-1-abcdef.partial", "monitor.session"):
                (root / name).write_text("preserve")
            nested = root / "archived-project"
            nested.mkdir()
            (nested / "before-core-0.sqlite3").write_text("preserve")
            before = sorted(p.name for p in root.iterdir())
            plan = rotate_backups(root, db=db)
            self.assertTrue(plan["dry_run"])
            self.assertEqual(len(plan["candidates"]), 8)
            self.assertEqual(plan["deleted"], [])
            self.assertEqual(sorted(p.name for p in root.iterdir()), before)
            applied = rotate_backups(root, db=db, dry_run=False)
            self.assertEqual(len(applied["deleted"]), 8)
            self.assertEqual(set(rotate_backups(root, db=db)["groups"].values()), {14})
            self.assertTrue((root / "unknown.sqlite3").exists())
            self.assertTrue((nested / "before-core-0.sqlite3").exists())
            self.assertEqual(db.execute("SELECT count(*) FROM backup_records").fetchone()[0], 32)

    def test_invalid_retention_and_missing_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            for keep in (0, -1, True):
                with self.assertRaises(ValueError):
                    rotate_backups(folder, keep=keep)
            self.assertEqual(rotate_backups(Path(folder) / "absent")["candidates"], [])
