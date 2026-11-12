"""Нормализация для кросспостов; оригинал хранится отдельно без изменений."""

import hashlib
import re
import unicodedata

URL = re.compile(r"(?:https?://|tg://|www\.)\S+|(?<!\w)t\.me/\S+", re.IGNORECASE)


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", URL.sub(" ", value)).casefold()
    cleaned = "".join(char if unicodedata.category(char)[0] in {"L", "N"} else " " for char in value)
    return " ".join(cleaned.split())


def text_hash(value: str) -> str:
    return hashlib.sha256(normalize_text(value).encode("utf-8")).hexdigest()
