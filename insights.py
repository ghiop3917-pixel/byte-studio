"""Статистика по фактическим сообщениям и решениям владельца, без автообучения."""

import hashlib
import json
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from statistics import median

from crm import NotFound, identifier, text
from lead_analysis import VERSION
from reliability import transaction


class Insights:
    def __init__(self, db, owner):
        self.db = db
        self.owner = identifier(owner)

    def report(self, days=30, now=None):
        if type(days) is not int or not 1 <= days <= 365:
            raise ValueError("Период должен быть от 1 до 365 дней.")
        now = time.time() if now is None else now
        cutoff = now - days * 86400
        sources = {}
        rows = self.db.execute(
            """SELECT r.chat_id,max(r.id) last_id,count(*) messages,
            sum(CASE WHEN a.bucket='lead' THEN 1 ELSE 0 END) candidates,
            sum(CASE WHEN a.bucket='review' THEN 1 ELSE 0 END) review,
            sum(CASE WHEN a.bucket='ignored' THEN 1 ELSE 0 END) ignored,
            sum(CASE WHEN a.version=? THEN 1 ELSE 0 END) current_version_messages
            FROM raw_messages r LEFT JOIN message_analyses a ON a.raw_id=r.id AND a.recipient=?
            AND a.id=(SELECT max(id) FROM message_analyses WHERE raw_id=r.id AND recipient=?)
            WHERE r.received_at>=? AND json_extract(r.payload,'$.recipient')=? GROUP BY r.chat_id""",
            (VERSION, self.owner, self.owner, cutoff, self.owner),
        )
        for row in rows:
            name = self.db.execute("SELECT source FROM raw_messages WHERE id=?", (row["last_id"],)).fetchone()[0]
            sources[row["chat_id"]] = {
                **dict(row),
                "name": name,
                "leads": 0,
                "approved": 0,
                "rejected": 0,
                "needs_review": 0,
                "unjudged": 0,
            }
            sources[row["chat_id"]].pop("last_id")
        leads = {
            row["id"]: dict(row)
            for row in self.db.execute("SELECT id,merged_into,status FROM leads WHERE recipient=?", (self.owner,))
        }

        def root(lead_id):
            return leads[lead_id]["merged_into"] or lead_id

        labels = {}
        for row in self.db.execute(
            """SELECT f.* FROM lead_feedback f JOIN leads l ON l.id=f.lead_id
            WHERE l.recipient=? ORDER BY f.id""",
            (self.owner,),
        ):
            labels[root(row["lead_id"])] = dict(row)
        per_source = defaultdict(set)
        for row in self.db.execute(
            """SELECT DISTINCT r.chat_id,m.lead_id FROM raw_messages r
            JOIN lead_messages m ON m.raw_id=r.id WHERE m.recipient=? AND r.received_at>=?""",
            (self.owner, cutoff),
        ):
            per_source[row["chat_id"]].add(root(row["lead_id"]))
        categories = defaultdict(set)
        recent_roots = {
            root(row["id"])
            for row in self.db.execute(
                "SELECT id FROM leads WHERE recipient=? AND discovered_at>=?", (self.owner, cutoff)
            )
        }
        for row in self.db.execute(
            """SELECT c.category,c.lead_id FROM lead_categories c JOIN leads l ON l.id=c.lead_id
            WHERE l.recipient=?""",
            (self.owner,),
        ):
            if root(row["lead_id"]) in recent_roots:
                categories[row["category"]].add(root(row["lead_id"]))

        def judged(ids):
            counts = {"leads": len(ids), "approved": 0, "rejected": 0, "needs_review": 0}
            for lead_id in ids:
                label = labels.get(lead_id)
                if label:
                    counts[label["decision"]] += 1
            total = counts["approved"] + counts["rejected"]
            return {
                **counts,
                "judged": total,
                "unjudged": len(ids) - total,
                "observed_approval_rate": counts["approved"] / total if total else None,
                "small_sample": total < 5,
            }

        for source_id, item in sources.items():
            item.update(judged(per_source[source_id]))
            item["sample_lead_ids"] = sorted(per_source[source_id], reverse=True)[:5]
        category_rows = [{"category": key, **judged(ids)} for key, ids in sorted(categories.items())]
        reasons = defaultdict(int)
        for lead_id, label in labels.items():
            if lead_id in recent_roots and label["decision"] == "rejected":
                reasons[label["reason"] or "Причина не указана"] += 1
        prices = defaultdict(list)
        for row in self.db.execute(
            """SELECT d.actual_revenue,d.currency FROM deals d JOIN leads l ON l.id=d.lead_id
            WHERE l.recipient=? AND d.status='won' AND d.actual_revenue IS NOT NULL AND d.closed_at>=?""",
            (self.owner, cutoff),
        ):
            prices[row["currency"]].append(Decimal(row["actual_revenue"]))
        return {
            "days": days,
            "generated_at": now,
            "engine": "rules",
            "version": VERSION,
            "sources": sorted(sources.values(), key=lambda item: (-item["leads"], -item["messages"], item["name"])),
            "categories": category_rows,
            "feedback": judged(recent_roots),
            "rejection_reasons": dict(reasons),
            "won_prices": [
                {
                    "currency": code,
                    "count": len(values),
                    "min": str(min(values)),
                    "max": str(max(values)),
                    "median": str(median(values)) if len(values) >= 3 else None,
                }
                for code, values in sorted(prices.items())
            ],
            "automatic_learning": False,
            "ai_requests": 0,
            "recall": None,
            ("note"): "Подходит/мусор — только решения владельца. Доля одобрения от"
            "носится лишь к оценённым заявкам, не ко всему потоку. Источн"
            "ики одного проекта учитываются отдельно; их итоги нельзя скл"
            "адывать как уникальные лиды.",
        }

    def generate_suggestions(self, now=None):
        now = time.time() if now is None else now
        report = self.report(now=now)
        calendar = datetime.fromtimestamp(now, timezone(timedelta(hours=3))).isocalendar()
        week = f"{calendar.year}-{calendar.week}"
        proposals = []
        if report["feedback"]["leads"] >= 10 and report["feedback"]["judged"] < 5:
            proposals.append(
                (
                    "label_examples",
                    "Разметить несколько последних заявок",
                    {"leads": report["feedback"]["leads"], "judged": report["feedback"]["judged"]},
                    "global",
                )
            )
        for source in report["sources"]:
            if source["judged"] >= 5 and source["rejected"] / source["judged"] >= 0.6:
                proposals.append(
                    ("review_source", "Проверить источник: " + source["name"], source, str(source["chat_id"]))
                )
        for category in report["categories"]:
            if category["judged"] >= 5 and category["rejected"] / category["judged"] >= 0.6:
                proposals.append(
                    (
                        "review_category",
                        "Проверить признаки категории: " + category["category"],
                        category,
                        category["category"],
                    )
                )
        with transaction(self.db):
            for kind, title, evidence, key in proposals:
                fingerprint = hashlib.sha256(f"{week}:{kind}:{key}".encode()).hexdigest()
                self.db.execute(
                    """INSERT OR IGNORE INTO quality_suggestions(owner_id,kind,fingerprint,title,evidence,created_at)
                    VALUES (?,?,?,?,?,?)""",
                    (self.owner, kind, fingerprint, title, json.dumps(evidence, ensure_ascii=False), now),
                )
        return {"proposed": len(proposals), "rules_changed": False}

    def suggestions(self):
        return [
            {**dict(row), "evidence": json.loads(row["evidence"]), "rules_changed": False}
            for row in self.db.execute(
                "SELECT * FROM quality_suggestions WHERE owner_id=? ORDER BY id DESC LIMIT 100", (self.owner,)
            )
        ]

    def decide(self, suggestion_id, decision, note=None):
        if decision not in {"approved", "rejected"}:
            raise ValueError("Выбери approved или rejected.")
        if note is not None:
            note = text(note, 2000, optional=True)
        with transaction(self.db):
            row = self.db.execute(
                "SELECT * FROM quality_suggestions WHERE id=? AND owner_id=?", (identifier(suggestion_id), self.owner)
            ).fetchone()
            if row is None:
                raise NotFound("Предложение не найдено.")
            if row["status"] != decision or row["decision_note"] != note:
                self.db.execute(
                    "UPDATE quality_suggestions SET status=?,decided_at=?,decision_note=? WHERE id=?",
                    (decision, time.time(), note, suggestion_id),
                )
                self.db.execute(
                    (
                        "INSERT INTO suggestion_decisions(suggestion_id,owner_id,decision,not"
                        "e,created_at) VALUES (?,?,?,?,?)"
                    ),
                    (suggestion_id, self.owner, decision, note, time.time()),
                )
        return {
            "id": suggestion_id,
            "status": decision,
            "rules_changed": False,
            "message": "Решение записано. Правила и источники не изменены; предложение требует отдельной реализации.",
        }
