# КОНТРАКТЫ — единый бот, Этап 1

Контракты нового каркаса. Существующие сервисы CRM и Telegram-ядра не переименованы.
Имена и сигнатуры ниже зафиксированы; изменение требует отдельного описания.
Репозитории используют транзакцию Database.session(), не делают сетевых отправок.

## app.config

- Класс `Settings`
  - `Settings.validate_admin_ids(value: tuple[int, ...]) -> tuple[int, ...]`
  - `Settings.validate_sessions(value: tuple[pathlib.Path, ...]) -> tuple[pathlib.Path, ...]`
  - `Settings.validate_plans(value: dict[int, int]) -> dict[int, int]`
  - `Settings.validate_timezone(value: str) -> str`
  - `Settings.validate_intervals(self) -> 'Settings'`

## app.db.session

- Класс `Database`
  - `Database.__init__(self, settings: app.config.Settings, *, read_only: bool = True, testing: bool = False) -> None`
  - `Database.create_test_schema(self) -> None`
  - `Database.session(self) -> collections.abc.AsyncIterator[sqlalchemy.ext.asyncio.session.AsyncSession]`
  - `Database.close(self) -> None`

## app.db.repo

- Класс `LeadInput`
  - `LeadInput.__init__(self, category_id: int, text: str, summary: str, score: int, source_chat_id: int, message_id: int, text_hash: str, author_tg_id: int | None = None, author_username: str | None = None, message_link: str | None = None, created_at: datetime.datetime | None = None) -> None`
- Класс `Repository`
  - `Repository.__init__(self, session: sqlalchemy.ext.asyncio.session.AsyncSession, settings: app.config.Settings) -> None`
  - `Repository.get_user(self, tg_id: int) -> app.db.models.User | None`
  - `Repository.ensure_user(self, tg_id: int, username: str | None = None, *, is_admin: bool = False) -> app.db.models.User`
  - `Repository.seed_categories(self) -> tuple[app.db.models.Category, ...]`
  - `Repository.upsert_source(self, tg_chat_id: int, title: str, *, username: str | None = None, access_hash: int | None = None, geo: str | None = None) -> app.db.models.SourceChat`
  - `Repository.active_source_ids(self) -> frozenset[int]`
  - `Repository.save_lead(self, data: app.db.repo.LeadInput) -> app.db.models.Lead`
  - `Repository.record_delivery(self, lead_id: int, user_id: int, message_id: int) -> bool`

## app.check

- Класс `CheckResult`
  - `CheckResult.__init__(self, name: 'str', status: 'str', detail: 'str') -> None`
- `inspect_sqlite(path: 'Path') -> 'bool'`
- `run_checks(settings: 'Settings | None' = None, *, online: 'bool' = False) -> 'list[CheckResult]'`
- `main(argv: 'list[str] | None' = None) -> 'int'`

## app.main

- `main(argv: list[str] | None = None) -> int`

## app.utils.runtime

- `supervise(name: str, factory: collections.abc.Callable[[], collections.abc.Awaitable[None]], settings: app.config.Settings, stop: asyncio.locks.Event) -> None`
- `run_services(services: collections.abc.Mapping[str, collections.abc.Callable[[], collections.abc.Awaitable[None]]], settings: app.config.Settings, stop: asyncio.locks.Event) -> None`

## app.utils.session

- `load_session(path: pathlib.Path) -> telethon.sessions.memory.MemorySession`

## app.db.models

- `utc_now() -> datetime.datetime`
- Класс `UTCDateTime`
  - `UTCDateTime.process_bind_param(self, value: datetime.datetime | None, dialect: sqlalchemy.engine.interfaces.Dialect) -> str | None`
  - `UTCDateTime.process_result_value(self, value: str | None, dialect: sqlalchemy.engine.interfaces.Dialect) -> datetime.datetime | None`
- Класс `Base`
  - `Base.__init__(self: 'Any', **kwargs: 'Any') -> 'None'`
- Класс `User`
  - `User.__init__(self, **kwargs)`
- Класс `Category`
  - `Category.__init__(self, **kwargs)`
- Класс `UserCategory`
  - `UserCategory.__init__(self, **kwargs)`
- Класс `SourceChat`
  - `SourceChat.__init__(self, **kwargs)`
- Класс `ChatCandidate`
  - `ChatCandidate.__init__(self, **kwargs)`
- Класс `Lead`
  - `Lead.__init__(self, **kwargs)`
- Класс `LeadDelivery`
  - `LeadDelivery.__init__(self, **kwargs)`
- Класс `LeadFeedback`
  - `LeadFeedback.__init__(self, **kwargs)`
- Класс `AuthorBlacklist`
  - `AuthorBlacklist.__init__(self, **kwargs)`
- Класс `Keyword`
  - `Keyword.__init__(self, **kwargs)`
