"""Ротация SQLite-копий: по умолчанию только план, без удаления файлов."""

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path

MIGRATION_COPY = re.compile(r"before-(core|crm|analysis|web|operations)-\d+\.sqlite3")
SCHEDULED_COPY = re.compile(r"scheduled-\d+-[0-9a-f]+\.sqlite3")


def rotate_backups(folder, *, db=None, keep=14, dry_run=True):
    if type(keep) is not int or keep < 1:
        raise ValueError("Нужно хранить минимум одну копию каждого типа.")
    folder = Path(folder).resolve()
    if not folder.is_dir():
        return {"dry_run": dry_run, "keep": keep, "groups": {}, "candidates": [], "deleted": []}
    records = dict(db.execute("SELECT path,job_name FROM backup_records")) if db is not None else {}
    groups = defaultdict(list)
    for path in folder.iterdir():
        if path.is_symlink() or not path.is_file():
            continue
        match = MIGRATION_COPY.fullmatch(path.name)
        if match:
            kind = "before-" + match[1]
        elif SCHEDULED_COPY.fullmatch(path.name):
            job = records.get(path.name)
            kind = "scheduled-" + (job.split(":", 1)[0] if job else "unclassified")
        else:
            continue
        info = path.stat()
        groups[kind].append((info.st_mtime_ns, path.name, info.st_size))
    candidates = []
    for kind, copies in sorted(groups.items()):
        for modified, name, size in sorted(copies, reverse=True)[keep:]:
            candidates.append({"type": kind, "name": name, "size": size, "mtime_ns": modified})
    deleted = []
    if not dry_run:
        for item in candidates:
            path = folder / item["name"]
            # Удаляются только проверенные файлы непосредственно в выбранном каталоге.
            if path.is_symlink() or path.resolve().parent != folder:
                raise ValueError("Путь копии изменился; ротация остановлена.")
            info = path.stat()
            if (info.st_mtime_ns, info.st_size) != (item["mtime_ns"], item["size"]):
                raise RuntimeError("Файл копии изменился; ротация остановлена.")
            path.unlink()
            deleted.append(item["name"])
    return {
        "dry_run": dry_run,
        "keep": keep,
        "groups": {kind: len(copies) for kind, copies in sorted(groups.items())},
        "candidates": candidates,
        "deleted": deleted,
    }


def main():
    parser = argparse.ArgumentParser(description="План ротации SQLite-копий: 14 последних каждого типа")
    parser.add_argument("--database", default=str(Path(__file__).with_name("monitor.sqlite3")))
    parser.add_argument("--keep", type=int, default=14)
    parser.add_argument("--apply", action="store_true", help="Удалить кандидатов; только после согласования владельцем")
    args = parser.parse_args()
    database = Path(args.database).resolve()
    # Только чтение метаданных; записи backup_records сохраняются как история.
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        result = rotate_backups(database.parent / "backups", db=db, keep=args.keep, dry_run=not args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
