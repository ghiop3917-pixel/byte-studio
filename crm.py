"""CRM-сервис с проверкой владельца. Сетевых отправок здесь нет."""

import json
import re
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from urllib.parse import urlsplit, urlunsplit

from reliability import transaction

STATUSES = {
    "new": "Новый",
    "analyzing": "Анализируется",
    "verified": "Проверен",
    "promising": "Перспективный",
    "ready": "Готов к отклику",
    "draft_prepared": "Черновик подготовлен",
    "outreach_sent": "Отклик отправлен",
    "client_replied": "Клиент ответил",
    "negotiation": "Переговоры",
    "proposal_sent": "КП отправлено",
    "awaiting_decision": "Ожидание решения",
    "won": "Сделка заключена",
    "rejected": "Отказ",
    "archived": "Архив",
}
PRIORITIES = {"low", "normal", "high", "urgent"}
MONEY_FIELDS = {
    "confirmed_budget",
    "estimated_budget_min",
    "estimated_budget_max",
    "recommended_price",
    "minimum_price",
    "desired_price",
}
EDITABLE = MONEY_FIELDS | {
    "title",
    "task_description",
    "priority",
    "score",
    "currency",
    "estimated_timeline",
    "client_id",
}


class NotFound(ValueError):
    pass


class Conflict(ValueError):
    pass


