"""Коммерческие черновики без внешнего AI и автоматической отправки."""
from crm import text, amount, currency, Conflict
from reliability import transaction


def prepare(service, lead_id, data):
    with transaction(service.db):
        return _prepare(service, lead_id, data)


def _prepare(service, lead_id, data):
    if set(data) - {"kind", "scope", "deliverables", "timeline", "price", "currency", "exclusions", "next_step", "due_at", "solution", "stack", "milestones", "payment", "support", "extra_costs"}:
        raise ValueError("Неизвестные поля документа.")
    lead = service.lead(lead_id, writable=True)
    kind = data.get("kind", "proposal")
    task = text(data.get("scope") or lead["task_description"] or lead["title"], 10000)
    if kind == "proposal":
        parts = ["Коммерческое предложение", "Задача: " + task]
        for field, label in (("solution", "Решение"), ("deliverables", "Результат и функции"), ("stack", "Технологии"), ("milestones", "Этапы"), ("timeline", "Сроки"), ("payment", "Условия оплаты"), ("support", "Поддержка"), ("extra_costs", "Дополнительные расходы"), ("exclusions", "Не входит в объём"), ("next_step", "Следующий шаг")):
            if data.get(field):
                parts.append(label + ": " + text(data[field], 10000))
        if data.get("price") is not None:
            price = amount(data["price"])
            unit = currency(data.get("currency"))
            if unit is None:
                raise ValueError("Укажи валюту цены.")
            parts.append(f"Стоимость: {price} {unit}")
        parts.append("Объём, стоимость и сроки требуют согласования обеими сторонами.")
    elif kind == "followup":
        if lead['status'] in {'rejected','archived','won'}:
            raise Conflict('Для закрытого проекта повторный отклик не готовится. Сначала проверь его актуальность.')
        parts = ["Здравствуйте! Возвращаюсь к обсуждению задачи: " + task,
                 text(data.get("next_step") or "Подскажите, проект ещё актуален? Готов обсудить следующий шаг.", 10000)]
    elif kind == "questions":
        parts = ["Здравствуйте! Уточним задачу «" + task + "»:", "Какой результат нужен в первой версии? Какие интеграции и материалы уже есть? Какие ограничения по бюджету и срокам? Как будем принимать результат?"]
    else:
        raise ValueError("Неизвестный тип документа.")
    draft_id = service.add_draft(lead_id, "\n\n".join(parts))
    reminder_id = None
    if data.get("due_at"):
        reminder_id = service.add_reminder(lead_id, "Проверить ответ клиента и следующий шаг", data["due_at"])
    return {"draft_id": draft_id, "reminder_id": reminder_id, "sent": False}
