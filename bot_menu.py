import asyncio
import json
import logging
import sqlite3
import re
from contextlib import closing
from html import escape
from pathlib import Path

from bot import DIRECTIONS, TelegramError

PREFERENCES = Path(__file__).with_name("directions.json")
MONITOR_DB = Path(__file__).with_name("monitor.sqlite3")


def saved_directions(recipient):
    if not PREFERENCES.exists():
        return ("dev",)
    saved = json.loads(PREFERENCES.read_text(encoding="utf-8"))
    return tuple(saved.get(str(recipient), ("dev",)))


def set_directions(recipient, selected):
    saved = json.loads(PREFERENCES.read_text(encoding="utf-8")) if PREFERENCES.exists() else {}
    saved[str(recipient)] = [key for key in DIRECTIONS if key in selected]
    temporary = PREFERENCES.with_suffix(".tmp")
    temporary.write_text(json.dumps(saved), encoding="utf-8")
    temporary.replace(PREFERENCES)


def screen(page, group_count, selected=("dev",)):
    if page == "directions":
        buttons = [[{"text": f"{'✅' if key in selected else '◻️'} {title}", "callback_data": f"dir:{key}"}]
                   for key, (title, _) in DIRECTIONS.items()]
        buttons.append([{"text": "‹ Главное меню", "callback_data": "home"}])
        return (
            "🎛 <b>Направления</b>\n\n"
            "Нажми на направление, чтобы включить или выключить его. Можно выбрать несколько.",
            buttons,
        )
    if page == "status":
        return (
            f"📡 <b>Статус мониторинга</b>\n\n"
            f"Подключено групп: <b>{group_count}</b>.\n"
            f"Направления: <b>{', '.join(DIRECTIONS[key][0] for key in selected) or 'доставка на паузе'}</b>.",
            [[{"text": "‹ Главное меню", "callback_data": "home"}]],
        )
    if page == "examples":
        return (
            "🎯 <b>Какие заявки ищу</b>\n\n"
            "• Сайты, интернет-магазины и веб-порталы\n"
            "• Telegram-боты и мини-приложения\n"
            "• MVP, CRM-интеграции и автоматизация\n"
            "• Дизайн, видео, тексты и маркетинг при включении\n\n"
            "Например: «Нужно разработать сайт с каталогом и формой заявки».",
            [[{"text": "‹ Главное меню", "callback_data": "home"}]],
        )
    return (
        "⚡ <b>Заказ Сигнал</b>\n\n"
        "Заявки на разработку из бизнес-чатов. "
        "В каждой карточке есть текст задачи, источник и ссылка на оригинал.",
        [[{"text": "🎛 Направления", "callback_data": "directions"}],
         [{"text": "🔎 На проверку", "callback_data": "reviews"},
          {"text": "📈 Качество", "callback_data": "quality"}],
         [{"text": "⭐ Сохранённые", "callback_data": "saved"},
          {"text": "🕘 Последние", "callback_data": "recent"}],
         [{"text": "📡 Статус", "callback_data": "status"},
          {"text": "🎯 Что ищу", "callback_data": "examples"}]],
    )


