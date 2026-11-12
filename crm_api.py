"""Локальный HTTP API CRM. По умолчанию доступен только через loopback."""

import argparse
import hashlib
import hmac
import json
import logging
import os
import re
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from pathlib import Path

from bot_menu import MONITOR_DB
from crm import CRM, Conflict, NotFound, STATUSES, identifier
from crm_export import export
from monitor import recipient_id
from reliability import migrate, open_database, transaction


class Unauthorized(Exception):
    pass


class CRMApp:
    def __init__(self, database, owner_id, token):
        if not isinstance(token, str) or len(token) < 32:
            raise ValueError("CRM_API_TOKEN должен содержать минимум 32 символа.")
        self.database = database
        self.owner = identifier(owner_id)
        self.token = token

    def request(self, method, target, authorization, data=None, idempotency_key=None):
        expected = ("Bearer " + self.token).encode()
        if not isinstance(authorization, str) or not hmac.compare_digest(authorization.encode(), expected):
            raise Unauthorized()
        parsed = urlsplit(target)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if any(len(values) != 1 for values in query.values()):
            raise ValueError("Повторяющиеся параметры запрещены.")
        query = {key: value[0] for key, value in query.items()}
        data = {} if data is None else data
        if not isinstance(data, dict):
            raise ValueError("Тело запроса должно быть JSON-объектом.")
        mutation = method in {"POST", "PATCH"}
        if mutation and (not isinstance(idempotency_key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", idempotency_key)):
            raise ValueError("Для изменения нужен Idempotency-Key длиной 16–128 символов.")
        request_hash = hashlib.sha256(json.dumps([method, target, data], sort_keys=True).encode()).hexdigest()
        with closing(open_database(self.database)) as db:
            service = CRM(db, self.owner)
            with transaction(db, immediate=mutation):
                if mutation:
                    previous = db.execute(
                        "SELECT * FROM crm_api_requests WHERE owner_id=? AND request_key=?",
                        (self.owner, idempotency_key),
                    ).fetchone()
                    if previous:
                        if previous["request_hash"] != request_hash:
                            raise Conflict("Этот Idempotency-Key уже использован для другого запроса.")
                        return previous["content_type"], previous["response"]
                result = route(service, method, parsed.path.rstrip("/") or "/", query, data)
                response = result if isinstance(result, tuple) else (
                    "application/json; charset=utf-8", json.dumps(result, ensure_ascii=False).encode("utf-8")
                )
                if mutation:
                    db.execute("INSERT INTO crm_api_requests VALUES (?,?,?,?,?,?)",
                               (self.owner, idempotency_key, request_hash, *response, time.time()))
                return response

    def intake(self, data, client_address):
        from web_intake import submit
        client_hash = hashlib.sha256(f'byte-intake:{self.owner}:{client_address}'.encode()).hexdigest()
        with closing(open_database(self.database)) as db:
            return submit(db, self.owner, data, client_hash)


def filters(query, pagination=True):
    allowed = {"search", "status", "platform", "category", "priority", "include_merged"}
    if pagination:
        allowed |= {"limit", "offset"}
    if set(query) - allowed:
        raise ValueError("Неизвестные параметры фильтра.")
    result = dict(query)
    for key in ("limit", "offset"):
        if key in result:
            result[key] = int(result[key])
    if "include_merged" in result:
        if result["include_merged"] not in {"0", "1"}:
            raise ValueError("include_merged должен быть 0 или 1.")
        result["include_merged"] = result["include_merged"] == "1"
    return result


def require_fields(data, required, optional=()):
    if not set(required) <= set(data) or set(data) - set(required) - set(optional):
        raise ValueError("Проверь поля запроса.")


def route(service, method, path, query, data):
    if path == '/api/insights' and method == 'GET':
        from insights import Insights
        if set(query)-{'days'}:raise ValueError('Неизвестный параметр статистики качества.')
        return Insights(service.db,service.owner).report(int(query.get('days',30)))
    if path == '/api/suggestions':
        from insights import Insights
        insights=Insights(service.db,service.owner)
        if method=='GET':return insights.suggestions()
        if method=='POST':
            require_fields(data,())
            return insights.generate_suggestions()
    suggestion=re.fullmatch(r'/api/suggestions/([1-9]\d{0,17})/decision',path)
    if suggestion and method=='POST':
        from insights import Insights
        require_fields(data,('decision',),('note',))
        return Insights(service.db,service.owner).decide(int(suggestion[1]),data['decision'],data.get('note'))
    if path == '/api/operations' and method=='GET':
        from maintenance import operations_report
        return operations_report(service.db,service.owner)
    if path == '/api/operations/run' and method=='POST':
        from maintenance import request_run
        require_fields(data,('kind',))
        return request_run(service.db,service.owner,data['kind'])
    backup_verify=re.fullmatch(r'/api/backups/([1-9]\d{0,17})/verify',path)
    if backup_verify and method=='GET':
        from maintenance import verify_backup
        return verify_backup(service.db,service.owner,int(backup_verify[1]))
    commercial = re.fullmatch(r"/api/leads/([1-9]\d{0,17})/(pricing|reply-analysis)", path)
    if commercial and method == 'POST':
        from sales_analysis import pricing, analyze_reply
        return (pricing if commercial[2]=='pricing' else analyze_reply)(service,int(commercial[1]),data)
    sales_match = re.fullmatch(r"/api/leads/([1-9]\d{0,17})/sales", path)
    if sales_match and method == "POST":
        from sales import prepare
        return prepare(service, int(sales_match[1]), data)
    from quality import Quality
    quality = Quality(service.db, service.owner)
    if path == "/api/quality" and method == "GET":
        return quality.report()
    if path == "/api/reanalysis" and method == "POST":
        require_fields(data, (), ("limit",))
        return quality.reanalyze_missing(data.get("limit", 100))
    if path == "/api/reviews" and method == "GET":
        if set(query) - {"limit", "offset", "status"}:
            raise ValueError("Неизвестный фильтр проверки.")
        return quality.reviews(limit=int(query.get("limit", 20)), offset=int(query.get("offset", 0)),
                               status=query.get("status", "pending"))
    review = re.fullmatch(r"/api/reviews/([1-9]\d{0,17})(?:/(decision))?", path)
    if review:
        if method == "GET" and review[2] is None:
            return quality.review(int(review[1]))
        if method == "POST" and review[2] == "decision":
            require_fields(data, ("decision",), ("reason",))
            return quality.decide(int(review[1]), data["decision"], data.get("reason"))
    if path == "/api/statuses" and method == "GET":
        return STATUSES
    if path == "/api/runtime" and method == "GET":
        now = time.time()
        heartbeats = [dict(row) for row in service.db.execute("SELECT * FROM core_heartbeat")]
        for item in heartbeats:
            item["age_seconds"] = max(0, now - item["updated_at"])
        return {"heartbeats": heartbeats, "sources": [dict(row) for row in service.db.execute(
            "SELECT username,status,attempts,due_at,last_success FROM source_health ORDER BY username")],
            "processing": dict(service.db.execute("SELECT status,count(*) FROM processing_queue GROUP BY status")),
            "notifications": dict(service.db.execute("SELECT status,count(*) FROM notification_queue WHERE recipient=? GROUP BY status", (service.owner,))),
            "events": [dict(row) for row in service.db.execute('SELECT created_at,component,item_id,error FROM core_events ORDER BY id DESC LIMIT 50')]}
    if path == '/api/runtime/stop' and method == 'POST':
        require_fields(data, ('confirmed',))
        if data['confirmed'] is not True:
            raise ValueError('Подтверди сохранение очередей и остановку мониторинга.')
        from reliability import heartbeat
        heartbeat(service.db, 'stop_request')
        return {'requested':True, 'message':'Запрошена штатная остановка. Дождись состояния stopped; очереди сохраняются.'}
    if path == '/api/backups' and method == 'GET':
        database_path = Path(service.db.execute('PRAGMA database_list').fetchone()[2])
        folder = database_path.parent / 'backups'
        return [{'name':p.name,'size':p.stat().st_size,'modified_at':p.stat().st_mtime}
                for p in sorted(folder.glob('*.sqlite3'),key=lambda p:p.stat().st_mtime,reverse=True)[:100]]
    if path == "/api/stats" and method == "GET":
        return service.statistics()
    if path == "/api/retention" and method == "GET":
        return service.retention_candidates()
    if path == "/api/clients":
        if method == "GET":
            return service.clients()
        if method == "POST":
            return service.create_client(data)
    if path == "/api/reminders" and method == "GET":
        if set(query) - {"due_before"}:
            raise ValueError("Неизвестные параметры напоминаний.")
        return service.reminders(query.get("due_before"))
    if path in {"/api/export.csv", "/api/export.xlsx"} and method == "GET":
        file_format = path.rsplit(".", 1)[-1]
        content_type = "text/csv; charset=utf-8" if file_format == "csv" else (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        return content_type, export(service, file_format, **filters(query, pagination=False))
    if path == "/api/leads":
        if method == "GET":
            return service.list_leads(**filters(query))
        if method == "POST":
            return service.create(data)
    match = re.fullmatch(r"/api/(leads|clients|drafts|reminders|deals|merges)/([1-9]\d{0,17})(?:/([a-z-]+))?", path)
    if not match:
        raise NotFound("Маршрут не найден.")
    entity, entity_id, action = match[1], int(match[2]), match[3]
    if entity == "clients" and method == "GET" and action is None:
        return service.client(entity_id)
    if entity == "clients" and method == "PATCH" and action is None:
        return service.update_client(entity_id, data)
    if entity == "deals" and method == "PATCH" and action is None:
        return {"deal_id": service.update_deal(entity_id, data)}
    if entity == "drafts":
        if method == "PATCH" and action is None:
            return service.update_draft(entity_id, data)
        if method == "POST" and action == "sent":
            require_fields(data, ("confirmed",))
            if data["confirmed"] is not True:
                raise ValueError("Подтверди, что отправка уже выполнена вручную.")
            return {"conversation_id": service.mark_draft_sent(entity_id)}
    if entity == "reminders" and method == "PATCH" and action is None:
        return {"reminder_id": service.update_reminder(entity_id, data)}
    if entity == "merges" and method == "POST" and action == "split":
        return service.split(entity_id)
    if entity == "leads":
        if action is None and method == "GET":
            return service.card(entity_id)
        if action is None and method == "PATCH":
            return service.update(entity_id, data)
        if method == "GET":
            if action == "history":
                return service.related("activities", entity_id)
            if action == "duplicates":
                return service.possible_duplicates(entity_id)
        if method == "POST":
            if action == "status":
                require_fields(data, ("status",))
                return service.change_status(entity_id, data["status"])
            if action == "sources":
                return {"source_id": service.add_source(entity_id, data)}
            if action == "notes":
                require_fields(data, ("text",))
                return {"note_id": service.add_note(entity_id, data["text"])}
            if action == "drafts":
                require_fields(data, ("text",))
                return {"draft_id": service.add_draft(entity_id, data["text"])}
            if action == "reminders":
                require_fields(data, ("text", "due_at"))
                return {"reminder_id": service.add_reminder(entity_id, data["text"], data["due_at"])}
            if action == "conversations":
                require_fields(data, ("text", "direction"), ("occurred_at",))
                return {"conversation_id": service.add_conversation(
                    entity_id, data["text"], data["direction"], data.get("occurred_at")
                )}
            if action == "feedback":
                require_fields(data, ("decision",), ("reason",))
                return {"feedback_id": quality.feedback(entity_id, data["decision"], data.get("reason"))}
            if action == "deals":
                return {"deal_id": service.add_deal(entity_id, data)}
            if action == "merge":
                require_fields(data, ("secondary_id",))
                return service.merge(entity_id, data["secondary_id"])
    raise NotFound("Маршрут не найден.")


STATIC_FILES = {
    "/": ("studio/index.html", "text/html; charset=utf-8"),
    "/studio.css": ("studio/studio.css", "text/css; charset=utf-8"),
    "/studio.js": ("studio/studio.js", "application/javascript; charset=utf-8"),
    "/experience.css": ("studio/experience.css", "text/css; charset=utf-8"),
    "/motion.js": ("studio/motion.js", "application/javascript; charset=utf-8"),
    "/hero-3d.js": ("studio/hero-3d.js", "application/javascript; charset=utf-8"),
    "/portfolio.js": ("studio/portfolio.js", "application/javascript; charset=utf-8"),
    "/projects.json": ("studio/projects.json", "application/json; charset=utf-8"),
    "/favicon.svg": ("studio/favicon.svg", "image/svg+xml"),
    "/vendor/gsap.min.js": ("studio/vendor/gsap.min.js", "application/javascript; charset=utf-8"),
    "/vendor/ScrollTrigger.min.js": ("studio/vendor/ScrollTrigger.min.js", "application/javascript; charset=utf-8"),
    "/fonts/Manrope-Variable.ttf": ("studio/fonts/Manrope-Variable.ttf", "font/ttf"),
    "/demo.html": ("studio/demo.html", "text/html; charset=utf-8"),
    "/forma.html": ("studio/forma.html", "text/html; charset=utf-8"),
    "/nexus.html": ("studio/nexus.html", "text/html; charset=utf-8"),
    "/concepts.css": ("studio/concepts.css", "text/css; charset=utf-8"),
    "/concepts.js": ("studio/concepts.js", "application/javascript; charset=utf-8"),
    "/images/forma-interior.webp": ("studio/images/forma-interior.webp", "image/webp"),
    "/images/forma-living.webp": ("studio/images/forma-living.webp", "image/webp"),
    "/demo.js": ("studio/demo.js", "application/javascript; charset=utf-8"),
    "/privacy.html": ("studio/privacy.html", "text/html; charset=utf-8"),
    "/crm": ("crm/index.html", "text/html; charset=utf-8"),
    "/crm/app.css": ("crm/app.css", "text/css; charset=utf-8"),
    "/crm/app.js": ("crm/app.js", "application/javascript; charset=utf-8"),
    "/crm/operations.js": ("crm/operations.js", "application/javascript; charset=utf-8"),
}

# Публичные файлы импортированного сайта. Служебные каталоги и база не обслуживаются.
for public_file in (
    'style.css', 'reference.css', 'film.css', 'ui.css', 'cases.css', 'polish.css',
    'app.js', 'reference.js', 'film.js', 'locale.js', 'polish.js', 'projects.js',
    'case-previews.js', 'intake.js', 'intake.css',
    'assets/aether-residence.jpg', 'assets/byte-logo-dark.png', 'assets/reference-visuals.png',
    'fonts/Onest-Variable.ttf', 'fonts/Oswald-Variable.ttf',
    'fonts/GolosText-Variable.ttf', 'fonts/Anton-Regular.ttf',
):
    suffix = Path(public_file).suffix
    content_type = {'.css':'text/css; charset=utf-8', '.js':'application/javascript; charset=utf-8',
                    '.jpg':'image/jpeg', '.png':'image/png', '.ttf':'font/ttf'}[suffix]
    STATIC_FILES['/'+public_file] = ('studio/'+public_file, content_type)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format_string, *args):
        # URL и заголовки могут содержать пользовательские данные.
        logging.info("CRM API ответ %s", args[1] if len(args) > 1 else "")

    def do_GET(self):
        self.handle_request("GET")

    def do_POST(self):
        self.handle_request("POST")

    def do_PATCH(self):
        self.handle_request("PATCH")

    def handle_request(self, method):
        status = 200
        try:
            origin = self.headers.get("Origin")
            if origin and origin != f"http://127.0.0.1:{self.server.server_port}":
                raise Unauthorized()
            if method == "GET" and urlsplit(self.path).path in STATIC_FILES:
                filename, content_type = STATIC_FILES[urlsplit(self.path).path]
                content = (Path(__file__).parent / "web" / filename).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(content)))
                self.send_header("X-Content-Type-Options", "nosniff")
                policy = "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"
                if not urlsplit(self.path).path.startswith('/crm'):
                    # Архив использует inline CSS-атрибуты для превью и data-SVG для текстуры.
                    # Скрипты, таблицы стилей и шрифты по-прежнему только локальные.
                    policy += "; style-src-attr 'unsafe-inline'; img-src 'self' data:; font-src 'self'; form-action 'self'"
                self.send_header("Content-Security-Policy", policy)
                self.end_headers()
                self.wfile.write(content)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= 262144:
                raise ValueError("Размер запроса превышен.")
            self.connection.settimeout(10)
            body = None
            if length:
                if self.headers.get_content_type() != "application/json":
                    raise ValueError("Используй application/json.")
                body = json.loads(self.rfile.read(length))
            if method == 'POST' and self.path == '/public/intake':
                if origin != f"http://127.0.0.1:{self.server.server_port}":
                    raise Unauthorized()
                result = self.server.app.intake(body, self.client_address[0])
                content_type, content = 'application/json; charset=utf-8', json.dumps(result).encode()
            else:
                content_type, content = self.server.app.request(
                    method, self.path, self.headers.get("Authorization"), body,
                    self.headers.get("Idempotency-Key"),
                )
        except Unauthorized:
            status, content_type, content = 401, "application/json; charset=utf-8", b'{"error":"Unauthorized"}'
        except NotFound as error:
            status, content_type, content = 404, "application/json; charset=utf-8", json.dumps({"error": str(error)}, ensure_ascii=False).encode()
        except Conflict as error:
            status, content_type, content = 409, "application/json; charset=utf-8", json.dumps({"error": str(error)}, ensure_ascii=False).encode()
        except (ValueError, TypeError, KeyError) as error:
            from web_intake import RateLimited
            status = 429 if isinstance(error, RateLimited) else 400
            content_type, content = "application/json; charset=utf-8", json.dumps({"error": str(error)}, ensure_ascii=False).encode()
        except Exception:
            logging.exception("Внутренняя ошибка CRM API.")
            status, content_type, content = 500, "application/json; charset=utf-8", '{"error":"Внутренняя ошибка"}'.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)


def main():
    parser = argparse.ArgumentParser(description="Локальный CRM API Byte Studio")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--database", default=str(MONITOR_DB))
    args = parser.parse_args()
    owner = int(os.environ["CRM_OWNER_ID"]) if os.environ.get("CRM_OWNER_ID") else recipient_id(None)
    app = CRMApp(args.database, owner, os.environ.get("CRM_API_TOKEN", ""))
    with closing(open_database(args.database)) as db:
        migrate(db)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.app = app
    print(f"CRM API доступен локально на http://127.0.0.1:{server.server_port}/api/leads")
    print(f"Сайт: http://127.0.0.1:{server.server_port}/")
    print(f"CRM: http://127.0.0.1:{server.server_port}/crm")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
