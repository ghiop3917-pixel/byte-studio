"""Локальные операции восстановления без отправки сообщений."""

import argparse
import json

from bot_menu import MONITOR_DB
from reliability import heartbeat, migrate, open_database, reprocess, retry_notification


def main():
    parser = argparse.ArgumentParser(description="Состояние и восстановление Telegram-ядра")
    parser.add_argument("--reprocess", type=int, metavar="MESSAGE_ID")
    parser.add_argument("--retry-notification", type=int, metavar="NOTIFICATION_ID")
    parser.add_argument("--retry-source", metavar="USERNAME")
    parser.add_argument("--stop", action="store_true", help="Сохранить состояние и остановить мониторинг")
    args = parser.parse_args()
    db = open_database(MONITOR_DB)
    try:
        migrate(db)
        if args.stop:
            heartbeat(db, "stop_request")
        if args.reprocess is not None:
            reprocess(db, args.reprocess)
        if args.retry_notification is not None:
            print("Внимание: повтор неоднозначной доставки может создать дубль в Telegram.")
            retry_notification(db, args.retry_notification)
        if args.retry_source:
            db.execute("UPDATE source_health SET status='pending',attempts=0,due_at=0 WHERE username=?",
                       (args.retry_source.lstrip("@"),))
        for table in ("processing_queue", "notification_queue", "source_health"):
            print(table, json.dumps(dict(db.execute(f"SELECT status,count(*) FROM {table} GROUP BY status")),
                                    ensure_ascii=False))
        print("Heartbeat", [dict(row) for row in db.execute("SELECT * FROM core_heartbeat")])
    finally:
        db.close()


if __name__ == "__main__":
    main()
