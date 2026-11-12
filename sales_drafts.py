"""Короткие черновики по исходному запросу. Отправок и внешнего AI нет."""

import re


def make_draft(content, categories, language="ru"):
    english = language == "en"
    clauses = [part.strip(" \t•—") for part in re.split(r"[\n.!?;]+", content)]
    request = re.compile(
        r"нуж|ищ|хочу|хотим|планируем|вручную|сайт|лендинг|бот|"
        r"need|looking for|seeking|website|manually|app", re.IGNORECASE,
    )
    task = next((part for part in clauses if request.search(part)), "")
    task = re.sub(r"https?://\S+|@[a-zA-Z0-9_]+", "", task).strip(" ,:—")
    task = task[:140].rstrip() + ("…" if len(task) > 140 else "")
    value = content.casefold()
    questions = []
    if any(category in categories for category in ("Боты", "Приложения", "CRM и автоматизация")):
        if not re.search(r"запис|заявк|заказ|оплат|crm|таблиц|booking|orders?|leads?|payment|sheets", value):
            questions.append(("Какой основной сценарий нужен в первой версии?",
                              "What should the first version do?"))
        if not re.search(r"интеграц|подключ|crm|таблиц|integration|sheets", value):
            questions.append(("Нужны подключения к CRM, таблицам или другим сервисам?",
                              "Should it connect to a CRM, spreadsheets, or other services?"))
    elif "Сайты и магазины" in categories:
        if not re.search(r"страниц|каталог|корзин|лендинг|landing|pages?|catalog|cart", value):
            questions.append(("Какие страницы и функции нужны?", "Which pages and features do you need?"))
        if not re.search(r"референс|пример|https?://(?!t\.me)|reference|example", value):
            questions.append(("Есть пример сайта, на который стоит ориентироваться?",
                              "Do you have a reference website in mind?"))
    elif "Дизайн" in categories and not re.search(r"референс|стиль|reference|style", value):
        questions.append(("Есть пожелания по стилю или референсы?", "Do you have a preferred style or references?"))
    elif "Видео, фото и звук" in categories and not re.search(r"исходник|материал|footage|source files", value):
        questions.append(("Исходные материалы уже готовы?", "Are the source materials ready?"))
    if not re.search(r"срок|до\s+\d|к\s+\d|(?:за|через)\s+\d|deadline|by\s|within|срочно|urgent", value):
        questions.append(("К какой дате нужен результат?", "What is your target deadline?"))
    if not re.search(r"бюджет|budget|₽|руб|[$€]", value):
        questions.append(("Какой бюджет рассматриваете?", "What budget range do you have in mind?"))
    questions.append(("Обсудим первый этап работы?", "Would you like to discuss the first milestone?"))
    question = questions[0][1 if english else 0]
    if english:
        return f'Hi! I can help with your request “{task}”. {question}' if task else f"Hi! I can help with this project. {question}"
    return f"Здравствуйте! Могу помочь с задачей «{task}». {question}" if task else f"Здравствуйте! Могу помочь с проектом. {question}"
