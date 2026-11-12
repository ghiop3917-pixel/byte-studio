"""Сохранение анализа и ручная проверка. Правила автоматически не меняются."""

import json
import time

from crm import CRM, NotFound, activity, identifier
from lead_analysis import VERSION, analyze
from reliability import reprocess, transaction


def store_analysis(db, raw, recipient, directions):
    author = json.loads(raw["payload"])["message"].get("from", {})
    context_key = json.dumps(sorted(directions))
    row = db.execute(
        "SELECT * FROM message_analyses WHERE raw_id=? AND recipient=? AND version=? AND context_key=?",
        (raw["id"], recipient, VERSION, context_key),
    ).fetchone()
    if row:
        return row["id"], json.loads(row["result"])
    result = analyze(raw["original_text"], directions, author)
    analysis_id = db.execute(
        (
            "INSERT INTO message_analyses(raw_id,recipient,version,context_key,inte"
            "nt,score,bucket,language,result,created_at) VALUES (?,?,?,?,?,?,?,?,?,"
            "?)"
        ),
        (
            raw["id"],
            recipient,
            VERSION,
            context_key,
            result["intent"],
            result["score"],
            result["bucket"],
            result["language"],
            json.dumps(result, ensure_ascii=False),
            time.time(),
        ),
    ).lastrowid
    return analysis_id, result


def update_review(db, raw_id, recipient, analysis_id, result):
    if result["bucket"] == "review":
        db.execute(
            "INSERT INTO message_reviews(raw_id,recipient,analysis_id) VALUES (?,?,?) "
            "ON CONFLICT(raw_id,recipient) DO UPDATE SET analysis_id=excluded.analysis_id",
            (raw_id, recipient, analysis_id),
        )


def apply_score(db, lead_id, result):
    lead = db.execute("SELECT score,score_origin FROM leads WHERE id=?", (lead_id,)).fetchone()
    if lead["score_origin"] != "manual":
        db.execute("UPDATE leads SET score=?,score_origin='rules' WHERE id=?", (result["score"], lead_id))
    activity(
        db,
        lead_id,
        "analysis_completed",
        {"version": result["version"], "intent": result["intent"], "score": result["score"], "engine": "rules"},
        "rules",
    )


