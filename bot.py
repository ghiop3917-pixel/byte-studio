import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from contextlib import closing, nullcontext
from html import escape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


CATEGORIES = {
    "Сайты и магазины": r"сайт\w*|лендинг\w*|website\w*|web\s*site\w*|landing(?:\s+page)?|online\s+store|ecommerce|интернет[ -]магазин\w*|веб[ -]?сайт\w*|веб[ -]?портал\w*|(?:информационн\w*|корпоративн\w*)\s+портал\w*|онлайн[ -]каталог\w*|витрин\w*|tilda|тильд\w*|wordpress",
    "Приложения": r"(?:мини|веб|мобильн\w*|телеграм|десктопн\w*)[ -]?приложени\w*|mini[ -]?app\w*|(?:web|mobile|desktop)[ -]?(?:app\w*|application\w*)|приложени\w*|десктопн\w*\s+программ\w*",
    "Боты": r"(?:чат[ -]?)?бот(?:а|у|ом|е|ы|ов|ам|ами|ах)?|(?:telegram\s+)?(?:chat)?bots?",
    "CRM и автоматизация": r"crm|црм|срм|амосрм|amo\s?crm|битрикс\w*|автоматизац\w*|интеграц\w*|automation|automate\w*|integration\w*|n8n|систем\w*\s+записи|обрабатыва\w*\s+заявки\s+вручную",
    "Другие задачи разработки": r"разработчик\w*|программист\w*|фронтенд\w*|бэкенд\w*|фуллстек\w*|веб[ -]?сервис\w*|личн\w* кабинет\w*|api|mvp|парсер\w*|saas",
    "Дизайн": r"дизайн\w*|логотип\w*|баннер\w*|иллюстра\w*|figma|презентац\w*|инфографик\w*",
    "Видео, фото и звук": r"монтаж\w*|видеомонтаж\w*|рилс\w*|reels|видеоролик\w*|ретуш\w*|фотограф\w*|озвуч\w*|субтитр\w*",
    "Тексты и переводы": r"копирайт\w*|рерайт\w*|стать(?:я|ю|и|ей)|текст\w*|переводчик\w*|перевести\s+текст\w*|редактор\w*",
    "Маркетинг": r"smm|seo|сео|таргет\w*|реклам\w*|директолог\w*|продвижени\w*|маркетолог\w*",
    "Другие фриланс-заказы": r"фрилансер\w*|исполнител\w*|подрядчик\w*|разов\w*\s+задач\w*|проектн\w*\s+работ\w*",
}
CATEGORY_PATTERNS = {label: re.compile(rf"\b(?:{pattern})\b") for label, pattern in CATEGORIES.items()}
DIRECTIONS = {
    "dev": ("Разработка", ("Сайты и магазины", "Приложения", "Боты", "CRM и автоматизация", "Другие задачи разработки")),
    "design": ("Дизайн", ("Дизайн",)),
    "video": ("Видео и звук", ("Видео, фото и звук",)),
    "text": ("Тексты и переводы", ("Тексты и переводы",)),
    "marketing": ("Маркетинг", ("Маркетинг",)),
}
REQUEST = re.compile(
    r"\b(?:ищу|ищем|нуж(?:ен|на|но|ны)|требу(?:ется|ются)|надо|"
    r"кто\s+(?:может|возьм[её]тся|сделает)|"
    r"подскажите\s+кто|посоветуйте|порекомендуйте|"
    r"возьм[её]тся\s+ли\s+кто|есть\s+(?:заказ|задача|проект)|looking for|need|seeking|can anyone)\b"
)
PROMOTION = re.compile(
    r"(?:^|[.!?\n]\s*)\s*(?:я\s+|мы\s+)?(?:"
    r"делаю|делаем|создаю|создаём|создаем|разрабатываю|разрабатываем|"
    r"предлага(?:ю|ем)\s+(?:услуги|разработку|создание|продвижение)|"
    r"оказываем\s+услуги|ищу\s+(?:работу|заказы|клиентов)|ищем\s+клиентов)\b"
)
EMPLOYMENT = re.compile(r"\b(?:в\s+штат|трудоустройство|оклад|full[ -]?time|оформление\s+по\s+тк)\b")
NEGATION = re.compile(r"\bне\s+нуж(?:ен|на|но|ны)\b")
OFFER = re.compile(
    r"\b(?:услуги\s+и\s+прайс|наш[и]?\s+услуги|бер[её]м\s+заказы|"
    r"принима[юе]\w*\s+заказы|готов[аы]?\s+(?:сделать|разработать)|"
    r"привлекаем\s+целевую\s+аудиторию|заказать\s+[^\n.!?]{1,65}:\s*(?:@|https?://)|"
    r"ищ[уе]м?\s+(?:бизнес[ -]?)?партн[её]р\w*|пассивн\w*\s+доход)\b"
)
PROJECT_ACTION = re.compile(
    r"\b(?:сделать|создать|разработать|доработать|переделать|собрать|настроить|"
    r"подключить|починить|интегрировать|оформить|сверстать|смонтировать|написать|заказать)\b"
)
PROJECT_SCOPE = re.compile(
    r"\b(?:лендинг\w*|landing|сайт\w*|интернет[ -]магазин\w*|веб[ -]?сайт\w*|"
    r"веб[ -]?портал\w*|(?:информационн\w*|корпоративн\w*)\s+портал\w*|онлайн[ -]каталог\w*|"
    r"tilda|тильд\w*|wordpress|"
    r"мини[ -]?приложени\w*|mini[ -]?app\w*|mvp|прототип\w*|"
    r"(?:чат[ -]?)?бот(?:а|у|ом|е|ы|ов)?|crm|црм|срм|битрикс\w*|"
    r"автоматизац\w*|интеграц\w*|n8n|парсер\w*|личн\w*\s+кабинет\w*)\b"
)
COMPLEX_PROJECT = re.compile(
    r"\b(?:высоконагруженн\w*|highload|микросервисн\w*|"
    r"enterprise|банковск\w*\s+(?:платформ\w*|систем\w*)|"
    r"нативн\w*\s+(?:мобильн\w*\s+)?приложени\w*|"
    r"крупн\w*\s+(?:маркетплейс\w*|платформ\w*)|на\s+фултайм)\b"
)
NON_DEVELOPMENT = re.compile(
    r"\b(?:дизайн\w*|макет\w*|логотип\w*|баннер\w*|текст\w*|реклам\w*|seo|сео)"
    r"\s+(?:для\s+)?(?:сайт\w*|лендинг\w*|магазин\w*)\b"
)
DEVELOPMENT_ACTION = re.compile(
    r"\b(?:разработ\w*|сверстать|верстальщик\w*|запрограммировать|"
    r"подключить|интегрировать|доработать|починить)\b"
)
REPEAT_WINDOW = 48 * 60 * 60
CLOSED_REQUEST = re.compile(
    r"(?:исполнител\w*\s+(?:уже\s+)?найден|(?:уже\s+)?нашли\s+(?:исполнител\w*|подрядчик\w*|разработчик\w*)|"
    r"заказ\s+закрыт|вопрос\s+реш[её]н|(?:developer|contractor)\s+(?:already\s+)?found|"
    r"(?:already\s+)?found\s+(?:a\s+)?(?:developer|contractor)|position\s+filled)",
    re.IGNORECASE,
)
EARLY_NEED = re.compile(
    r"(?:обрабатыва\w*\s+заявки\s+вручную|manually\s+(?:process|handl)\w*\s+(?:orders|leads)|"
    r"(?:process|handl)\w*\s+(?:orders|leads)\s+manually|"
    r"сайт\s+(?:перестал\s+|не\s+)справля\w*|нуж\w*\s+автоматиз\w*)", re.IGNORECASE,
)
EXTRA_SCOPE = re.compile(
    r"\b(?:website\w*|web\s*site|landing\s+page|online\s+store|ecommerce|"
    r"(?:telegram\s+)?(?:chat)?bots?|(?:мобильн\w*|десктопн\w*)\s+(?:приложени\w*|программ\w*)|"
    r"(?:mobile|desktop|web)\s+(?:app\w*|application\w*)|систем\w*\s+записи|automation|automate\w*)\b"
)


