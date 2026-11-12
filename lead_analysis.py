"""Объяснимая классификация без сетевых вызовов и языковых моделей."""

import re

from bot import (
    CATEGORY_PATTERNS,
    CLOSED_REQUEST,
    DIRECTIONS,
    EARLY_NEED,
    EMPLOYMENT,
    OFFER,
    PROMOTION,
    classify,
)

VERSION = "rules-v4"
INTENTS = {
    "direct_order": "Готовый запрос",
    "contractor_search": "Поиск подрядчика",
    "recommendation": "Рекомендация специалиста",
    "early_need": "Ранняя потребность",
    "uncertain": "Требует проверки",
    "closed": "Запрос закрыт",
    "promotion": "Реклама услуг",
    "employment": "Вакансия",
    "irrelevant": "Другая тема",
}
PROMO_EN = re.compile(
    r"\b(?:we|i)\s+(?:build|create|offer|develop)\b|\bour services\b|"
    r"ищу\s+(?:клиентов|заказы|работу)|заработок\s+без\s+опыта",
    re.IGNORECASE,
)
RECOMMENDATION = re.compile(r"посоветуй\w*|порекомендуй\w*|recommend\w*", re.IGNORECASE)
CONTRACTOR = re.compile(r"\b(?:ищу|ищем|looking\s+for|seeking|найти\s+(?:разработчика|подрядчика))\b", re.IGNORECASE)
ADVERTISING = re.compile(
    r"\bищ(?:ете|ешь)\s+(?:клиент\w*|подработ\w*|занятость\w*|заработ\w*|работ\w*)|"
    r"\b(?:л[её]гк\w*\s+подработ\w*|ноль\s+вложений|без\s+вложений|"
    r"почему\s+выбирают\s+нас|мы\s+знаем,?\s+как\s+увеличить\s+ваш\s+доход)|"
    r"\b(?:предлагаем|оказываем)\s+(?:услуги\s+)?рассыл\w*|"
    r"\b(?:переходите|заходите)\s+в\s+бот[^\n]{0,60}(?:заработ|задани)|"
    r"\b(?:easy money|no investment|earn money daily|get clients with our)\b",
    re.IGNORECASE,
)
URGENCY = re.compile(r"срочно|urgent|asap|до\s+\d{1,2}[./]|by\s+(?:friday|monday|tomorrow)", re.IGNORECASE)
BUDGET = re.compile(
    r"(?:бюджет|budget|оплат\w*)[^\n.!?]{0,35}\d|"
    r"\d[\d\s,.]*(?:₽|руб\w*|usd|eur|rub|dollars?)\b|[$€]\s*\d",
    re.IGNORECASE,
)
DETAILS = re.compile(
    r"каталог|корзин|запис[ьи]|заявк|оплат|интеграц|страниц|магазин|"
    r"catalog|cart|booking|payment|integration|pages?|orders?|leads?",
    re.IGNORECASE,
)
ADVICE = re.compile(
    r"нужен\s+совет|какую\s+(?:платформу|cms)|как\s+самому|"
    r"which\s+(?:platform|tool)|what\s+(?:platform|tool)",
    re.IGNORECASE,
)
SERVICE_FALLBACK = {
    "Сайты и магазины": r"сайт|website|landing|e-?commerce|online store",
    "Приложения": r"mini.?app|mobile app|desktop app|мобильн\w* приложени|десктопн\w* программ",
    "Боты": r"telegram.?bot|chat.?bot|телеграм.?бот",
    "CRM и автоматизация": r"автоматиз|automation|вручную|manually|систем\w* записи|crm",
    "Другие задачи разработки": r"developer|programmer|разработчик",
}