class Quality:
    def __init__(self, db, recipient):
        self.db = db
        self.recipient = identifier(recipient)

    def reviews(self, limit=20, offset=0, status="pending"):
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise ValueError("Некорректная страница проверки.")
        if status not in {"pending", "approved", "rejected"}:
            raise ValueError("Неизвестный статус проверки.")
        total = self.db.execute(
            "SELECT count(*) FROM message_reviews WHERE recipient=? AND status=?", (self.recipient, status)
        ).fetchone()[0]
        rows = self.db.execute(
            "SELECT r.raw_id,r.status,m.source,m.original_text,m.url,a.score,a.intent "
            "FROM message_reviews r JOIN raw_messages m ON m.id=r.raw_id "
            "JOIN message_analyses a ON a.id=r.analysis_id WHERE r.recipient=? AND r.status=? "
            "ORDER BY r.raw_id DESC LIMIT ? OFFSET ?",
            (self.recipient, status, limit, offset),
        )
        return {"total": total, "items": [dict(row) for row in rows]}

    def review(self, raw_id):
        row = self.db.execute(
            "SELECT r.*,m.source,m.original_text,m.url,a.result FROM message_reviews r "
            "JOIN raw_messages m ON m.id=r.raw_id JOIN message_analyses a ON a.id=r.analysis_id "
            "WHERE r.raw_id=? AND r.recipient=?",
            (identifier(raw_id), self.recipient),
        ).fetchone()
        if row is None:
            raise NotFound("Сообщение для проверки не найдено.")
        result = dict(row)
        result["analysis"] = json.loads(result.pop("result"))
        return result

    def decide(self, raw_id, decision, reason=None):
        if decision not in {"approved", "rejected", "needs_review"}:
            raise ValueError("Неизвестное решение.")
        if reason is not None and (not isinstance(reason, str) or len(reason) > 2000):
            raise ValueError("Некорректная причина.")
        status = "pending" if decision == "needs_review" else decision
        with transaction(self.db):
            current = self.review(raw_id)
            latest = self.db.execute(
                "SELECT decision,reason FROM review_events WHERE raw_id=? AND recipient=? ORDER BY id DESC LIMIT 1",
                (raw_id, self.recipient),
            ).fetchone()
            if (
                current["status"] == status
                and current["reason"] == reason
                and latest
                and latest["decision"] == decision
                and latest["reason"] == reason
            ):
                return current
            self.db.execute(
                "UPDATE message_reviews SET status=?,reason=?,decided_at=? WHERE raw_id=? AND recipient=?",
                (status, reason, time.time(), raw_id, self.recipient),
            )
            self.db.execute(
                "INSERT INTO review_events(raw_id,recipient,decision,reason,created_at) VALUES (?,?,?,?,?)",
                (raw_id, self.recipient, decision, reason, time.time()),
            )
            if decision == "approved":
                reprocess(self.db, raw_id)
        return self.review(raw_id)

    def feedback(self, lead_id, decision, reason=None):
        if decision not in {"approved", "rejected", "needs_review"}:
            raise ValueError("Неизвестное решение.")
        if reason is not None and (not isinstance(reason, str) or len(reason) > 2000):
            raise ValueError("Некорректная причина.")
        with transaction(self.db):
            service = CRM(self.db, self.recipient)
            service.lead(lead_id, writable=True)
            previous = self.db.execute(
                "SELECT id,decision,reason FROM lead_feedback WHERE lead_id=? ORDER BY id DESC LIMIT 1",
                (lead_id,),
            ).fetchone()
            if previous and previous["decision"] == decision and previous["reason"] == reason:
                feedback_id = previous["id"]
            else:
                feedback_id = service.add_feedback(lead_id, decision, reason)
            ids = service.members(lead_id)
            placeholders = ",".join("?" for _ in ids)
            for row in self.db.execute(
                f"SELECT a.id,a.raw_id FROM message_analyses a JOIN lead_messages lm ON lm.raw_id=a.raw_id "  # noqa: S608 — список плейсхолдеров «?» по числу идентификаторов
                f"AND lm.recipient=a.recipient WHERE lm.lead_id IN ({placeholders}) AND lm.recipient=? AND a.id="
                "(SELECT max(id) FROM message_analyses WHERE raw_id=a.raw_id AND recipient=a.recipient)",
                (*ids, self.recipient),
            ).fetchall():
                status = "pending" if decision == "needs_review" else decision
                review = self.db.execute(
                    "SELECT status,reason FROM message_reviews WHERE raw_id=? AND recipient=?",
                    (row["raw_id"], self.recipient),
                ).fetchone()
                if decision == "needs_review":
                    self.db.execute(
                        (
                            "INSERT INTO message_reviews(raw_id,recipient,analysis_id,status,reason"
                            ",decided_at) VALUES (?,?,?,?,?,?) ON CONFLICT(raw_id,recipient) DO UPD"
                            "ATE SET status=excluded.status,analysis_id=excluded.analysis_id,reason"
                            "=excluded.reason,decided_at=excluded.decided_at"
                        ),
                        (row["raw_id"], self.recipient, row["id"], status, reason, time.time()),
                    )
                elif review:
                    self.db.execute(
                        "UPDATE message_reviews SET status=?,reason=?,decided_at=? WHERE raw_id=? AND recipient=?",
                        (status, reason, time.time(), row["raw_id"], self.recipient),
                    )
                if (review or decision == "needs_review") and (
                    not review or review["status"] != status or review["reason"] != reason
                ):
                    self.db.execute(
                        "INSERT INTO review_events(raw_id,recipient,decision,reason,created_at) VALUES (?,?,?,?,?)",
                        (row["raw_id"], self.recipient, decision, reason, time.time()),
                    )
        return feedback_id

    def reanalyze_missing(self, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Размер пакета должен быть от 1 до 1000.")
        with transaction(self.db):
            rows = self.db.execute(
                "SELECT m.id FROM raw_messages m JOIN processing_queue q ON q.raw_id=m.id "
                "WHERE json_extract(m.payload,'$.recipient')=? AND q.status!='processing' "
                "AND NOT EXISTS(SELECT 1 FROM message_analyses a WHERE a.raw_id=m.id "
                "AND a.recipient=? AND a.version=?) ORDER BY m.id DESC LIMIT ?",
                (self.recipient, self.recipient, VERSION, limit),
            ).fetchall()
            for row in rows:
                reprocess(self.db, row["id"])
        return {"queued": len(rows)}

    def report(self):
        latest = self.db.execute(
            "SELECT f.decision,count(*) FROM lead_feedback f JOIN leads l ON l.id=f.lead_id "
            "WHERE l.recipient=? AND f.id=(SELECT max(id) FROM lead_feedback WHERE lead_id=f.lead_id) "
            "GROUP BY f.decision",
            (self.recipient,),
        )
        labels = dict(latest)
        judged = labels.get("approved", 0) + labels.get("rejected", 0)
        reviews = dict(
            self.db.execute(
                "SELECT status,count(*) FROM message_reviews WHERE recipient=? GROUP BY status",
                (self.recipient,),
            )
        )
        return {
            "engine": "rules",
            "version": VERSION,
            "lead_feedback": labels,
            "reviews": reviews,
            "judged_leads": judged,
            "observed_approval_rate": labels.get("approved", 0) / judged if judged else None,
            "recall": None,
            "recall_note": "Полноту нельзя измерить без размеченных пропущенных сообщений.",
            "automatic_learning": False,
            "ai_requests": 0,
        }