def classify(text, directions=("dev",)):
    value = text.casefold()
    if any(pattern.search(value) for pattern in (PROMOTION, EMPLOYMENT, OFFER, CLOSED_REQUEST)):
        return []
    categories = set()
    if "dev" in directions and EARLY_NEED.search(value):
        categories.add("Сайты и магазины" if "сайт" in value else "CRM и автоматизация")
    for clause in re.split(r"[.!?;\n]+", value):
        clause = NEGATION.sub("", clause)
        if not REQUEST.search(clause) and not (
            re.search(r"\b(?:хочу|хотим|планируем)\b", clause) and PROJECT_ACTION.search(clause)
        ):
            continue
        if ("dev" in directions and (PROJECT_SCOPE.search(clause) or EXTRA_SCOPE.search(clause)) and not COMPLEX_PROJECT.search(value)
                and (not NON_DEVELOPMENT.search(clause) or DEVELOPMENT_ACTION.search(clause))):
            matched = {label for label in DIRECTIONS["dev"][1] if CATEGORY_PATTERNS[label].search(clause)}
            categories.update(matched or {"Другие задачи разработки"})
        for key in directions:
            if key != "dev":
                categories.update(label for label in DIRECTIONS[key][1] if CATEGORY_PATTERNS[label].search(clause))
    return [label for label in CATEGORY_PATTERNS if label in categories]


