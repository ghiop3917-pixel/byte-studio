"""Независимые циклы сбора, обработки и доставки внутри одного процесса."""

import asyncio
import json
import time
import sqlite3

from telethon import errors, utils

from reliability import (
    MAX_ATTEMPTS, acquire_lock, claim, event, finish_notification, heartbeat,
    ingest, process_one, recover, renew_lock, retry,
)


def source_error(db, name, error):
    row = db.execute("SELECT attempts FROM source_health WHERE username=?", (name,)).fetchone()
    attempts = row["attempts"] + 1
    flood = isinstance(error, errors.FloodWaitError)
    status = "retry_wait" if attempts < MAX_ATTEMPTS else "failed"
    delay = error.seconds + 1 if flood else min(30 * 2 ** (attempts - 1), 3600)
    db.execute(
        "UPDATE source_health SET status=?,attempts=?,due_at=?,error=? WHERE username=?",
        (status, attempts, time.time() + delay, type(error).__name__, name),
    )
    event(db, "source", name, type(error).__name__)
    if flood:
        # Общий лимит метода учитывается также для остальных источников.
        method = "resolve" if getattr(error, "request", None) and (
            "ResolveUsername" in type(error.request).__name__
        ) else "history"
        heartbeat(db, f"cooldown:{method}", str(time.time() + delay))


def cooldown(db, method):
    row = db.execute("SELECT detail FROM core_heartbeat WHERE component=?", (f"cooldown:{method}",)).fetchone()
    return float(row["detail"]) if row else 0


def telegram_message(group, message):
    sender = message.sender
    name = " ".join(filter(None, (getattr(sender, "first_name", None), getattr(sender, "last_name", None))))
    return {
        "chat": {"id": utils.get_peer_id(group), "type": "supergroup",
                 "title": group.title, "username": group.username},
        "message_id": message.id, "text": message.raw_text or "",
        "date": message.date.isoformat() if message.date else None,
        "media_type": type(message.media).__name__ if message.media else None,
        "from": {"id": message.sender_id, "name": name or getattr(sender, "title", None),
                 "username": getattr(sender, "username", None) or "",
                 "is_bot": getattr(sender, "bot", False)},
    }


async def collect_group(client, db, group, recipient):
    chat_id = utils.get_peer_id(group)
    row = db.execute("SELECT message_id FROM cursors WHERE chat_id=?", (chat_id,)).fetchone()
    if row is None:
        # Новый источник начинает с последних 100 доступных сообщений.
        messages = list(reversed(await client.get_messages(group, limit=100)))
        for message in messages:
            ingest(db, telegram_message(group, message), recipient)
    else:
        async for message in client.iter_messages(group, min_id=row["message_id"], reverse=True, limit=100):
            ingest(db, telegram_message(group, message), recipient)
    return chat_id


async def source_loop(client, db, sources, resolve, recipient, groups):
    while True:
        for name in sources():
            db.execute("INSERT OR IGNORE INTO source_health(username) VALUES (?)", (name,))
        rows = db.execute(
            "SELECT * FROM source_health WHERE status != 'failed' AND due_at<=? ORDER BY due_at,username",
            (time.time(),),
        ).fetchall()
        selected = next((row for row in rows if row["username"] in sources() and
                         time.time() >= cooldown(db, "history") and
                         (row["username"] in groups or time.time() >= cooldown(db, "resolve"))), None)
        if selected is None:
            await asyncio.sleep(1)
            continue
        name = selected["username"]
        try:
            async def fetch():
                if not client.is_connected():
                    await client.connect()
                if name not in groups:
                    groups[name] = await resolve(client, name)
                return await collect_group(client, db, groups[name], recipient())

            chat_id = await asyncio.wait_for(fetch(), timeout=45)
            db.execute(
                "UPDATE source_health SET status='healthy',attempts=0,due_at=?,chat_id=?,"
                "last_success=?,error=NULL WHERE username=?", (time.time() + 20, chat_id, time.time(), name),
            )
        except (errors.RPCError, OSError, asyncio.TimeoutError, ValueError) as error:
            source_error(db, name, error)
        await asyncio.sleep(1)