def text(value, maximum=100000, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("Текст пустой или превышает допустимую длину.")
    return value.strip()


def identifier(value):
    if type(value) is not int or not 0 < value <= 9223372036854775807:
        raise ValueError("ID должен быть положительным целым числом.")
    return value


def amount(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("Денежные значения передавай десятичной строкой.")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError("Некорректная денежная сумма.") from None
    if not number.is_finite() or number < 0 or number > Decimal("1000000000000") or number.as_tuple().exponent < -4:
        raise ValueError("Денежная сумма вне допустимого диапазона.")
    return format(number, "f")


def currency(value):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z]{3}", value):
        raise ValueError("Укажи трёхбуквенный код валюты.")
    return value.upper()


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Дата должна быть строкой ISO 8601 с часовым поясом.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Некорректная дата.") from None
    if parsed.tzinfo is None:
        raise ValueError("Укажи часовой пояс даты.")
    return parsed.timestamp()


def activity(db, lead_id, kind, details, actor="owner"):
    db.execute(
        "INSERT INTO activities(lead_id,actor,kind,details,created_at) VALUES (?,?,?,?,?)",
        (lead_id, actor, kind, json.dumps(details, ensure_ascii=False), time.time()),
    )


def source_record(db, platform, external_id, name, url=None):
    db.execute(
        "INSERT INTO sources(platform,external_id,name,url) VALUES (?,?,?,?) "
        "ON CONFLICT(platform,external_id) DO NOTHING",
        (platform, external_id, name, url),
    )
    return db.execute("SELECT id FROM sources WHERE platform=? AND external_id=?", (platform, external_id)).fetchone()[
        "id"
    ]


def client_record(db, owner, platform, public_id, name=None, contact=None):
    if public_id:
        existing = db.execute(
            "SELECT id FROM clients WHERE owner_id=? AND platform=? AND public_id=?",
            (owner, platform, public_id),
        ).fetchone()
        if existing:
            db.execute(
                "UPDATE clients SET name=COALESCE(name,?),contact=COALESCE(contact,?) WHERE id=?",
                (name, contact, existing["id"]),
            )
            return existing["id"]
    return db.execute(
        "INSERT INTO clients(owner_id,platform,public_id,name,contact,created_at) VALUES (?,?,?,?,?,?)",
        (owner, platform, public_id, name, contact, time.time()),
    ).lastrowid


def sync_telegram_lead(db, raw, recipient, lead_id, categories, notification):
    """Вызывается внутри транзакции очереди, без отдельных коммитов."""
    external_key = f"raw:{raw['id']}:{recipient}"
    if db.execute("SELECT 1 FROM conversations WHERE external_key=?", (external_key,)).fetchone():
        return
    registered = db.execute("SELECT 1 FROM activities WHERE lead_id=?", (lead_id,)).fetchone() is None
    payload = json.loads(raw["payload"])
    author = payload["message"].get("from", {})
    contact = notification.get("contact")
    sender_contact = f"https://t.me/{author['username']}" if author.get("username") else None
    if contact and contact != sender_contact:
        public_id, name = contact.rsplit("/", 1)[-1].casefold(), None
    else:
        public_id = f"user:{raw['author_id']}" if raw["author_id"] else (author.get("username") or None)
        name = raw["author_name"]
    client_id = db.execute("SELECT client_id FROM leads WHERE id=?", (lead_id,)).fetchone()["client_id"]
    if client_id is None and (public_id or name):
        client_id = client_record(db, recipient, "telegram", public_id, name, contact)
    db.execute(
        "UPDATE leads SET original_text=COALESCE(original_text,?),task_description=COALESCE(task_description,?),"
        "discovered_at=COALESCE(discovered_at,?),client_id=COALESCE(client_id,?),updated_at=? WHERE id=?",
        (raw["original_text"], raw["original_text"], raw["received_at"], client_id, time.time(), lead_id),
    )
    source_id = source_record(db, "telegram", str(raw["chat_id"]), raw["source"])
    db.execute(
        "INSERT INTO lead_sources(lead_id,source_id,raw_id,publication_url,external_message_id) "
        "VALUES (?,?,?,?,?) ON CONFLICT(lead_id,source_id,external_message_id) DO UPDATE "
        "SET raw_id=excluded.raw_id,publication_url=COALESCE(lead_sources.publication_url,excluded.publication_url)",
        (lead_id, source_id, raw["id"], raw["url"], str(raw["message_id"])),
    )
    db.execute(
        "INSERT OR IGNORE INTO crm_message_keys VALUES (?,?,?,?,?)",
        (recipient, "telegram", str(raw["chat_id"]), str(raw["message_id"]), lead_id),
    )
    for category in categories:
        db.execute("INSERT OR IGNORE INTO lead_categories VALUES (?,?)", (lead_id, category))
    occurred = raw["received_at"]
    if raw["published_at"]:
        try:
            occurred = timestamp(raw["published_at"])
        except ValueError:
            pass
    db.execute(
        "INSERT OR IGNORE INTO conversations(lead_id,client_id,direction,text,occurred_at,provenance,"
        "external_key,created_at) VALUES (?,?,'incoming',?,?, 'telegram',?,?)",
        (lead_id, client_id, raw["original_text"], occurred, external_key, time.time()),
    )
    # Восстанавливается только действительно существовавший черновик карточки.
    from html import unescape

    draft = notification.get("text", "").partition("<b>Черновик отклика</b>\n")[2]
    if draft:
        now = time.time()
        db.execute(
            "INSERT INTO drafts(lead_id,text,created_at,updated_at) VALUES (?,?,?,?)",
            (lead_id, unescape(draft), now, now),
        )
    activity(
        db,
        lead_id,
        "lead_registered" if registered else "message_attached",
        {"status": "new", "raw_id": raw["id"]} if registered else {"raw_id": raw["id"]},
        "telegram",
    )


def hydrate_legacy(db):
    for lead in db.execute("SELECT * FROM leads").fetchall():
        linked = db.execute(
            "SELECT r.* FROM raw_messages r JOIN lead_messages lm ON lm.raw_id=r.id WHERE lm.lead_id=? LIMIT 1",
            (lead["id"],),
        ).fetchone()
        if linked:
            queued = db.execute(
                "SELECT payload FROM notification_queue WHERE lead_id=? LIMIT 1", (lead["id"],)
            ).fetchone()
            result = json.loads(linked["result"]) if linked["result"] else {}
            sync_telegram_lead(
                db,
                linked,
                lead["recipient"],
                lead["id"],
                result.get("categories", []),
                json.loads(queued["payload"]) if queued else {},
            )
        else:
            source_id = source_record(db, "telegram", f"legacy:{lead['id']}", lead["source"])
            db.execute(
                "INSERT INTO lead_sources(lead_id,source_id,publication_url) VALUES (?,?,?)",
                (lead["id"], source_id, lead["url"]),
            )
            activity(db, lead["id"], "legacy_imported", {"original_available": False}, "migration")


class CRM:
    def __init__(self, db, owner_id):
        self.db = db
        self.owner = identifier(owner_id)
        self.db.create_function("casefold", 1, lambda value: (value or "").casefold(), deterministic=True)

    def lead(self, lead_id, writable=False):
        row = self.db.execute(
            "SELECT * FROM leads WHERE id=? AND recipient=?", (identifier(lead_id), self.owner)
        ).fetchone()
        if row is None:
            raise NotFound("Лид не найден.")
        if writable and row["merged_into"] is not None:
            raise Conflict("Лид объединён. Измени основную карточку или сначала раздели лиды.")
        return dict(row)

    def members(self, lead_id):
        self.lead(lead_id)
        return [
            row["id"]
            for row in self.db.execute(
                "SELECT id FROM leads WHERE recipient=? AND (id=? OR merged_into=?)",
                (self.owner, lead_id, lead_id),
            )
        ]

    def client(self, client_id):
        row = self.db.execute(
            "SELECT * FROM clients WHERE id=? AND owner_id=?", (identifier(client_id), self.owner)
        ).fetchone()
        if row is None:
            raise NotFound("Заказчик не найден.")
        result = dict(row)
        result["projects"] = [
            dict(item)
            for item in self.db.execute(
                "SELECT id,title,status,merged_into FROM leads WHERE client_id=? AND recipient=?",
                (client_id, self.owner),
            )
        ]
        return result

    def clients(self):
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM clients WHERE owner_id=? ORDER BY id DESC LIMIT 1000",
                (self.owner,),
            )
        ]

    def create_client(self, data):
        allowed = {"name", "contact", "platform", "public_id", "kind", "company_id"}
        if set(data) - allowed:
            raise ValueError("Неизвестное поле заказчика.")
        name = text(data.get("name"), 500, optional=True)
        contact = text(data.get("contact"), 1000, optional=True)
        platform = text(data.get("platform"), 40, optional=True)
        public_id = text(data.get("public_id"), 500, optional=True)
        kind = data.get("kind", "person")
        if kind not in {"person", "company"} or (public_id and not platform):
            raise ValueError("Проверь тип заказчика и платформу идентификатора.")
        company_id = data.get("company_id")
        if company_id is not None and self.client(company_id)["kind"] != "company":
            raise ValueError("Связь компании должна вести на компанию.")
        if not any((name, contact, public_id)):
            raise ValueError("Укажи имя, контакт или публичный идентификатор.")
        with transaction(self.db):
            if public_id:
                existing = self.db.execute(
                    "SELECT id FROM clients WHERE owner_id=? AND platform=? AND public_id=?",
                    (self.owner, platform, public_id),
                ).fetchone()
                if existing:
                    return self.client(existing["id"])
            client_id = client_record(self.db, self.owner, platform, public_id, name, contact)
            self.db.execute("UPDATE clients SET kind=?,company_id=? WHERE id=?", (kind, company_id, client_id))
        return self.client(client_id)

    def update_client(self, client_id, changes):
        if not changes or set(changes) - {"name", "contact", "company_id"}:
            raise ValueError("Публичный идентификатор клиента неизменяем. Проверь редактируемые поля.")
        with transaction(self.db):
            current = self.client(client_id)
            name = text(changes.get("name", current["name"]), 500, optional=True)
            contact = text(changes.get("contact", current["contact"]), 1000, optional=True)
            parent = changes.get("company_id", current["company_id"])
            ancestor = parent
            seen = {client_id}
            while ancestor is not None:
                if ancestor in seen:
                    raise Conflict("Связь компаний создаёт цикл.")
                seen.add(ancestor)
                company = self.client(ancestor)
                if company["kind"] != "company":
                    raise ValueError("Родительская карточка должна быть компанией.")
                ancestor = company["company_id"]
            self.db.execute(
                "UPDATE clients SET name=?,contact=?,company_id=? WHERE id=?", (name, contact, parent, client_id)
            )
            for lead in current["projects"]:
                activity(
                    self.db,
                    lead["id"],
                    "client_updated",
                    {"client_id": client_id, "before": {key: current[key] for key in changes}, "changes": changes},
                )
        return self.client(client_id)

    def create(self, data):
        allowed = {
            "title",
            "original_text",
            "platform",
            "source_key",
            "source_name",
            "external_message_id",
            "url",
            "client_id",
            "categories",
        }
        if set(data) - allowed:
            raise ValueError("Неизвестное поле нового лида.")
        title = text(data.get("title"), 500)
        original = text(data.get("original_text"), optional=True)
        platform = text(data.get("platform", "manual"), 40)
        source_key = text(data.get("source_key", "manual"), 500)
        source_name = text(data.get("source_name", "Ручной ввод"), 500)
        message_id = text(data.get("external_message_id"), 500, optional=True)
        url = text(data.get("url"), 2000, optional=True)
        if url and urlsplit(url).scheme not in {"http", "https"}:
            raise ValueError("Ссылка должна начинаться с http или https.")
        client_id = data.get("client_id")
        if client_id is not None:
            self.client(client_id)
        categories = data.get("categories", [])
        if not isinstance(categories, list) or len(categories) > 20:
            raise ValueError("Категории должны быть списком до 20 значений.")
        categories = [text(value, 200) for value in categories]
        with transaction(self.db):
            if message_id:
                duplicate = self.db.execute(
                    (
                        "SELECT lead_id FROM crm_message_keys WHERE owner_id=? AND platform=?"
                        " AND source_key=? AND message_key=?"
                    ),
                    (self.owner, platform, source_key, message_id),
                ).fetchone()
                if duplicate:
                    return self.card(duplicate["lead_id"])
            now = time.time()
            lead_id = self.db.execute(
                "INSERT INTO leads(recipient,title,source,url,platform,original_text,task_description,"
                "discovered_at,updated_at,client_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (self.owner, title, source_name, url, platform, original, original or title, now, now, client_id),
            ).lastrowid
            source_id = source_record(self.db, platform, source_key, source_name)
            self.db.execute(
                "INSERT INTO lead_sources(lead_id,source_id,publication_url,external_message_id) VALUES (?,?,?,?)",
                (lead_id, source_id, url, message_id),
            )
            if message_id:
                self.db.execute(
                    "INSERT INTO crm_message_keys VALUES (?,?,?,?,?)",
                    (self.owner, platform, source_key, message_id, lead_id),
                )
            for category in categories:
                self.db.execute("INSERT OR IGNORE INTO lead_categories VALUES (?,?)", (lead_id, category))
            activity(self.db, lead_id, "lead_registered", {"status": "new"}, "owner")
        return self.card(lead_id)

    def list_leads(
        self,
        search="",
        status=None,
        platform=None,
        category=None,
        priority=None,
        limit=50,
        offset=0,
        include_merged=False,
    ):
        if type(limit) is not int or not 1 <= limit <= 10000 or type(offset) is not int or offset < 0:
            raise ValueError("Некорректные параметры страницы.")
        if status is not None and status not in STATUSES:
            raise ValueError("Неизвестный статус.")
        if priority is not None and priority not in PRIORITIES:
            raise ValueError("Неизвестный приоритет.")
        where, params = ["l.recipient=?"], [self.owner]
        if not include_merged:
            where.append("l.merged_into IS NULL")
        if search:
            search = text(search, 500)
            where.append(
                "(instr(casefold(l.title),casefold(?))>0 OR instr(casefold(l.original_text),casefold(?))>0 "
                "OR EXISTS(SELECT 1 FROM clients c WHERE c.id=l.client_id AND "
                "(instr(casefold(c.name),casefold(?))>0 OR instr(casefold(c.contact),casefold(?))>0)))"
            )
            params.extend((search, search, search, search))
        for column, value in (("status", status), ("priority", priority)):
            if value is not None:
                where.append(f"l.{column}=?")
                params.append(value)
        if platform:
            where.append(
                "(l.platform=? OR EXISTS(SELECT 1 FROM lead_sources ls JOIN sources s ON s.id=ls.source_id "
                "JOIN leads linked ON linked.id=ls.lead_id WHERE "
                "(linked.id=l.id OR linked.merged_into=l.id) AND linked.recipient=l.recipient AND s.platform=?))"
            )
            params.extend((platform, platform))
        if category:
            where.append(
                "EXISTS(SELECT 1 FROM lead_categories c JOIN leads linked ON linked.id=c.lead_id "
                "WHERE (linked.id=l.id OR linked.merged_into=l.id) AND linked.recipient=l.recipient AND c.category=?)"
            )
            params.append(category)
        condition = " AND ".join(where)
        total = self.db.execute(f"SELECT count(*) FROM leads l WHERE {condition}", params).fetchone()[0]  # noqa: S608 — условие из констант; значения передаются параметрами
        rows = self.db.execute(
            f"SELECT l.*,c.name AS client_name,c.contact AS contact,"  # noqa: S608 — условие из констант; значения передаются параметрами
            f"(SELECT group_concat(category,' | ') FROM lead_categories WHERE lead_id=l.id) AS categories "
            f"FROM leads l LEFT JOIN clients c ON c.id=l.client_id WHERE {condition} "
            "ORDER BY l.id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return {"total": total, "items": [dict(row) for row in rows], "limit": limit, "offset": offset}

    def related(self, table, lead_id):
        if table not in {"conversations", "drafts", "deals", "activities", "notes", "reminders", "lead_feedback"}:
            raise ValueError("Неизвестный раздел карточки.")
        ids = self.members(lead_id)
        placeholders = ",".join("?" for _ in ids)
        return [
            dict(row)
            for row in self.db.execute(
                f"SELECT * FROM {table} WHERE lead_id IN ({placeholders}) ORDER BY id",  # noqa: S608 — имя таблицы из белого списка; список плейсхолдеров «?»
                ids,
            )
        ]

    def card(self, lead_id):
        result = self.lead(lead_id)
        ids = self.members(lead_id)
        placeholders = ",".join("?" for _ in ids)
        result["sources"] = [
            dict(row)
            for row in self.db.execute(
                f"SELECT ls.*,s.platform,s.name,s.external_id,r.published_at,r.author_id,r.author_name,"  # noqa: S608 — список плейсхолдеров «?» по числу идентификаторов
                f"r.original_text AS source_original FROM lead_sources ls "
                f"JOIN sources s ON s.id=ls.source_id LEFT JOIN raw_messages r ON r.id=ls.raw_id "
                f"WHERE ls.lead_id IN ({placeholders})",
                ids,
            )
        ]
        result["categories"] = [
            row[0]
            for row in self.db.execute(
                f"SELECT DISTINCT category FROM lead_categories WHERE lead_id IN ({placeholders})",  # noqa: S608 — список плейсхолдеров «?» по числу идентификаторов
                ids,
            )
        ]
        result["client"] = self.client(result["client_id"]) if result["client_id"] else None
        result["merged_lead_ids"] = [value for value in ids if value != lead_id]
        result["merges"] = [
            dict(row)
            for row in self.db.execute(
                (
                    "SELECT id,secondary_id,created_at FROM lead_merges WHERE owner_id=? "
                    "AND target_id=? AND active=1 ORDER BY id DESC"
                ),
                (self.owner, lead_id),
            )
        ]
        result["analyses"] = []
        if self.db.execute("SELECT 1 FROM core_migrations WHERE version=3").fetchone():
            for row in self.db.execute(
                f"SELECT a.result,a.raw_id FROM message_analyses a JOIN lead_messages lm ON lm.raw_id=a.raw_id "  # noqa: S608 — список плейсхолдеров «?» по числу идентификаторов
                f"AND lm.recipient=a.recipient WHERE lm.lead_id IN ({placeholders}) AND a.recipient=? "
                f"AND a.id=(SELECT max(id) FROM message_analyses WHERE raw_id=a.raw_id AND recipient=a.recipient)",
                (*ids, self.owner),
            ):
                result["analyses"].append({"raw_id": row["raw_id"], **json.loads(row["result"])})
        for table in ("conversations", "drafts", "deals", "activities", "notes", "reminders", "lead_feedback"):
            result[table] = self.related(table, lead_id)
        return result

    def update(self, lead_id, changes):
        if not changes or set(changes) - EDITABLE:
            raise ValueError("Проверь редактируемые поля карточки.")
        with transaction(self.db):
            current = self.lead(lead_id, writable=True)
            values = dict(changes)
            for key, value in changes.items():
                if key in MONEY_FIELDS:
                    values[key] = amount(value)
                elif key == "currency":
                    values[key] = currency(value)
                elif key == "priority":
                    if value not in PRIORITIES:
                        raise ValueError("Неизвестный приоритет.")
                elif key == "score":
                    if value is not None and (type(value) is not int or not 0 <= value <= 100):
                        raise ValueError("Оценка должна быть целой от 0 до 100.")
                elif key == "client_id":
                    if value is not None:
                        self.client(value)
                else:
                    values[key] = text(value, 500 if key == "title" else 100000, optional=key != "title")
            if "score" in values and "score_origin" in current:
                values["score_origin"] = "manual" if values["score"] is not None else None
            combined = {**current, **values}
            if any(combined[key] is not None for key in MONEY_FIELDS) and not combined["currency"]:
                raise ValueError("Для денежных полей нужна валюта.")
            for lower, upper in (
                ("estimated_budget_min", "estimated_budget_max"),
                ("minimum_price", "recommended_price"),
            ):
                if (
                    combined[lower] is not None
                    and combined[upper] is not None
                    and Decimal(combined[lower]) > Decimal(combined[upper])
                ):
                    raise ValueError("Нижняя граница превышает верхнюю.")
            assignments = ",".join(f"{key}=?" for key in values)
            self.db.execute(
                f"UPDATE leads SET {assignments},updated_at=? WHERE id=?",  # noqa: S608 — имена столбцов из белого списка EDITABLE
                (*values.values(), time.time(), lead_id),
            )
            activity(
                self.db, lead_id, "lead_updated", {"before": {key: current[key] for key in values}, "after": values}
            )
        return self.card(lead_id)

    def change_status(self, lead_id, status):
        if status not in STATUSES:
            raise ValueError("Неизвестный статус.")
        with transaction(self.db):
            lead = self.lead(lead_id, writable=True)
            if lead["status"] != status:
                self.db.execute("UPDATE leads SET status=?,updated_at=? WHERE id=?", (status, time.time(), lead_id))
                activity(self.db, lead_id, "status_changed", {"from": lead["status"], "to": status})
        return self.card(lead_id)

    def add_source(self, lead_id, data):
        allowed = {"platform", "source_key", "source_name", "external_message_id", "url"}
        if set(data) - allowed:
            raise ValueError("Неизвестное поле источника.")
        platform = text(data.get("platform"), 40)
        key = text(data.get("source_key"), 500)
        name = text(data.get("source_name"), 500)
        message_id = text(data.get("external_message_id"), 500, optional=True)
        url = text(data.get("url"), 2000, optional=True)
        if url and urlsplit(url).scheme not in {"http", "https"}:
            raise ValueError("Источник должен иметь http/https ссылку.")
        with transaction(self.db):
            self.lead(lead_id, writable=True)
            if message_id:
                existing = self.db.execute(
                    (
                        "SELECT lead_id FROM crm_message_keys WHERE owner_id=? AND platform=?"
                        " AND source_key=? AND message_key=?"
                    ),
                    (self.owner, platform, key, message_id),
                ).fetchone()
                if existing and existing["lead_id"] != lead_id:
                    raise Conflict("Сообщение связано с другим лидом. Проверь ручное объединение.")
            source_id = source_record(self.db, platform, key, name)
            existing_source = self.db.execute(
                "SELECT id FROM lead_sources WHERE lead_id=? AND source_id=? AND external_message_id IS ?",
                (lead_id, source_id, message_id),
            ).fetchone()
            if existing_source:
                return existing_source["id"]
            row_id = self.db.execute(
                "INSERT INTO lead_sources(lead_id,source_id,external_message_id,publication_url) VALUES (?,?,?,?)",
                (lead_id, source_id, message_id, url),
            ).lastrowid
            if message_id:
                self.db.execute(
                    "INSERT OR IGNORE INTO crm_message_keys VALUES (?,?,?,?,?)",
                    (self.owner, platform, key, message_id, lead_id),
                )
            activity(self.db, lead_id, "source_added", {"source_id": source_id, "publication_url": url})
        return row_id

    def add_note(self, lead_id, value):
        content = text(value)
        with transaction(self.db):
            self.lead(lead_id, writable=True)
            row_id = self.db.execute(
                "INSERT INTO notes(lead_id,text,created_at) VALUES (?,?,?)", (lead_id, content, time.time())
            ).lastrowid
            activity(self.db, lead_id, "note_added", {"note_id": row_id})
        return row_id

    def add_conversation(self, lead_id, content, direction, occurred_at=None):
        if direction not in {"incoming", "outgoing", "system"}:
            raise ValueError("Переписка должна быть входящей, исходящей или системной.")
        content = text(content)
        occurred = timestamp(occurred_at) if occurred_at is not None else time.time()
        with transaction(self.db):
            lead = self.lead(lead_id, writable=True)
            row_id = self.db.execute(
                "INSERT INTO conversations(lead_id,client_id,direction,text,occurred_at,provenance,created_at) "
                "VALUES (?,?,?,?,?,'manual',?)",
                (lead_id, lead["client_id"], direction, content, occurred, time.time()),
            ).lastrowid
            activity(self.db, lead_id, "conversation_recorded", {"conversation_id": row_id, "direction": direction})
        return row_id

    def add_draft(self, lead_id, content):
        content = text(content)
        with transaction(self.db):
            self.lead(lead_id, writable=True)
            now = time.time()
            row_id = self.db.execute(
                "INSERT INTO drafts(lead_id,text,created_at,updated_at) VALUES (?,?,?,?)", (lead_id, content, now, now)
            ).lastrowid
            activity(self.db, lead_id, "draft_created", {"draft_id": row_id})
        return row_id

    def draft(self, draft_id):
        row = self.db.execute("SELECT * FROM drafts WHERE id=?", (identifier(draft_id),)).fetchone()
        if row is None:
            raise NotFound("Черновик не найден.")
        self.lead(row["lead_id"], writable=True)
        return dict(row)

    def update_draft(self, draft_id, changes):
        if not changes or set(changes) - {"text", "status"}:
            raise ValueError("Неизвестные поля черновика.")
        with transaction(self.db):
            draft = self.draft(draft_id)
            if draft["status"] == "sent":
                raise Conflict("Отправленный черновик сохранён в истории. Создай новый.")
            content = text(changes.get("text", draft["text"]))
            status = changes.get("status", draft["status"])
            if status not in {"draft", "approved", "rejected"}:
                raise ValueError("Отметка отправки требует отдельного действия.")
            self.db.execute(
                "UPDATE drafts SET text=?,status=?,updated_at=? WHERE id=?", (content, status, time.time(), draft_id)
            )
            activity(
                self.db,
                draft["lead_id"],
                "draft_updated",
                {"draft_id": draft_id, "before": draft, "status": status, "text": content},
            )
        return self.draft(draft_id)

    def mark_draft_sent(self, draft_id):
        # Регистрирует ручную отправку владельцем, сетевых вызовов нет.
        with transaction(self.db):
            draft = self.draft(draft_id)
            if draft["status"] == "sent":
                return draft["sent_conversation_id"]
            if draft["status"] != "approved":
                raise Conflict("Сначала одобри черновик.")
            now = time.time()
            client_id = self.lead(draft["lead_id"])["client_id"]
            row_id = self.db.execute(
                "INSERT INTO conversations(lead_id,client_id,direction,text,occurred_at,provenance,created_at) "
                "VALUES (?,?,'outgoing',?,?,'manual_draft_confirmation',?)",
                (draft["lead_id"], client_id, draft["text"], now, now),
            ).lastrowid
            self.db.execute(
                "UPDATE drafts SET status='sent',updated_at=?,sent_conversation_id=? WHERE id=?",
                (now, row_id, draft_id),
            )
            activity(self.db, draft["lead_id"], "draft_marked_sent", {"draft_id": draft_id, "conversation_id": row_id})
        return row_id

    def add_reminder(self, lead_id, content, due_at):
        content, due = text(content), timestamp(due_at)
        with transaction(self.db):
            self.lead(lead_id, writable=True)
            now = time.time()
            row_id = self.db.execute(
                "INSERT INTO reminders(lead_id,text,due_at,created_at,updated_at) VALUES (?,?,?,?,?)",
                (lead_id, content, due, now, now),
            ).lastrowid
            activity(self.db, lead_id, "reminder_created", {"reminder_id": row_id, "due_at": due})
        return row_id

    def update_reminder(self, reminder_id, changes):
        if not changes or set(changes) - {"text", "due_at", "status"}:
            raise ValueError("Неизвестные поля напоминания.")
        with transaction(self.db):
            row = self.db.execute("SELECT * FROM reminders WHERE id=?", (identifier(reminder_id),)).fetchone()
            if row is None:
                raise NotFound("Напоминание не найдено.")
            self.lead(row["lead_id"], writable=True)
            status = changes.get("status", row["status"])
            if status not in {"pending", "completed", "cancelled"}:
                raise ValueError("Неизвестный статус напоминания.")
            content = text(changes.get("text", row["text"]))
            due = timestamp(changes["due_at"]) if "due_at" in changes else row["due_at"]
            self.db.execute(
                "UPDATE reminders SET text=?,due_at=?,status=?,updated_at=? WHERE id=?",
                (content, due, status, time.time(), reminder_id),
            )
            activity(
                self.db,
                row["lead_id"],
                "reminder_updated",
                {"reminder_id": reminder_id, "before": dict(row), "status": status, "due_at": due, "text": content},
            )
        return reminder_id

    def reminders(self, due_before=None):
        condition, params = "", [self.owner]
        if due_before:
            condition = " AND r.due_at<=?"
            params.append(timestamp(due_before))
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT r.* FROM reminders r JOIN leads l ON l.id=r.lead_id "  # noqa: S608 — условие из констант; значения передаются параметрами
                f"WHERE l.recipient=? AND r.status='pending'{condition} ORDER BY r.due_at LIMIT 1000",
                params,
            )
        ]

    def add_feedback(self, lead_id, decision, reason=None):
        if decision not in {"approved", "rejected", "needs_review"}:
            raise ValueError("Неизвестное решение.")
        reason = text(reason, optional=True)
        with transaction(self.db):
            self.lead(lead_id, writable=True)
            row_id = self.db.execute(
                "INSERT INTO lead_feedback(lead_id,decision,reason,created_at) VALUES (?,?,?,?)",
                (lead_id, decision, reason, time.time()),
            ).lastrowid
            activity(self.db, lead_id, "feedback_added", {"feedback_id": row_id, "decision": decision})
        return row_id

    def add_deal(self, lead_id, data):
        if set(data) - {"title", "status", "actual_revenue", "currency"}:
            raise ValueError("Неизвестное поле сделки.")
        title = text(data.get("title"), 500)
        status = data.get("status", "open")
        revenue = amount(data.get("actual_revenue"))
        code = currency(data.get("currency"))
        if status not in {"open", "won", "lost"} or (revenue is not None and (not code or status != "won")):
            raise ValueError("Фактическую выручку можно указать для заключённой сделки с валютой.")
        with transaction(self.db):
            self.lead(lead_id, writable=True)
            now = time.time()
            row_id = self.db.execute(
                (
                    "INSERT INTO deals(lead_id,title,status,actual_revenue,currency,creat"
                    "ed_at,closed_at) VALUES (?,?,?,?,?,?,?)"
                ),
                (lead_id, title, status, revenue, code, now, now if status != "open" else None),
            ).lastrowid
            activity(
                self.db,
                lead_id,
                "deal_created",
                {"deal_id": row_id, "title": title, "status": status, "actual_revenue": revenue, "currency": code},
            )
        return row_id

    def update_deal(self, deal_id, changes):
        if not changes or set(changes) - {"title", "status", "actual_revenue", "currency"}:
            raise ValueError("Неизвестные поля сделки.")
        with transaction(self.db):
            row = self.db.execute("SELECT * FROM deals WHERE id=?", (identifier(deal_id),)).fetchone()
            if row is None:
                raise NotFound("Сделка не найдена.")
            self.lead(row["lead_id"], writable=True)
            title = text(changes.get("title", row["title"]), 500)
            status = changes.get("status", row["status"])
            revenue = amount(changes.get("actual_revenue", row["actual_revenue"]))
            code = currency(changes.get("currency", row["currency"]))
            if status not in {"open", "won", "lost"} or (revenue is not None and (not code or status != "won")):
                raise ValueError("Фактическая выручка допустима у заключённой сделки с указанной валютой.")
            closed = (row["closed_at"] or time.time()) if status != "open" else None
            self.db.execute(
                "UPDATE deals SET title=?,status=?,actual_revenue=?,currency=?,closed_at=? WHERE id=?",
                (title, status, revenue, code, closed, deal_id),
            )
            activity(
                self.db,
                row["lead_id"],
                "deal_updated",
                {
                    "deal_id": deal_id,
                    "before": dict(row),
                    "status": status,
                    "actual_revenue": revenue,
                    "currency": code,
                    "title": title,
                },
            )
        return deal_id

    def possible_duplicates(self, lead_id):
        lead = self.lead(lead_id)
        client = self.client(lead["client_id"]) if lead["client_id"] else None
        target_text = " ".join((lead["original_text"] or lead["title"]).casefold().split())
        target_url = canonical_url(lead["url"])
        candidates = self.db.execute(
            "SELECT l.*,c.platform AS client_platform,c.public_id AS client_public_id,c.company_id "
            "FROM leads l LEFT JOIN clients c ON c.id=l.client_id "
            "WHERE l.recipient=? AND l.id<>? AND l.merged_into IS NULL ORDER BY l.id DESC LIMIT 1000",
            (self.owner, lead_id),
        ).fetchall()
        matches = []
        for candidate in candidates:
            reasons = []
            other = " ".join((candidate["original_text"] or candidate["title"]).casefold().split())
            if (
                target_text
                and len(target_text) >= 15
                and SequenceMatcher(None, target_text[:4000], other[:4000]).ratio() >= 0.82
            ):
                reasons.append("similar_text")
            if target_url and target_url == canonical_url(candidate["url"]):
                reasons.append("same_url")
            if lead["client_id"] and lead["client_id"] == candidate["client_id"]:
                reasons.append("same_client")
            if client and client["company_id"] and client["company_id"] == candidate["company_id"]:
                reasons.append("same_company")
            if reasons:
                matches.append({"lead_id": candidate["id"], "reasons": reasons, "automatic_merge": False})
        return matches

    def merge(self, target_id, secondary_id):
        if target_id == secondary_id:
            raise ValueError("Нельзя объединить лид с собой.")
        with transaction(self.db):
            self.lead(target_id, writable=True)
            self.lead(secondary_id, writable=True)
            items = self.db.execute(
                "SELECT id,merged_into FROM leads WHERE recipient=? AND (id=? OR merged_into=?)",
                (self.owner, secondary_id, secondary_id),
            ).fetchall()
            merge_id = self.db.execute(
                "INSERT INTO lead_merges(target_id,secondary_id,owner_id,created_at) VALUES (?,?,?,?)",
                (target_id, secondary_id, self.owner, time.time()),
            ).lastrowid
            for item in items:
                self.db.execute(
                    "INSERT INTO lead_merge_items VALUES (?,?,?)", (merge_id, item["id"], item["merged_into"])
                )
                self.db.execute("UPDATE leads SET merged_into=? WHERE id=?", (target_id, item["id"]))
            activity(
                self.db, target_id, "leads_merged", {"merge_id": merge_id, "members": [item["id"] for item in items]}
            )
            activity(self.db, secondary_id, "merged_into", {"merge_id": merge_id, "target_id": target_id})
        return {"merge_id": merge_id, "lead": self.card(target_id)}

    def split(self, merge_id):
        with transaction(self.db):
            merge = self.db.execute(
                "SELECT * FROM lead_merges WHERE id=? AND owner_id=?", (identifier(merge_id), self.owner)
            ).fetchone()
            if merge is None:
                raise NotFound("Объединение не найдено.")
            if not merge["active"]:
                raise Conflict("Это объединение уже отменено.")
            items = self.db.execute("SELECT * FROM lead_merge_items WHERE merge_id=?", (merge_id,)).fetchall()
            self.lead(merge["target_id"], writable=True)
            for item in items:
                lead = self.lead(item["lead_id"])
                if lead["merged_into"] != merge["target_id"]:
                    raise Conflict("Сначала отмени более позднее объединение.")
            for item in items:
                self.db.execute("UPDATE leads SET merged_into=? WHERE id=?", (item["previous_parent"], item["lead_id"]))
            self.db.execute("UPDATE lead_merges SET active=0,split_at=? WHERE id=?", (time.time(), merge_id))
            activity(self.db, merge["target_id"], "leads_split", {"merge_id": merge_id})
            activity(self.db, merge["secondary_id"], "restored_from_merge", {"merge_id": merge_id})
        return self.card(merge["secondary_id"])

    def statistics(self):
        rows = self.db.execute("SELECT * FROM leads WHERE recipient=?", (self.owner,)).fetchall()
        roots = {row["id"]: row["merged_into"] or row["id"] for row in rows}
        active = [row for row in rows if row["merged_into"] is None]
        stages = {status: 0 for status in STATUSES}
        platforms, categories = {}, {}
        reached = {status: set() for status in STATUSES}
        for row in active:
            stages[row["status"]] += 1
            platforms[row["platform"]] = platforms.get(row["platform"], 0) + 1
            reached[row["status"]].add(row["id"])
        for row in self.db.execute(
            "SELECT a.* FROM activities a JOIN leads l ON l.id=a.lead_id WHERE l.recipient=?",
            (self.owner,),
        ):
            details = json.loads(row["details"])
            status = details.get("to") if row["kind"] == "status_changed" else details.get("status")
            if status in reached:
                reached[status].add(roots[row["lead_id"]])
        for row in self.db.execute(
            "SELECT c.lead_id,c.category FROM lead_categories c JOIN leads l ON l.id=c.lead_id WHERE l.recipient=?",
            (self.owner,),
        ):
            categories.setdefault(row["category"], set()).add(roots[row["lead_id"]])
        conversations = dict(
            self.db.execute(
                "SELECT c.direction,count(*) FROM conversations c JOIN leads l ON l.id=c.lead_id "
                "WHERE l.recipient=? AND c.direction!='system' GROUP BY c.direction",
                (self.owner,),
            )
        )
        replies = self.db.execute(
            "SELECT count(*) FROM conversations c JOIN leads l ON l.id=c.lead_id "
            "WHERE l.recipient=? AND c.direction='incoming' AND c.provenance='manual'",
            (self.owner,),
        ).fetchone()[0]
        revenue, won_deals = {}, 0
        for row in self.db.execute(
            "SELECT d.* FROM deals d JOIN leads l ON l.id=d.lead_id WHERE l.recipient=? AND d.status='won'",
            (self.owner,),
        ):
            won_deals += 1
            if row["actual_revenue"] is not None:
                revenue[row["currency"]] = revenue.get(row["currency"], Decimal(0)) + Decimal(row["actual_revenue"])
        return {
            "total": len(active),
            "new": stages["new"],
            "by_status": stages,
            "by_platform": platforms,
            "by_category": {key: len(value) for key, value in categories.items()},
            "outgoing_messages": conversations.get("outgoing", 0),
            "incoming_messages": conversations.get("incoming", 0),
            "replies": replies,
            "negotiations": stages["negotiation"],
            "won_deals": won_deals,
            "actual_revenue": {key: str(value) for key, value in revenue.items()},
            "stage_reached": {key: len(value) for key, value in reached.items()},
            "stage_conversion": {key: len(value) / len(active) if active else None for key, value in reached.items()},
        }

    def retention_candidates(self, now=None):
        cutoff = (time.time() if now is None else now) - 30 * 86400
        items = []
        for row in self.db.execute(
            "SELECT id,discovered_at,status FROM leads WHERE recipient=? AND merged_into IS NULL "
            "AND discovered_at IS NOT NULL AND discovered_at<?",
            (self.owner, cutoff),
        ).fetchall():
            active_statuses = {
                "ready",
                "draft_prepared",
                "outreach_sent",
                "client_replied",
                "negotiation",
                "proposal_sent",
                "awaiting_decision",
                "won",
            }
            protected = any(
                item["status"] in active_statuses
                for item in self.db.execute(
                    "SELECT status FROM leads WHERE recipient=? AND (id=? OR merged_into=?)",
                    (self.owner, row["id"], row["id"]),
                )
            )
            protected = protected or any(deal["status"] in {"open", "won"} for deal in self.related("deals", row["id"]))
            protected = protected or any(
                reminder["status"] == "pending" for reminder in self.related("reminders", row["id"])
            )
            items.append({**dict(row), "protected": bool(protected), "automatic_deletion": False})
        return {"retention_days": 30, "deletion_enabled": False, "items": items}


def canonical_url(value):
    if not value:
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"}:
        return None
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), parsed.query, ""))
