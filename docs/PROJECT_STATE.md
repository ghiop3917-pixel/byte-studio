# PROJECT_STATE — Этап 1 единого бота

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

## Ещё не реализовано в рамках каркаса

Рабочие обработчики единого бота и подключение потоков мониторинга, классификация,
рассылка, платёжные обработчики и миграция новых таблиц. main сейчас диагностирует
окружение и выходит; это явно не выдаётся за работающий новый сервис.
Супервизор готов и проверяется Fake-сервисами в одном event loop.

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
```

Совместимые алиасы: TELEGRAM_BOT_TOKEN, TELEGRAM_LEADS_API_ID, TELEGRAM_LEADS_API_HASH, TELEGRAM_LEADS_SESSION.


## Самопроверка и результаты

- check.cmd: 172 теста, 8 ожидаемых пропусков, OK; покрытие всего проекта 84%.
- pytest tests/test_unified_stage1.py -q: 16 passed, 8 subtests passed.
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
- Пункты чек-листа о callback/HTML карточках, Stars, NewMessage, поиске и нормализованном
  кросспостинге пока не реализованы и не выдаются за готовые функции Этапа 1.
- AUTO_JOIN и другие новые настройки относятся к app/, не переписывают поведение
  старых expand_sources.py/source_growth.py. Эти старые утилиты не запускались.

Остановка: Этап 1 завершён как каркас. Продолжать только после слова владельца «дальше».