async def processing_loop(db, directions):
    while True:
        recover(db)
        for _ in range(20):
            if not process_one(db, directions):
                break
        heartbeat(db, "processing")
        await asyncio.sleep(1)


async def notification_loop(db, telegram, directions):
    while True:
        job = claim(db, "notification_queue")
        if job is not None:
            from delivery_filter import delivery_decision, suppress_notification
            try:
                allowed, reason = delivery_decision(db, job, directions(job['recipient']))
            except Exception as error:
                retry(db,'notification_queue',job,'FilterError:'+type(error).__name__)
                heartbeat(db,'notifications')
                await asyncio.sleep(1)
                continue
            if not allowed:
                suppress_notification(db, job, reason)
                heartbeat(db,'notifications')
                await asyncio.sleep(1)
                continue
            # Транзакция завершена до первого сетевого запроса.
            payload = json.loads(job["payload"])
            try:
                result = await asyncio.to_thread(telegram.send, **payload)
            except Exception as error:
                finish_notification(db, job, error=error)
            else:
                finish_notification(db, job, result=result)
        heartbeat(db, "notifications")
        await asyncio.sleep(1)


async def watch(db, lock_token, groups, tasks):
    started = time.time()
    while True:
        stop = db.execute("SELECT updated_at FROM core_heartbeat WHERE component='stop_request'").fetchone()
        if stop and stop["updated_at"] >= started:
            return
        renew_lock(db, "monitor", lock_token)
        heartbeat(db, "monitor", json.dumps({"resolved_groups": len(groups)}))
        for task in tasks:
            if task.done():
                error = task.exception()
                raise RuntimeError("Остановился рабочий цикл.") from error
        await asyncio.sleep(5)


async def maintenance_loop(db, recipient):
    from maintenance import run_tick
    database = db.execute('PRAGMA database_list').fetchone()[2]
    failures = 0
    paused_at = None
    while True:
        if failures >= 3:
            resume = db.execute('SELECT updated_at FROM core_heartbeat WHERE component=?',(f'maintenance_resume:{recipient()}',)).fetchone()
            if resume and resume['updated_at'] > paused_at:
                failures = 0
            else:
                heartbeat(db,'maintenance','paused_after_errors')
                await asyncio.sleep(60)
                continue
        try:
            result = await asyncio.to_thread(run_tick,database,recipient())
            failures = 0
            heartbeat(db,'maintenance',json.dumps(result or {'status':'idle'}))
            delay = 5 if result else 30
        except (sqlite3.Error,OSError,ValueError,RuntimeError) as error:
            failures += 1
            paused_at = time.time()
            event(db,'maintenance','scheduler',type(error).__name__)
            heartbeat(db,'maintenance','attention')
            # Обслуживание не останавливает сбор сообщений; после 3 сбоев — длинная пауза.
            delay = min(60*2**min(failures,6),3600)
        await asyncio.sleep(delay)


async def run(client, db, telegram, sources, resolve, recipient, directions, menu_runner):
    token = acquire_lock(db, "monitor")
    groups = {}
    tasks = []
    try:
        recover(db)
        tasks = [
            asyncio.create_task(source_loop(client, db, sources, resolve, recipient, groups)),
            asyncio.create_task(processing_loop(db, directions)),
            asyncio.create_task(notification_loop(db, telegram, directions)),
            asyncio.create_task(menu_runner(telegram, recipient, lambda: len(groups))),
            asyncio.create_task(maintenance_loop(db, recipient)),
        ]
        print(f"Надёжный мониторинг запущен. Источников: {len(sources())}.", flush=True)
        await watch(db, token, groups, tasks)
    except Exception as error:
        event(db, "monitor", "runtime", type(error).__name__)
        raise
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        db.execute("UPDATE core_locks SET lease_until=0 WHERE name='monitor' AND token=?", (token,))
        heartbeat(db, "monitor", "stopped")
