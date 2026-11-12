"""Настройки нового приложения. Загрузка не открывает базу и сеть."""

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from session_config import session_path

DEFAULT_MIN_SCORE = 50
INITIAL_CATEGORIES = (
    ("web_dev", "Веб-разработка"),
    ("web_design", "Веб-дизайн"),
    ("ai_orders", "Заказы по AI"),
    ("tech_specialists", "Тех. специалисты"),
    ("jobs", "Поиск вакансий"),
    ("freelance_jobs", "Фриланс вакансии"),
    ("seo", "SEO"),
)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
DEFAULT_DATABASE_URL = f"sqlite+aiosqlite:///{(PROJECT_ROOT / 'monitor.sqlite3').as_posix()}"


class Settings(BaseSettings):
    """Секреты маскируются, потенциально платные и изменяющие действия выключены."""

    model_config = SettingsConfigDict(
        env_file=DEFAULT_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    bot_token: SecretStr = Field(default="", validation_alias=AliasChoices("BOT_TOKEN", "TELEGRAM_BOT_TOKEN"))
    telegram_api_id: int = Field(
        default=0, ge=0, validation_alias=AliasChoices("TELEGRAM_API_ID", "TELEGRAM_LEADS_API_ID")
    )
    telegram_api_hash: SecretStr = Field(
        default="", validation_alias=AliasChoices("TELEGRAM_API_HASH", "TELEGRAM_LEADS_API_HASH")
    )
    telegram_session_paths: tuple[Path, ...] = Field(default_factory=lambda: (session_path(),))
    admin_ids: tuple[int, ...] = ()
    database_url: str = DEFAULT_DATABASE_URL
    allow_schema_changes: bool = False
    check_network: bool = False
    network_timeout_seconds: float = Field(default=30, gt=0)
    llm_health_max_tokens: int = Field(default=16, ge=1)
    display_tz: str = "Europe/Moscow"
    log_level: str = "INFO"

    sqlite_timeout_seconds: float = Field(default=10, gt=0)
    service_retry_initial_seconds: float = Field(default=5, gt=0)
    service_retry_max_seconds: float = Field(default=300, gt=0)
    service_max_restarts: int = Field(default=5, ge=1)
    default_category_slug: str = "web_dev"
    score_choices: tuple[int, ...] = (30, 50, 70, 90)
    geo_max_length: int = Field(default=100, ge=1, le=100)
    dry_run: bool = True
    admin_only_delivery: bool = True
    default_min_score: int = Field(default=DEFAULT_MIN_SCORE, ge=0, le=100)
    trial_days: int = Field(default=3, ge=0)
    subscription_plans: dict[int, int] = Field(default_factory=lambda: {7: 100, 30: 300, 90: 800})
    payments_enabled: bool = False

    llm_enabled: bool = False
    llm_base_url: str = "http://127.0.0.1:11434/v1"
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""
    llm_timeout_seconds: float = Field(default=30, gt=0)
    llm_max_concurrency: int = Field(default=1, ge=1)
    llm_max_attempts: int = Field(default=3, ge=1)
    retry_initial_seconds: float = Field(default=5, gt=0)
    retry_max_seconds: float = Field(default=300, gt=0)

    llm_min_interval_seconds: float = Field(default=1, ge=0)
    llm_max_response_bytes: int = Field(default=65536, ge=1024, le=1048576)
    llm_max_input_chars: int = Field(default=16000, ge=100, le=100000)
    llm_classify_max_tokens: int = Field(default=256, ge=32, le=2048)
    llm_draft_max_tokens: int = Field(default=512, ge=32, le=2048)
    classifier_poll_seconds: float = Field(default=1, gt=0)
    classifier_lease_seconds: float = Field(default=120, gt=0)
    delivery_poll_seconds: float = Field(default=1, gt=0)
    delivery_lease_seconds: float = Field(default=120, gt=0)
    delivery_max_attempts: int = Field(default=5, ge=1)
    delivery_batch_size: int = Field(default=100, ge=1, le=1000)
    draft_max_length: int = Field(default=1200, ge=100, le=3000)
    draft_lease_seconds: float = Field(default=120, gt=0)

    reader_flood_padding_seconds: float = Field(default=1, ge=0)
    reader_refresh_seconds: float = Field(default=60, gt=0)
    reader_poll_seconds: float = Field(default=1, gt=0)
    reader_rpc_interval_seconds: float = Field(default=2, ge=0)
    reader_source_interval_seconds: float = Field(default=20, gt=0)
    reader_initial_history_limit: int = Field(default=100, ge=1, le=100)
    reader_history_batch_size: int = Field(default=100, ge=1, le=100)
    reader_source_max_attempts: int = Field(default=5, ge=1)
    ingest_max_attempts: int = Field(default=3, ge=1)
    ingest_lease_seconds: float = Field(default=120, gt=0)
    ingest_poll_seconds: float = Field(default=1, gt=0)
    keyword_max_length: int = Field(default=100, ge=1, le=100)
    admin_list_limit: int = Field(default=20, ge=1, le=20)
    regex_text_limit: int = Field(default=512, ge=1, le=4096)
    default_keywords: dict[str, tuple[str, ...]] = Field(
        default_factory=lambda: {
            "web_dev": ("сайт", "лендинг", "бот", "crm", "автоматизац", "website", "telegram bot", "app"),
            "web_design": ("дизайн", "figma", "designer"),
            "ai_orders": ("нейросет", "искусственный интеллект", "ai specialist"),
            "tech_specialists": ("разработчик", "программист", "developer"),
            "jobs": ("вакансия", "vacancy", "hiring"),
            "freelance_jobs": ("фриланс", "freelance"),
            "seo": ("seo", "сео", "продвижение сайта"),
        }
    )
    default_exclude_keywords: tuple[str, ...] = ("ищете клиентов", "предлагаю услуги", "уже нашли исполнителя")
    auto_join: bool = False
    max_chats: int = Field(default=450, ge=1)
    daily_join_limit: int = Field(default=10, ge=0)
    join_pause_min_seconds: int = Field(default=60, ge=1)
    join_pause_max_seconds: int = Field(default=120, ge=1)
    discovery_interval_seconds: int = Field(default=86400, ge=1)
    source_refresh_seconds: int = Field(default=60, ge=1)
    min_chat_participants: int = Field(default=300, ge=0)
    activity_sample_limit: int = Field(default=50, ge=1)
    activity_window_days: int = Field(default=7, ge=1)
    min_active_messages: int = Field(default=10, ge=1)
    min_unique_authors: int = Field(default=5, ge=1)

    discovery_queries: dict[str, tuple[str, ...]] = Field(
        default_factory=lambda: {
            "web_dev": ("заказы разработка", "фриланс сайты", "ищу программиста", "вакансии it"),
            "web_design": ("заказы дизайн", "дизайн фриланс"),
            "ai_orders": ("нейросети заказы", "автоматизация бизнес"),
            "tech_specialists": ("вакансии разработчик", "it специалисты"),
            "jobs": ("вакансии работа", "ищу сотрудника"),
            "freelance_jobs": ("фриланс заказы", "удаленная работа"),
            "seo": ("seo заказы", "продвижение сайтов"),
        }
    )
    discovery_search_limit: int = Field(default=50, ge=1, le=50)
    discovery_max_attempts: int = Field(default=3, ge=1, le=10)
    growth_poll_seconds: float = Field(default=5, gt=0)
    join_max_attempts: int = Field(default=3, ge=1, le=10)
    join_lease_seconds: float = Field(default=300, gt=0)
    dialog_scan_limit: int = Field(default=2000, ge=1)
    candidate_page_size: int = Field(default=5, ge=1, le=5)
    link_scan_batch: int = Field(default=100, ge=1, le=1000)
    links_per_message: int = Field(default=20, ge=1, le=100)
    source_review_days: int = Field(default=30, ge=1)
    source_review_interval_days: int = Field(default=7, ge=1)
    captcha_words: tuple[str, ...] = ("captcha", "капча", "verify", "подтвердите", "проверку")

    min_message_length: int = Field(default=30, ge=1)
    dedup_window_hours: int = Field(default=24, ge=1)
    delivery_messages_per_second: int = Field(default=20, ge=1, le=20)
    spam_distinct_user_threshold: int = Field(default=3, ge=1)
    lead_retention_days: int = Field(default=30, ge=1)
    auto_delete_leads: bool = False
    telegram_text_limit: int = Field(default=4096, ge=1, le=4096)
    callback_data_max_bytes: int = Field(default=64, ge=1, le=64)
    lead_excerpt_length: int = Field(default=300, ge=1)
    summary_max_length: int = Field(default=120, ge=1)
    subscription_reminder_hours: int = Field(default=24, ge=1)

    @field_validator("discovery_queries")
    @classmethod
    def validate_discovery_queries(cls, value: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
        known = {slug for slug, _ in INITIAL_CATEGORIES}
        if any(
            slug not in known or len(queries) > 20 or any(not q.strip() or len(q) > 100 for q in queries)
            for slug, queries in value.items()
        ):
            raise ValueError("Поисковые запросы: известная категория, до 20 непустых запросов длиной до 100 символов.")
        return {slug: tuple(dict.fromkeys(q.strip() for q in queries)) for slug, queries in value.items()}

    @field_validator("score_choices")
    @classmethod
    def validate_score_choices(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value or any(score < 0 or score > 100 for score in value):
            raise ValueError("Пороги score должны быть в диапазоне 0–100.")
        return tuple(dict.fromkeys(value))

    @field_validator("default_category_slug")
    @classmethod
    def validate_default_category(cls, value: str) -> str:
        if value not in {slug for slug, _ in INITIAL_CATEGORIES}:
            raise ValueError("Неизвестная категория по умолчанию.")
        return value

    @field_validator("admin_ids")
    @classmethod
    def validate_admin_ids(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(item <= 0 for item in value):
            raise ValueError("ADMIN_IDS должен содержать положительные Telegram ID.")
        return tuple(dict.fromkeys(value))

    @field_validator("telegram_session_paths")
    @classmethod
    def validate_sessions(cls, value: tuple[Path, ...]) -> tuple[Path, ...]:
        if not value:
            raise ValueError("Нужен хотя бы один путь Telegram-сессии.")
        return tuple(path.expanduser().resolve() for path in value)

    @field_validator("subscription_plans")
    @classmethod
    def validate_plans(cls, value: dict[int, int]) -> dict[int, int]:
        if any(days <= 0 or stars <= 0 for days, stars in value.items()):
            raise ValueError("Дни и стоимость тарифов в Stars должны быть положительными.")
        return value

    @field_validator("display_tz")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError("Неизвестный DISPLAY_TZ или отсутствует системная база часовых поясов.") from None
        return value

    @model_validator(mode="after")
    def validate_intervals(self) -> "Settings":
        if self.join_pause_min_seconds > self.join_pause_max_seconds:
            raise ValueError("Минимальная пауза вступлений больше максимальной.")
        if self.retry_initial_seconds > self.retry_max_seconds:
            raise ValueError("Начальная пауза повторов больше максимальной.")
        if self.service_retry_initial_seconds > self.service_retry_max_seconds:
            raise ValueError("Начальная пауза перезапуска сервисов больше максимальной.")
        return self


@lru_cache(maxsize=1)
def get_settings(env_file: str | Path | None = DEFAULT_ENV_FILE) -> Settings:
    """Загружает настройки; None запрещает чтение .env для офлайн-тестов."""
    return Settings(_env_file=env_file)