def show(telegram, chat_id, page, group_count, message_id=None):
    if page in {"quality", "reviews", "sources_quality", "maintenance"} or re.fullmatch(r"(?:reviews|review):\d{1,18}", page):
        text, keyboard = quality_screen(chat_id, page)
    elif page in {"saved", "recent"}:
        with closing(sqlite3.connect(MONITOR_DB)) as db:
            rows = db.execute(
                "SELECT title, source, url FROM leads WHERE recipient = ? AND (? = 0 OR saved = 1) "
                "ORDER BY id DESC LIMIT 8",
                (chat_id, page == "saved"),
            ).fetchall()
        heading = "⭐ Сохранённые заявки" if page == "saved" else "🕘 Последние заявки"
        items = []
        keyboard = []
        for index, (title, source, url) in enumerate(rows, 1):
            items.append(f"<b>{index}. {escape(title)}</b>\n{escape(source)}")
            if url:
                keyboard.append([{"text": f"↗ Открыть заявку {index}", "url": url}])
        text = f"<b>{heading}</b>\n\n" + (
            "\n\n".join(items) if items else "Пока здесь пусто. Новые карточки появятся после следующей заявки."
        )
        keyboard.append([{"text": "‹ Главное меню", "callback_data": "home"}])
    else:
        text, keyboard = screen(page, group_count, saved_directions(chat_id))
    options = {
        "chat_id": chat_id, "text": text, "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": keyboard},
    }
    if message_id is None:
        return telegram.call("sendMessage", **options)
    return telegram.call("editMessageText", message_id=message_id, **options)


def dispatch(telegram, update, recipient, group_count):
    message = update.get("message", {})
    chat = message.get("chat", {})
    if chat.get("type") == "private" and chat.get("id") == recipient:
        page = {"/start": "home", "/menu": "home", "/status": "status",
                "/directions": "directions", "/reviews": "reviews", "/quality": "quality",
                "/sourcesquality":"sources_quality", "/maintenance":"maintenance"}.get(message.get("text"))
        if page:
            show(telegram, recipient, page, group_count)
    callback = update.get("callback_query")
    if not callback or callback.get("from", {}).get("id") != recipient:
        return
    message = callback.get("message", {})
    if message.get("chat", {}).get("id") != recipient:
        return
    page = callback.get("data", "")
    reason_match = re.fullmatch(r'fbr:(spam|irrelevant|closed|duplicate):([1-9]\d{0,17})',page)
    if reason_match:
        from quality import Quality
        from reliability import open_database, transaction
        from crm import NotFound, Conflict
        reasons={'spam':'Реклама / рассылка','irrelevant':'Нецелевая задача','closed':'Заказ закрыт','duplicate':'Дубликат'}
        response='Причина сохранена'
        try:
            with closing(open_database(MONITOR_DB)) as db, transaction(db):
                if not db.execute('SELECT 1 FROM bot_feedback_receipts WHERE callback_id=? AND recipient=?',(callback['id'],recipient)).fetchone():
                    Quality(db,recipient).feedback(int(reason_match[2]),'rejected',reasons[reason_match[1]])
                    db.execute('INSERT INTO bot_feedback_receipts VALUES (?,?,?)',(callback['id'],recipient,response))
        except (NotFound,Conflict,ValueError):response='Заявка недоступна'
        telegram.call('answerCallbackQuery',callback_query_id=callback['id'],text=response)
        return
    feedback = re.fullmatch(r"(fb|reviewfb):(approved|rejected|needs_review):([1-9]\d{0,17})", page)
    if feedback:
        from crm import Conflict, NotFound
        from quality import Quality
        from reliability import open_database, transaction
        response = "Решение сохранено"
        fresh_decision = False
        try:
            with closing(open_database(MONITOR_DB)) as db, transaction(db):
                receipt = db.execute("SELECT response FROM bot_feedback_receipts WHERE callback_id=? AND recipient=?",
                                     (callback["id"], recipient)).fetchone()
                if receipt:
                    response = receipt["response"]
                else:
                    service = Quality(db, recipient)
                    if feedback[1] == "fb":
                        service.feedback(int(feedback[3]), feedback[2])
                    else:
                        service.decide(int(feedback[3]), feedback[2])
                        if feedback[2] == "approved":
                            response = "Передано в очередь обработки"
                    db.execute("INSERT INTO bot_feedback_receipts VALUES (?,?,?)",
                               (callback["id"], recipient, response))
                    fresh_decision = True
        except (NotFound, Conflict, ValueError):
            response = "Не удалось применить решение. Обнови карточку."
        telegram.call("answerCallbackQuery", callback_query_id=callback["id"], text=response)
        if fresh_decision and feedback[1]=='fb' and feedback[2]=='rejected' and message.get('reply_markup'):
            keyboard=message['reply_markup'].get('inline_keyboard',[])
            if not any(str(button.get('callback_data','')).startswith('fbr:') for row in keyboard for button in row):
                keyboard=keyboard+[[{'text':'Реклама','callback_data':f'fbr:spam:{feedback[3]}'},{'text':'Не мой заказ','callback_data':f'fbr:irrelevant:{feedback[3]}'}],
                                   [{'text':'Закрыт','callback_data':f'fbr:closed:{feedback[3]}'},{'text':'Повтор','callback_data':f'fbr:duplicate:{feedback[3]}'}]]
                try:telegram.call('editMessageReplyMarkup',chat_id=recipient,message_id=message['message_id'],reply_markup={'inline_keyboard':keyboard})
                except TelegramError:logging.warning('Причина оценки сохранится при выборе в CRM.')
        if feedback[1] == "reviewfb" and response not in {"Не удалось применить решение. Обнови карточку."}:
            show(telegram, recipient, f"review:{feedback[3]}", group_count, message["message_id"])
        return
    if page.startswith("save:") and page[5:].isascii() and page[5:].isdigit() and len(page[5:]) <= 18:
        with closing(sqlite3.connect(MONITOR_DB)) as db, db:
            updated = db.execute(
                "UPDATE leads SET saved = 1 WHERE id = ? AND recipient = ?",
                (int(page[5:]), recipient),
            ).rowcount
        telegram.call("answerCallbackQuery", callback_query_id=callback["id"],
                      text="Заявка сохранена" if updated else "Заявка недоступна")
        return
    telegram.call("answerCallbackQuery", callback_query_id=callback["id"])
    if page.startswith("dir:") and page[4:] in DIRECTIONS:
        selected = set(saved_directions(recipient))
        selected.symmetric_difference_update({page[4:]})
        set_directions(recipient, selected)
        page = "directions"
    if page in {"home", "status", "examples", "directions", "recent", "saved", "reviews", "quality", "sources_quality", "maintenance"} or re.fullmatch(r"(?:reviews|review):\d{1,18}", page):
        show(telegram, recipient, page, group_count, message["message_id"])


