# PROJECT_STATE — Этап 2 единого бота

## Местоположение и существующий проект

Только C:/byte-studio. Прежние мониторинг, CRM и сайт сохранены.
Предыдущая работа закончена локальным коммитом 673c82f: четыре источника
вернулись после явного подтверждения владельца. Текущая разработка сохраняется контрольными коммитами.
Рабочая monitor.sqlite3, backups/ и Telegram-сессия не изменяются.

## Создано

app/__init__.py, app/config.py, app/check.py, app/main.py;
app/db/__init__.py, models.py, session.py, repo.py;
app/bot/__init__.py, texts.py;
app/utils/__init__.py, runtime.py, session.py;
Dockerfile, docker-compose.yml, .dockerignore, .env.example;
tests/test_unified_stage1.py, docs/UNIFIED_CONTRACTS.md, docs/PROJECT_STATE.md.
Изменены requirements.txt, requirements-dev.txt, pyproject.toml, .gitignore,
check.cmd, check.sh, README.md. Новые библиотеки изолированы в .venv.

## Контракты

Точные публичные сигнатуры и все поля моделей находятся в UNIFIED_CONTRACTS.md.
Settings и get_settings(env_file=DEFAULT_ENV_FILE); Database(settings, read_only=True,
testing=False); Repository(session, settings); LeadInput; CheckResult;
run_checks(settings=None, online=False); run_services(services, settings, stop);
supervise(name, factory, settings, stop); load_session(path).

## Схема

Один существующий файл monitor.sqlite3. Новые 11 логических сущностей объявлены
в SQLAlchemy с префиксом lh_, но в рабочую SQLite не применены. Таблицы и индексы
создаются только тестом create_test_schema() в SQLite в памяти.
Существующие таблицы CRM/очередей не изменяются и не переименовываются.
Alembic не используется; автоматического bootstrap рабочей базы нет.

## Допущения

- Последнее ТЗ заменяет прежний запрет на новые runtime-зависимости и задаёт Python 3.11+.
- Локальная проверка пока на Python 3.12; целевой Docker — 3.11-slim.
- Префикс lh_ сохраняет старые leads и lead_feedback, не подменяя их новой схемой.
- DRY_RUN и ADMIN_ONLY_DELIVERY включены, AUTO_JOIN/LLM/оплата/удаление лидов выключены.
- Цены тарифов — конфиг-пример, оплата не реализована и деньги не принимаются.
- Существующая сессия читается в MemorySession, без перезаписи или смены авторизации.
- PostgreSQL подготовлен на уровне DSN/фабрики; потребуется optional asyncpg и проверка сервера.

## Текущее состояние интерфейса

Этап 2: app/bot/accounts.py, keyboards.py, screens.py, router.py, runtime.py,
handlers/start.py, handlers/menu.py, handlers/__init__.py и tests/test_unified_stage2.py.
Есть /start/trial, /menu, /cancel, категории, score, гео, подписка без оплаты,
личные реальные счётчики. Dispatcher проверен с Fake Telegram transport.
main без --bot остаётся офлайн-диагностикой. --bot требует полной схемы lh_,
не запускает миграции и откажет до записей, если таблиц/полей нет.
Новая схема в monitor.sqlite3 не применялась; работающий бот этой ветки не запущен.
Осталось: согласованная аддитивная миграция, подключение мониторинга/очередей,
классификатор, персональная доставка, чаты, Stars и сквозная приёмка.
Подробная последовательность: REMAINING.md.

## Переменные .env (полный безопасный пример)