- Класс `Payment`
  - `Payment.__init__(self, **kwargs)`

## Поля моделей и физические таблицы

Даты UTCDateTime — ISO-8601 UTC в SQLite, aware datetime в Python.
Префикс lh_ обязателен для совместимости с существующей CRM; рабочая схема не применялась.

### lh_author_blacklist

| Поле | Тип хранения | NULL | PK |
|---|---|---|---|
| author_tg_id | BIGINT | False | True |
| reason | TEXT | False | False |
| created_at | VARCHAR(40) | False | False |

### lh_categories

| Поле | Тип хранения | NULL | PK |
|---|---|---|---|
| id | INTEGER | False | True |
| slug | VARCHAR(64) | False | False |
| title | VARCHAR(100) | False | False |
| is_active | BOOLEAN | False | False |

### lh_chat_candidates

| Поле | Тип хранения | NULL | PK |
|---|---|---|---|
| id | INTEGER | False | True |
| tg_chat_id | BIGINT | True | False |
| access_hash | BIGINT | True | False |
| username | VARCHAR(64) | True | False |
| invite_hash | VARCHAR(255) | True | False |
| title | VARCHAR(255) | False | False |
| participants | INTEGER | True | False |
| msgs_per_day | NUMERIC(12, 2) | True | False |
| category_hint | VARCHAR(64) | True | False |
| status | VARCHAR(32) | False | False |
| error | TEXT | True | False |
| found_at | VARCHAR(40) | False | False |

### lh_keywords

| Поле | Тип хранения | NULL | PK |
|---|---|---|---|
| id | INTEGER | False | True |
| category_id | INTEGER | False | False |
| word | TEXT | False | False |
| type | VARCHAR(16) | False | False |
| is_regex | BOOLEAN | False | False |

### lh_lead_deliveries

| Поле | Тип хранения | NULL | PK |
|---|---|---|---|
| id | INTEGER | False | True |
| lead_id | INTEGER | False | False |
| user_id | INTEGER | False | False |
| sent_at | VARCHAR(40) | False | False |
| message_id | INTEGER | False | False |

### lh_lead_feedback

| Поле | Тип хранения | NULL | PK |
|---|---|---|---|
| id | INTEGER | False | True |
| lead_id | INTEGER | False | False |
| user_id | INTEGER | False | False |
| type | VARCHAR(16) | False | False |
| created_at | VARCHAR(40) | False | False |

### lh_leads

| Поле | Тип хранения | NULL | PK |
|---|---|---|---|
| id | INTEGER | False | True |
| category_id | INTEGER | False | False |
| text | TEXT | False | False |
| summary | VARCHAR(120) | False | False |
| score | INTEGER | False | False |
| author_tg_id | BIGINT | True | False |
| author_username | VARCHAR(64) | True | False |
| source_chat_id | INTEGER | False | False |
| message_id | INTEGER | False | False |
| message_link | TEXT | True | False |
| text_hash | VARCHAR(64) | False | False |
| created_at | VARCHAR(40) | False | False |

### lh_payments

| Поле | Тип хранения | NULL | PK |
|---|---|---|---|
| id | INTEGER | False | True |
| user_id | INTEGER | False | False |
| amount | NUMERIC(18, 2) | False | False |
| currency | VARCHAR(8) | False | False |
| provider | VARCHAR(32) | False | False |
| status | VARCHAR(32) | False | False |
| telegram_payment_charge_id | VARCHAR(255) | True | False |
| payload | TEXT | False | False |
| created_at | VARCHAR(40) | False | False |

### lh_source_chats

| Поле | Тип хранения | NULL | PK |
|---|---|---|---|
| id | INTEGER | False | True |
| tg_chat_id | BIGINT | False | False |
| access_hash | BIGINT | True | False |
| username | VARCHAR(64) | True | False |
| title | VARCHAR(255) | False | False |
| geo | VARCHAR(100) | True | False |
| geo_guess | VARCHAR(100) | True | False |
| is_active | BOOLEAN | False | False |
| status | VARCHAR(32) | False | False |
| added_at | VARCHAR(40) | False | False |

### lh_user_categories

| Поле | Тип хранения | NULL | PK |
|---|---|---|---|
| user_id | INTEGER | False | True |
| category_id | INTEGER | False | True |

### lh_users

| Поле | Тип хранения | NULL | PK |
|---|---|---|---|
| id | INTEGER | False | True |
| tg_id | BIGINT | False | False |
| username | VARCHAR(64) | True | False |
| created_at | VARCHAR(40) | False | False |
| subscription_until | VARCHAR(40) | True | False |
| min_score | INTEGER | False | False |
| geo_filter | VARCHAR(100) | True | False |
| is_active | BOOLEAN | False | False |
| is_banned | BOOLEAN | False | False |
| is_admin | BOOLEAN | False | False |