def fingerprint(text):
    normalized = " ".join(text.casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def message_link(message):
    chat = message["chat"]
    chat_id = str(chat["id"])
    message_id = message["message_id"]
    if chat.get("username"):
        return f"https://t.me/{chat['username']}/{message_id}"
    if chat_id.startswith("-100") and chat["type"] == "supergroup":
        return f"https://t.me/c/{chat_id[4:]}/{message_id}"
    return None


class TelegramError(Exception):
    def __init__(self, code, retry_after=0):
        self.code = code
        self.retry_after = retry_after


class Telegram:
    def __init__(self, token):
        self.base_url = f"https://api.telegram.org/bot{token}/"

    def call(self, method, **params):
        request = Request(
            self.base_url + method,
            data=json.dumps(params).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=40) as response:
                result = json.load(response)
        except HTTPError as error:
            try:
                result = json.load(error)
            except (ValueError, OSError):
                raise TelegramError(error.code) from None
            finally:
                error.close()
        except (URLError, OSError, ValueError):
            raise TelegramError(0) from None
        if not result.get("ok"):
            raise TelegramError(
                result.get("error_code", 0),
                result.get("parameters", {}).get("retry_after", 0),
            )
        return result["result"]

    def send(self, chat_id, text, link=None, *, parse_mode=None, lead_id=None, contact=None):
        options = {}
        if parse_mode:
            options["parse_mode"] = parse_mode
        keyboard = [[{"text": "↗ Открыть заявку", "url": link}]] if link else []
        if contact:
            keyboard.append([{"text": "💬 Написать заказчику", "url": contact}])
        if lead_id is not None:
            keyboard.append([{"text": "⭐ Сохранить заявку", "callback_data": f"save:{lead_id}"}])
            keyboard.append([
                {"text": "👍 Подходит", "callback_data": f"fb:approved:{lead_id}"},
                {"text": "👎 Мусор", "callback_data": f"fb:rejected:{lead_id}"},
                {"text": "🤔 Не уверен", "callback_data": f"fb:needs_review:{lead_id}"},
            ])
        if keyboard:
            options["reply_markup"] = {"inline_keyboard": keyboard}
        return self.call(
            "sendMessage", chat_id=chat_id, text=text,
            link_preview_options={"is_disabled": True}, **options,
        )


def handle_update(telegram, db, update, owner_id, source_ids, bot_username, *, directions=("dev",),
                  record_leads=False, identity=None, manage_transaction=True, analysis=None):
    message = update.get("message")
    if not message:
        return
    chat = message["chat"]
    sender = message.get("from", {})
    text = (message.get("text") or message.get("caption") or "").strip()
    if not text or sender.get("is_bot"):
        return

    if chat["type"] == "private":
        if sender.get("id") == owner_id and text in {"/start", "/status"}:
            telegram.send(owner_id, f"Бот запущен. Источников в списке: {len(source_ids)}.")
        elif owner_id is None and text in {"/start", "/id"}:
            telegram.send(chat["id"], f"Твой ID: {chat['id']}")
        return

    if chat["type"] not in {"group", "supergroup"}:
        return
    if sender.get("id") == owner_id and text.casefold() == f"/source@{bot_username}".casefold():
        telegram.send(owner_id, f"Источник: {chat.get('title', chat['id'])}\nID: {chat['id']}")
        return
    if chat["id"] not in source_ids or text.startswith("/"):
        return
    categories = analysis["categories"] if analysis is not None else classify(text, directions)
    if not categories:
        return

    key = fingerprint(identity if identity is not None else text)
    timestamp = int(time.time())
    recent = db.execute("SELECT sent_at FROM delivered WHERE fingerprint = ?", (key,)).fetchone()
    if recent and timestamp - recent[0] < REPEAT_WINDOW:
        return

    source = chat.get("title") or str(chat["id"])
    link = message_link(message)
    excerpt = text[:1500] + ("…" if len(text) > 1500 else "")
    original = "" if link else f" · сообщение № {message['message_id']}"
    explicit_contact = re.search(
        r"(?:контакт(?:\s+для\s+связи)?|для\s+связи|писать|пишите|отклик)\s*[:—-]?\s*"
        r"(?:@|https?://t\.me/)([a-zA-Z][a-zA-Z0-9_]{4,31})\b", text, re.IGNORECASE,
    )
    username = explicit_contact[1] if explicit_contact else (sender.get("username") or "")
    contact = f"https://t.me/{username}" if re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{4,31}", username) else None
    from sales_drafts import make_draft
    language = analysis["language"] if analysis is not None else (
        "ru" if re.search(r"[а-яёА-ЯЁ]", text) else "en"
    )
    draft = make_draft(text, categories, language)
    assessment = ""
    if analysis is not None:
        from lead_analysis import INTENTS
        explanation = "; ".join(
            f"+{reason['points']} {reason['reason']}" for reason in analysis["reasons"] if reason["points"]
        )
        assessment = (
            f"📊 <b>Оценка правил {analysis['score']}/100</b> · {escape(INTENTS[analysis['intent']])}\n"
            f"{escape(explanation)}\n\n"
        )
    card = (
        f"⚡ <b>Новая заявка</b>\n"
        f"<b>{escape(' · '.join(categories))}</b>\n\n"
        f"{assessment}"
        f"<blockquote>{escape(excerpt)}</blockquote>\n\n"
        f"📍 {escape(source + original)}\n\n"
        f"✍️ <b>Черновик отклика</b>\n{escape(draft)}"
    )
    with db if manage_transaction else nullcontext():
        lead_id = None
        if record_leads:
            lead_id = db.execute(
                "INSERT INTO leads (recipient, title, source, url) VALUES (?, ?, ?, ?)",
                (owner_id, " ".join(excerpt.split())[:140], source, link),
            ).lastrowid
        telegram.send(owner_id, card, link, parse_mode="HTML", lead_id=lead_id, contact=contact)
        db.execute(
            "INSERT INTO delivered VALUES (?, ?) "
            "ON CONFLICT(fingerprint) DO UPDATE SET sent_at = excluded.sent_at",
            (key, timestamp),
        )


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not re.fullmatch(r"\d+:[\w-]+", token, flags=re.ASCII):
        raise SystemExit("Укажи корректный TELEGRAM_BOT_TOKEN.")
    try:
        owner_id = int(os.environ["TELEGRAM_OWNER_ID"]) if os.environ.get("TELEGRAM_OWNER_ID") else None
        source_ids = {
            int(item.strip()) for item in os.environ.get("TELEGRAM_SOURCE_IDS", "").split(",")
            if item.strip()
        }
        if (owner_id is not None and owner_id <= 0) or any(item >= 0 for item in source_ids):
            raise ValueError
    except ValueError:
        raise SystemExit("Укажи положительный ID владельца и отрицательные ID источников через запятую.") from None
    if owner_id is None and source_ids:
        raise SystemExit("Сначала укажи TELEGRAM_OWNER_ID.")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    telegram = Telegram(token)
    try:
        bot_username = telegram.call("getMe")["username"]
    except TelegramError as error:
        raise SystemExit(f"Не удалось подключиться к Telegram. Код: {error.code}.") from None
    logging.info("Бот запущен. Источников: %s. Режим определения ID: %s.", len(source_ids), owner_id is None)

    db_path = Path(__file__).with_name("leads.sqlite3")
    with closing(sqlite3.connect(db_path)) as db:
        with db:
            db.execute("CREATE TABLE IF NOT EXISTS delivered (fingerprint TEXT PRIMARY KEY, sent_at INTEGER NOT NULL)")
        offset = 0
        retry_delay = 1
        while True:
            try:
                updates = telegram.call(
                    "getUpdates", offset=offset, timeout=25,
                    allowed_updates=["message"],
                )
                if not updates:
                    offset = 0
                for update in updates:
                    handle_update(telegram, db, update, owner_id, source_ids, bot_username)
                    offset = update["update_id"] + 1
                retry_delay = 1
            except TelegramError as error:
                if error.code in {400, 401, 403, 404, 409}:
                    raise SystemExit(
                        f"Telegram остановил обработку (код {error.code}). "
                        "Проверь токен, личный чат с ботом и отсутствие другого экземпляра или вебхука."
                    ) from None
                delay = max(retry_delay, error.retry_after)
                logging.warning("Ошибка Telegram (%s). Повтор через %s сек.", error.code, delay)
                time.sleep(delay)
                retry_delay = min(retry_delay * 2, 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Бот остановлен.")