```dotenv
# Безопасный пример. Реальные секреты храните только в .env.
BOT_TOKEN=
TELEGRAM_API_ID=0
TELEGRAM_API_HASH=
# TELEGRAM_SESSION_PATHS не задаётся: сохраняется прежний путь session_path().
# Явный путь: TELEGRAM_SESSION_PATHS=["C:/Users/USER/AppData/Local/TelegramLeads/monitor.session"]
ADMIN_IDS=[]
DATABASE_URL=sqlite+aiosqlite:///./monitor.sqlite3
ALLOW_SCHEMA_CHANGES=false
CHECK_NETWORK=false
DISPLAY_TZ=Europe/Moscow
LOG_LEVEL=INFO

DRY_RUN=true
ADMIN_ONLY_DELIVERY=true
DEFAULT_MIN_SCORE=50
TRIAL_DAYS=3
# Пример тарифов: дни -> Stars. Оплата выключена.
SUBSCRIPTION_PLANS={"7":100,"30":300,"90":800}
PAYMENTS_ENABLED=false

LLM_ENABLED=false
LLM_BASE_URL=http://127.0.0.1:11434/v1
LLM_API_KEY=
LLM_MODEL=
LLM_TIMEOUT_SECONDS=30
LLM_MAX_CONCURRENCY=1
LLM_MAX_ATTEMPTS=3
RETRY_INITIAL_SECONDS=5
RETRY_MAX_SECONDS=300

AUTO_JOIN=false
MAX_CHATS=450
DAILY_JOIN_LIMIT=10
JOIN_PAUSE_MIN_SECONDS=60
JOIN_PAUSE_MAX_SECONDS=120
DISCOVERY_INTERVAL_SECONDS=86400
SOURCE_REFRESH_SECONDS=60
MIN_CHAT_PARTICIPANTS=300
ACTIVITY_SAMPLE_LIMIT=50
ACTIVITY_WINDOW_DAYS=7
MIN_ACTIVE_MESSAGES=10
MIN_UNIQUE_AUTHORS=5

MIN_MESSAGE_LENGTH=30
DEDUP_WINDOW_HOURS=24
DELIVERY_MESSAGES_PER_SECOND=20
SPAM_DISTINCT_USER_THRESHOLD=3
LEAD_RETENTION_DAYS=30
AUTO_DELETE_LEADS=false
TELEGRAM_TEXT_LIMIT=4096
CALLBACK_DATA_MAX_BYTES=64
LEAD_EXCERPT_LENGTH=300
SUMMARY_MAX_LENGTH=120
SUBSCRIPTION_REMINDER_HOURS=24

SQLITE_TIMEOUT_SECONDS=10
SERVICE_RETRY_INITIAL_SECONDS=5
SERVICE_RETRY_MAX_SECONDS=300
SERVICE_MAX_RESTARTS=5

NETWORK_TIMEOUT_SECONDS=30
LLM_HEALTH_MAX_TOKENS=16
# Только для Docker: абсолютный путь к существующей сессии на хосте.
TELEGRAM_SESSION_HOST=

DEFAULT_CATEGORY_SLUG=web_dev
SCORE_CHOICES=[30,50,70,90]
GEO_MAX_LENGTH=100
```

Совместимые алиасы: TELEGRAM_BOT_TOKEN, TELEGRAM_LEADS_API_ID, TELEGRAM_LEADS_API_HASH, TELEGRAM_LEADS_SESSION.


## Самопроверка и результаты

- check.cmd: 183 теста, 8 ожидаемых пропусков, OK; покрытие всего проекта 85%.
- pytest tests/test_unified_stage1.py tests/test_unified_stage2.py -q: 27 passed, 8 subtests passed.
- ruff check .: All checks passed. pip check: No broken requirements found.
- app.check и app.main: офлайн OK; schema/bot/userbot/llm — безопасный SKIP.
- Новые модели UTC и ограничения проверены в SQLite в памяти; транзакционный rollback
  сохраняет атомарность даже после nested savepoint, дубли сообщений/доставок не создаются.
- PostgreSQL DDL компилируется; фабрика проверена FakeEngine без сети.
- Супервизор проверен на восстановление, ограничение повторов и независимость сервисов.
- БД читается асинхронно; синхронная диагностика SQLite/сессии вынесена в asyncio.to_thread.
- Первый вход не выполняется автоматически; новый код не использует client.start().
- Нет пользовательских Telegram-отправок, автоматического вступления, платёжных операций
  или реальных вызовов LLM. Секреты не печатаются. В тестах внешняя сеть подменена.
- create_all не запускается для рабочей SQLite. Alembic и DDL-миграция не применялись.
- Не проверены Docker (CLI не найден), Python 3.11 как интерпретатор, Linux/POSIX,
  реальная авторизация Telegram, внешняя LLM, реальный PostgreSQL.
- Stars, NewMessage, поиск и нормализованный кросспостинг ещё не реализованы.
  Callback и HTML-экранирование экранов реализованы и проверены в Этапе 2.
- AUTO_JOIN и другие новые настройки относятся к app/, не переписывают поведение
  старых expand_sources.py/source_growth.py. Эти старые утилиты не запускались.

Первый каркас завершён; после команды владельца реализован Этап 2. Дальше не переходить без новой команды.


## Снимок Этапа 2 перед GitHub-checkpoint

Полный код сохраняется на GitHub без базы, backups, .env, сессии и .venv.
Настройки score_choices/default_category_slug/geo_max_length добавлены в конфиг.
Все старые контракты сохранены, новые описаны в UNIFIED_CONTRACTS.md.
Тесты интерфейса используют SQLite в памяти и RecordingSession вместо сети.
Реальный polling, Telegram-доставка и миграция не выполнялись. Оплата выключена.
Docker/Linux и реальный PostgreSQL остаются непроверенными.

Итог перед сохранением: 183 unittest, 8 ожидаемых пропусков, 27 pytest, покрытие 85%; ruff OK. Хеши 141 защищённого файла и сессии не изменились. Этап 2 окончен, следующие этапы пока не начинать.
