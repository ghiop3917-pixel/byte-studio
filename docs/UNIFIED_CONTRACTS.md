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



# Дополнение контрактов — Этап 2

Контракты Этапа 1 не переименованы. Accounts всегда получает tg_id из Telegram update, не из callback.

## app.bot.accounts

- `AccountError`
- `AccountView(tg_id: int, subscription_until: datetime.datetime | None, min_score: int, geo_filter: str | None, categories: tuple[tuple[int, str, str, bool], ...], received_day: int, received_week: int) -> None`
- `Accounts(database: app.db.session.Database, settings: app.config.Settings) -> None`
  - `Accounts.register(self, tg_id: int, username: str | None) -> app.bot.accounts.AccountView`
  - `Accounts.view(self, tg_id: int) -> app.bot.accounts.AccountView`
  - `Accounts.set_category(self, tg_id: int, category_id: int, enabled: bool) -> app.bot.accounts.AccountView`
  - `Accounts.set_score(self, tg_id: int, score: int) -> app.bot.accounts.AccountView`
  - `Accounts.set_geo(self, tg_id: int, value: str | None) -> app.bot.accounts.AccountView`

## app.bot.keyboards

- `PageData(*, name: str) -> None`
- `CategoryData(*, category_id: int, enabled: bool) -> None`
- `ScoreData(*, value: int) -> None`
- `GeoData(*, action: str) -> None`
- `home_button() -> list[aiogram.types.inline_keyboard_button.InlineKeyboardButton]`
- `keyboard(page: str, view: app.bot.accounts.AccountView, settings: app.config.Settings) -> aiogram.types.inline_keyboard_markup.InlineKeyboardMarkup`

## app.bot.screens

- `subscription_status(view: app.bot.accounts.AccountView, settings: app.config.Settings) -> str`
- `screen(page: str, view: app.bot.accounts.AccountView, settings: app.config.Settings, *, name: str = '') -> str`

## app.bot.router

- `ContextMiddleware(accounts: app.bot.accounts.Accounts) -> None`
- `create_router(database: app.db.session.Database, settings: app.config.Settings) -> aiogram.dispatcher.router.Router`

## app.bot.runtime

- `instance_lock(path: pathlib.Path = WindowsPath('C:/byte-studio/.bot-ui.lock')) -> Iterator[NoneType]`
- `verify_schema(database: app.db.session.Database) -> bool`
- `run_interface(settings: app.config.Settings) -> None`

## app.bot.handlers.start

- `start(message: aiogram.types.message.Message, state: aiogram.fsm.context.FSMContext, accounts: app.bot.accounts.Accounts) -> None`
- `menu(message: aiogram.types.message.Message, state: aiogram.fsm.context.FSMContext, accounts: app.bot.accounts.Accounts) -> None`

## app.bot.handlers.menu

- `GeoInput()`
  - `GeoInput.value(event: aiogram.types.base.TelegramObject, raw_state: str | None = None) -> bool`
- `actor(callback: aiogram.types.callback_query.CallbackQuery) -> int`
- `edit(callback: aiogram.types.callback_query.CallbackQuery, page: str, view: app.bot.accounts.AccountView, accounts: app.bot.accounts.Accounts) -> None`
- `navigate(callback: aiogram.types.callback_query.CallbackQuery, callback_data: app.bot.keyboards.PageData, state: aiogram.fsm.context.FSMContext, accounts: app.bot.accounts.Accounts) -> None`
- `category(callback: aiogram.types.callback_query.CallbackQuery, callback_data: app.bot.keyboards.CategoryData, state: aiogram.fsm.context.FSMContext, accounts: app.bot.accounts.Accounts) -> None`
- `score(callback: aiogram.types.callback_query.CallbackQuery, callback_data: app.bot.keyboards.ScoreData, state: aiogram.fsm.context.FSMContext, accounts: app.bot.accounts.Accounts) -> None`
- `geo(callback: aiogram.types.callback_query.CallbackQuery, callback_data: app.bot.keyboards.GeoData, state: aiogram.fsm.context.FSMContext, accounts: app.bot.accounts.Accounts) -> None`
- `save_geo(message: aiogram.types.message.Message, state: aiogram.fsm.context.FSMContext, accounts: app.bot.accounts.Accounts) -> None`

Callback-префиксы: page(name), category(category_id, enabled), score(value), geo(action). ID пользователя не кодируется.


## Контракты Этапа 3

Старые публичные функции сохранены. Дополнения:

- app.bot.runtime.run_application(settings: Settings) -> None — явный совместный запуск.
- login.login(path: Path, api_id: int, api_hash: str) -> bool — только отсутствующая сессия.
- utils.text.normalize_text(value: str) -> str; text_hash(value: str) -> str.
- utils.links.parse_chat_reference(value: str) -> ChatReference(username, invite_hash).
- utils.links.message_link(chat_id: int, username: str | None, message_id: int,
  thread_id: int | None = None) -> str | None.
- pipeline.prefilter.KeywordRule(slug, word, kind, is_regex=False).
- validate_keyword(word: str, regex: bool, settings: Settings) -> str.
- prefilter(value: str, metadata: dict, rules: tuple[KeywordRule, ...],
  settings: Settings) -> FilterResult(accepted, reason, category_hint).
