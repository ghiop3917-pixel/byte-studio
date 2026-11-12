"""Экраны интерфейса; все динамические HTML-значения экранируются."""

from html import escape
from zoneinfo import ZoneInfo

from app.bot import texts
from app.bot.accounts import AccountError, AccountView
from app.config import Settings
from app.db.models import utc_now


def subscription_status(view: AccountView, settings: Settings) -> str:
    until = view.subscription_until
    if until is None or until <= utc_now():
        return texts.SUB_INACTIVE
    date = until.astimezone(ZoneInfo(settings.display_tz)).strftime(texts.SUB_DATE_FORMAT)
    return texts.SUB_ACTIVE.format(date=escape(date))


def screen(page: str, view: AccountView, settings: Settings, *, name: str = "") -> str:
    status = subscription_status(view, settings)
    if page == "welcome":
        return texts.WELCOME.format(name=escape(name), status=status)
    if page == "home":
        return texts.MENU_TITLE.format(status=status, count=sum(item[3] for item in view.categories))
    if page == "categories":
        return texts.CATEGORIES_TITLE
    if page == "settings":
        return texts.SETTINGS_TITLE.format(score=view.min_score, geo=escape(view.geo_filter or texts.GEO_ANY))
    if page == "subscription":
        plans = "\n".join(
            texts.SUB_PLAN_LINE.format(days=days, stars=stars)
            for days, stars in sorted(settings.subscription_plans.items())
        )
        return texts.SUBSCRIPTION_TITLE.format(status=status) + texts.SUB_PLANS.format(plans=plans)
    if page == "about":
        return texts.ABOUT_TITLE
    if page == "stats":
        return texts.STATS_TITLE.format(day=view.received_day, week=view.received_week)
    raise AccountError(texts.OLD_CALLBACK)