def quality_screen(recipient, page):
    from lead_analysis import INTENTS
    from quality import Quality
    from reliability import open_database
    back = [{"text": "‹ Главное меню", "callback_data": "home"}]
    with closing(open_database(MONITOR_DB)) as db:
        service = Quality(db, recipient)
        if page=='sources_quality':
            from insights import Insights
            report=Insights(db,recipient).report()
            lines=['📊 <b>Источники за 30 дней</b>','Полезность — по твоим оценкам, не по баллам фильтра.']
            for source in report['sources'][:8]:
                rate=f"{source['observed_approval_rate']:.0%} из {source['judged']} оценок" if source['judged']>=5 else 'мало оценок'
                lines.append(f"\n<b>{escape(source['name'])}</b>\nСообщений: {source['messages']} · карточек: {source['leads']}\nПодходит: {source['approved']} · мусор: {source['rejected']} · {rate}")
            return '\n'.join(lines),[[{'text':'‹ Качество','callback_data':'quality'}]]
        if page=='maintenance':
            from maintenance import operations_report
            report=operations_report(db,recipient)
            return ('🛠 <b>Обслуживание</b>\n\n'
                    f"Проверок внимания: {len(report['health']['alerts'])}\n"
                    f"Проверенных плановых копий: {len(report['backups'])}\n"
                    'Расписание и подробности — в CRM. Служебные рассылки выключены.'),[back]
        if page == "quality":
            report = service.report()
            rate = report["observed_approval_rate"]
            judgement = f"{rate:.0%}" if rate is not None else "ещё нет оценок"
            labels = report["lead_feedback"]
            text = (
                "📈 <b>Качество заявок</b>\n\n"
                f"Подходит: {labels.get('approved', 0)} · Мусор: {labels.get('rejected', 0)}\n"
                f"Доля одобренных среди оценённых: {judgement}\n"
                f"На проверке: {report['reviews'].get('pending', 0)}\n\n"
                "Оценка выполняется правилами. Полнота поиска ещё не измерена. "
                "Обратная связь сохраняется, изменения правил требуют отдельного решения."
            )
            return text, [[{'text':'📊 Качество чатов','callback_data':'sources_quality'},
                           {'text':'🛠 Обслуживание','callback_data':'maintenance'}],back]
        if page.startswith("review:"):
            review = service.review(int(page.split(":")[1]))
            result = review["analysis"]
            explanation = "\n".join(f"+{reason['points']} {reason['reason']}" for reason in result["reasons"])
            statuses = {"pending": "Ждёт проверки", "approved": "Одобрено", "rejected": "Отклонено"}
            text = (
                f"🔎 <b>Проверка #{review['raw_id']}</b> · {statuses[review['status']]}\n"
                f"{escape(INTENTS[result['intent']])} · Оценка правил {result['score']}/100\n\n"
                f"<blockquote>{escape(review['original_text'][:1500])}</blockquote>\n"
                f"📍 {escape(review['source'])}\n\n{escape(explanation)}"
            )
            keyboard = [[
                {"text": "✅ Подходит", "callback_data": f"reviewfb:approved:{review['raw_id']}"},
                {"text": "👎 Мусор", "callback_data": f"reviewfb:rejected:{review['raw_id']}"},
                {"text": "🤔 Не уверен", "callback_data": f"reviewfb:needs_review:{review['raw_id']}"},
            ]]
            if review["url"]:
                keyboard.append([{"text": "↗ Оригинал", "url": review["url"]}])
            keyboard.append([{"text": "‹ Очередь проверки", "callback_data": "reviews"}])
            return text, keyboard
        offset = int(page.split(":")[1]) if ":" in page else 0
        reviews = service.reviews(limit=8, offset=offset)
        keyboard = [[{
            "text": f"#{item['raw_id']} · {item['score']}/100 · {item['original_text'][:35]}",
            "callback_data": f"review:{item['raw_id']}",
        }] for item in reviews["items"]]
        navigation = []
        if offset:
            navigation.append({"text": "‹ Назад", "callback_data": f"reviews:{max(0, offset - 8)}"})
        if offset + 8 < reviews["total"]:
            navigation.append({"text": "Дальше ›", "callback_data": f"reviews:{offset + 8}"})
        if navigation:
            keyboard.append(navigation)
        keyboard.append(back)
        return (
            f"🔎 <b>На проверку — {reviews['total']}</b>\n\n"
            "Открой сообщение и оцени его. Одобренное попадёт в CRM через очередь обработки.",
            keyboard,
        )


async def run_menu(telegram, recipient, group_count):
    offset = 0
    failures = 0
    while True:
        try:
            updates = await asyncio.to_thread(
                telegram.call, "getUpdates", offset=offset, timeout=20,
                allowed_updates=["message", "callback_query"],
            )
        except TelegramError as error:
            failures += 1
            logging.warning("Ошибка меню Telegram: %s.", error.code)
            if failures >= 5 or error.code in {401, 403, 404, 409}:
                raise RuntimeError("Меню Telegram требует проверки доступа.") from None
            await asyncio.sleep(max(min(5 * 2 ** (failures - 1), 60), error.retry_after))
            continue
        failures = 0
        for update in updates:
            offset = update["update_id"] + 1
            try:
                await asyncio.to_thread(dispatch, telegram, update, recipient(), group_count())
            except (TelegramError, sqlite3.Error, ValueError, OSError) as error:
                logging.warning("Не удалось обновить меню Telegram: %s.", type(error).__name__)
