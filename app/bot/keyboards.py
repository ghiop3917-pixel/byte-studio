"""Типизированные callback_data без ID пользователя и пользовательского текста."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot import texts
from app.bot.accounts import AccountView
from app.config import Settings


class PageData(CallbackData, prefix="page"):
    name: str


class CategoryData(CallbackData, prefix="category"):
    category_id: int
    enabled: bool


class ScoreData(CallbackData, prefix="score"):
    value: int


class GeoData(CallbackData, prefix="geo"):
    action: str


def home_button() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=texts.BTN_HOME, callback_data=PageData(name="home").pack())]


def keyboard(page: str, view: AccountView, settings: Settings) -> InlineKeyboardMarkup:
    if page == "categories":
        rows = []
        for category_id, slug, title, selected in view.categories:
            label = ("✅ " if selected else "+ " if slug == "seo" else "◻ ") + title
            rows.append(
                [
                    InlineKeyboardButton(
                        text=label, callback_data=CategoryData(category_id=category_id, enabled=not selected).pack()
                    )
                ]
            )
    elif page == "settings":
        rows = [
            [
                InlineKeyboardButton(
                    text=("✓ " if score == view.min_score else "") + str(score),
                    callback_data=ScoreData(value=score).pack(),
                )
                for score in settings.score_choices
            ],
            [
                InlineKeyboardButton(text=texts.BTN_GEO_EDIT, callback_data=GeoData(action="edit").pack()),
                InlineKeyboardButton(text=texts.BTN_GEO_CLEAR, callback_data=GeoData(action="clear").pack()),
            ],
        ]
    else:
        rows = [
            [InlineKeyboardButton(text=label, callback_data=PageData(name=name).pack())]
            for name, label in (
                ("categories", texts.BTN_CATEGORIES),
                ("settings", texts.BTN_SETTINGS),
                ("subscription", texts.BTN_SUBSCRIPTION),
                ("about", texts.BTN_ABOUT),
                ("stats", texts.BTN_STATS),
            )
        ]
    rows.append(home_button())
    return InlineKeyboardMarkup(inline_keyboard=rows)


class CandidateData(CallbackData, prefix="candidate"):
    candidate_id: int
    approve: bool


class CandidatesPageData(CallbackData, prefix="candidates"):
    after: int


class LeadActionData(CallbackData, prefix="lead"):
    lead_id: int
    action: str