- pipeline.ingestion.IncomingMessage(chat_id, message_id, text, published_at=None,
  author_tg_id=None, author_username=None, author_name=None, metadata=None).
- SourceInfo(id, chat_id, title, username, access_hash); JobClaim(id, raw_id, token, attempts).
- Ingestion(database, settings): active_sources(), seed_keywords(),
  ingest(source_id, message, *, history=False) -> int | None, recover(now=None) -> int,
  claim() -> JobClaim | None, retry(claim, error), process_one() -> bool,
  requeue(raw_id), heartbeat(key, detail=''), cooldown(kind), set_cooldown(kind, seconds),
  history_state(source_id), source_success(source_id),
  source_error(source_id, error, *, unavailable=False), processing_loop().
- userbot.client.snapshot(source, message) -> IncomingMessage.
- ReaderRPC(storage, settings).call(kind, action); ReaderCooldown обозначает сохранённую паузу.
- TelegramReader(client, storage, settings, rpc=None): refresh(), on_message(event),
  collect_history(source), history_loop().
- ReaderHub(): reader=None; resolve(username), refresh().
- run_reader(database, settings, *, hub=None) -> None.
- AdminSources(database, settings, hub=None): require_admin(tg_id),
  add_chat(tg_id, reference) -> tuple[str, str], remove_chat(tg_id, identity),
  chats(tg_id) -> tuple[dict, ...], keywords(tg_id, slug, *, action='list',
  kind=None, word=None, keyword_id=None, regex=False) -> tuple[dict, ...].

Физические таблицы, только декларации моделей:

- lh_raw_messages: id, source_chat_id FK, chat_id, message_id, source_title,
  original_text, message_link, author_tg_id, author_username, author_name, language,
  published_at, received_at, payload, status, reason, text_hash, category_hint, delivery_mode.
  UNIQUE(chat_id,message_id), индекс (author_tg_id,text_hash), индексы даты/статуса/хеша.
- lh_ingest_jobs: id, raw_id FK UNIQUE, phase, status, attempts, due_at, lease_until,
  token, errors; состояния pending/processing/completed/failed/retry_wait.
- lh_dedup_claims: id, text_hash, author_key, raw_id FK, expires_at;
  UNIQUE(text_hash,author_key). Оригиналы остаются источником проверки окна.
- lh_source_runtime: source_id FK PK, history_message_id, status, attempts,
  due_at, last_success, error.
- lh_reader_state: key PK, updated_at, detail (heartbeat и cooldown).
- lh_keywords: добавлено is_active, удаление только логическое.

Все даты UTC. Данные и DDL рабочей monitor.sqlite3 не менялись.
Предварительно подходящие сообщения ждут phase=classifier, без фиктивного score/summary.


## Контракты Этапа 3б

- app.discovery.writable_group(chat, *, supergroup=False) -> bool.
- can_participate(permissions) -> bool: реальный ParticipantPermissions Telethon.
- Growth(database: Database, settings: Settings, hub: ReaderHub).
- request_discovery(tg_id, slug), schedule_discovery(), discover_one() -> bool.
- save_candidate(chat, slug, participants, messages) -> int; collect_links() -> int.
- decide(actor, candidate_id, approve) -> str; candidates(actor, after=0) -> tuple[dict,...].
- recover_joins() -> int; claim_join() -> JoinClaim | None.
- JoinClaim(id, candidate_id, token, attempts, chat_id, access_hash, username, invite_hash).
- start_attempt(claim, account_chats) -> bool; resolve_claim(claim).
- finish(claim, status, error=None, *, retry=False, ambiguous=False).
- activate(claim, chat, *, actor=None); join_one() -> bool.
- verify_candidate(actor, candidate_id) -> str — только проверка без Join/Import.
- review_sources(), loop(). Один последовательный Growth.loop в текущем процессе.
- CallbackData: CandidateData(candidate_id: int, approve: bool),
  CandidatesPageData(after: int). В callback только типизированные ID, без ссылок/секретов.

Модели (не мигрированы в рабочую SQLite):

- lh_discovery_runs: slug PK, query_index, attempts, status, due_at (индекс), error, updated_at.
- lh_join_jobs: id PK, candidate_id FK UNIQUE, approved_by, status (индекс), attempts,
  due_at (индекс), lease_until, token, error.
  Статусы pending/processing/retry_wait/completed/failed/ambiguous.
- lh_join_attempts: id PK, job_id FK (индекс), token UNIQUE, started_at (индекс), outcome.
- ReaderState ключи: candidate_links (последний raw ID), join_pause (UTC ISO),
  join_stopped (причина/пусто после /join_resume), source_review (JSON), source_alerts,
  growth heartbeat. Существующие cooldown:* переиспользуются.

Публичные контракты Этапов 1–3 сохранены. Проверка runtime теперь также требует
три новые таблицы; это намеренный отказ запуска до согласованной миграции.
Кандидат pending_approval не означает членство. joined означает подтверждённое
членство; source.status=removed сохраняется и не включается обратно автоматически.