def analyze(content, directions=("dev",), author=None):
    value = content.casefold()
    selected = tuple(key for key in directions if key in DIRECTIONS)
    allowed = {category for key in selected for category in DIRECTIONS[key][1]}
    categories = classify(content, selected)
    hints = {
        category for category, pattern in CATEGORY_PATTERNS.items() if category in allowed and pattern.search(value)
    }
    if "dev" in selected:
        hints.update(category for category, pattern in SERVICE_FALLBACK.items() if re.search(pattern, value))
    author = author or {}
    blocked = None
    if CLOSED_REQUEST.search(value) or re.search(
        r"\b(?:неактуально|не\s+ищ(?:у|ем)|no longer needed|request closed)\b", value
    ):
        blocked = "closed"
    elif EMPLOYMENT.search(value):
        blocked = "employment"
    elif any(pattern.search(value) for pattern in (PROMOTION, OFFER, PROMO_EN, ADVERTISING)) or author.get("is_bot"):
        blocked = "promotion"
    if blocked:
        intent = blocked
    elif EARLY_NEED.search(value):
        intent = "early_need"
    elif ADVICE.search(value) and hints:
        intent = "uncertain"
    elif RECOMMENDATION.search(value) and hints:
        intent = "recommendation"
    elif CONTRACTOR.search(value) and (categories or hints):
        intent = "contractor_search"
    elif categories:
        intent = "direct_order"
    elif hints and re.search(
        r"вопрос|проект|помочь|проблем|нуж|ищ|хочу|как|обсужда|совет|запуска|"
        r"want|help|project|need|question|issue|problem|how|planning|manually|вручную",
        value,
    ):
        intent = "uncertain"
    else:
        intent = "irrelevant"
    if not categories:
        categories = [category for category in CATEGORY_PATTERNS if category in hints]
    language = "ru" if re.search(r"[а-яё]", value) else ("en" if re.search("[a-z]", value) else None)
    reasons = []
    features = {
        "has_contact": bool(author.get("username") or re.search(r"@[a-zA-Z][a-zA-Z0-9_]{4,31}", content)),
        "has_budget": bool(BUDGET.search(content)),
        "urgent": bool(URGENCY.search(content)),
        "specific_task": bool(DETAILS.search(content) or len(content.strip()) >= 70),
        "readiness": intent in {"direct_order", "contractor_search"},
    }
    if intent in {"closed", "employment", "promotion", "irrelevant"} or not selected or not categories:
        score, bucket = 0, "ignored"
        reasons.append({"points": 0, "reason": INTENTS[intent] if selected else "Направления выключены"})
    else:
        components = [
            (25, "Услуга соответствует выбранным направлениям"),
            (
                {"direct_order": 25, "contractor_search": 25, "recommendation": 15, "early_need": 10, "uncertain": 0}[
                    intent
                ],
                INTENTS[intent],
            ),
            (
                15 if features["specific_task"] else 5,
                "Подробность задачи" if features["specific_task"] else "Краткий запрос",
            ),
            (
                15 if features["readiness"] else 0,
                "Признак готовности заказать" if features["readiness"] else "Готовность заказать не подтверждена",
            ),
            (
                10 if features["has_contact"] else 0,
                "Доступен контакт" if features["has_contact"] else "Контакт не указан",
            ),
            (
                5 if features["has_budget"] else 0,
                "В тексте указан бюджет" if features["has_budget"] else "Бюджет не указан",
            ),
            (5 if features["urgent"] else 0, "Указана срочность" if features["urgent"] else "Срочность не указана"),
        ]
        reasons = [{"points": points, "reason": reason} for points, reason in components]
        score = min(100, sum(points for points, _ in components))
        bucket = (
            "lead" if score >= 65 and intent in {"direct_order", "contractor_search", "recommendation"} else "review"
        )
        if categories == ["Другие задачи разработки"] and not features["specific_task"]:
            bucket = "review"
    return {
        "version": VERSION,
        "engine": "rules",
        "intent": intent,
        "language": language,
        "score": score,
        "bucket": bucket,
        "categories": categories,
        "reasons": reasons,
        "features": features,
        "directions": list(selected),
    }
