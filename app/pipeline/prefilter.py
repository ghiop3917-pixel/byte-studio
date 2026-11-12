"""Дешёвый предфильтр: не подменяет LLM-классификацию и не присваивает score."""

import re
from dataclasses import dataclass

from app.config import Settings


@dataclass(frozen=True)
class KeywordRule:
    slug: str
    word: str
    kind: str
    is_regex: bool = False


@dataclass(frozen=True)
class FilterResult:
    accepted: bool
    reason: str
    category_hint: str | None = None


def validate_keyword(word: str, regex: bool, settings: Settings) -> str:
    word = word.strip()
    if not word or len(word) > settings.keyword_max_length:
        raise ValueError("Некорректная длина ключевого слова.")
    if regex:
        if (
            any(char in word for char in "()|{}")
            or re.search(r"\\[1-9]", word)
            or sum(word.count(c) for c in "*+?") > 1
        ):
            raise ValueError(
                "Регулярное выражение: без групп, альтернатив, обратных ссылок; не более одного квантификатора."
            )
        re.compile(word, re.IGNORECASE)
    return word


def prefilter(value: str, metadata: dict, rules: tuple[KeywordRule, ...], settings: Settings) -> FilterResult:
    if metadata.get("service"):
        return FilterResult(False, "service")
    if metadata.get("is_bot"):
        return FilterResult(False, "bot")
    if metadata.get("forwarded"):
        return FilterResult(False, "forwarded")
    if len(value.strip()) < settings.min_message_length:
        return FilterResult(False, "short")
    lowered = value.casefold()

    def matches(rule: KeywordRule) -> bool:
        if rule.is_regex:
            validate_keyword(rule.word, True, settings)
            return re.search(rule.word, value[: settings.regex_text_limit], re.IGNORECASE) is not None
        return rule.word.casefold() in lowered

    included = []
    excluded = set()
    for rule in rules:
        if matches(rule):
            if rule.kind == "exclude":
                excluded.add(rule.slug)
            elif rule.kind == "include":
                included.append(rule.slug)
    slug = next((slug for slug in included if slug not in excluded), None)
    return FilterResult(bool(slug), "candidate" if slug else "keywords", slug)
