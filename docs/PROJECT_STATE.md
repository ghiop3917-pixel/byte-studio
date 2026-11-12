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


## Этап 3 — приём сообщений, офлайн-приёмка

Реализованы app/userbot/client.py, app/pipeline/ingestion.py и prefilter.py,
utils/text.py и links.py, services_sources.py, bot/handlers/admin.py, login.py.
Один общий обработчик NewMessage, периодическое обновление источников,
сохранение оригиналов до фильтра, восстановление истории по отдельному курсору.
События не продвигают курсор истории: catch-up восстанавливает пропущенные события.
Первый проход читает последние 100 сообщений; более раннюю историю автоматически
не импортирует. Недоступный источник не останавливает обработку остальных.

Новые модели (не применены к рабочей базе): lh_raw_messages, lh_ingest_jobs,
lh_dedup_claims, lh_source_runtime, lh_reader_state; lh_keywords.is_active.
Запись сериализуется в одном Database. Единственный экземпляр app защищён lock;
это не распределённая очередь для нескольких процессов/хостов. Новые таблицы
не заменяют старые CRM и очереди. Проверка схемы запрещает запуск при отсутствии
таблиц/колонок и при активном legacy monitor/source_growth.

Префильтр оставляет кандидата в очереди classifier: финальная запись lh_leads,
оценка и саммари ждут Этапа 4. Авторассылок и автоматического вступления нет.
Автор неизвестен — сообщения разных людей не объединяются по имени.
Кросспосты известного автора проверяются и при истории, пришедшей не по порядку.
Регулярные выражения ограничены: без групп, альтернатив, обратных ссылок,
фигурных скобок и не более одного квантификатора; поиск только в первых
REGEX_TEXT_LIMIT символах. Это предфильтр, не гарантия качества заказа.

Новые настройки (по умолчанию):
```
READER_FLOOD_PADDING_SECONDS=1
READER_REFRESH_SECONDS=60
READER_POLL_SECONDS=1
READER_RPC_INTERVAL_SECONDS=2
READER_SOURCE_INTERVAL_SECONDS=20
READER_INITIAL_HISTORY_LIMIT=100
READER_HISTORY_BATCH_SIZE=100
READER_SOURCE_MAX_ATTEMPTS=5
INGEST_MAX_ATTEMPTS=3
INGEST_LEASE_SECONDS=120
INGEST_POLL_SECONDS=1
KEYWORD_MAX_LENGTH=100
ADMIN_LIST_LIMIT=20
REGEX_TEXT_LIMIT=512
```
DEFAULT_KEYWORDS — JSON-словарь slug → список включающих слов;
DEFAULT_EXCLUDE_KEYWORDS — JSON-массив минус-слов. Полные исходные значения
находятся в Settings.default_keywords/default_exclude_keywords (app/config.py).
Сид добавляет только отсутствующие правила и не включает отключённые.

Команды: --bot сохраняет прежний интерфейс; --run явно запускает бот,
читатель и обработчик в одном event loop. По умолчанию только диагностика.
login.py не открывает и не меняет существующую сессию. Основной читатель загружает
её read-only в MemorySession; интерактивного входа в приложении нет.

check.cmd: 209 тестов, 8 ожидаемых пропусков, 86% покрытия, Ruff без ошибок.
Новые 26 тестов используют FakeClient/поддельный транспорт, SQLite в памяти
и временную тестовую копию для проверки восстановления после перезапуска.
Хеши всех 141 защищённых файлов (monitor.sqlite3 и backups) и Telegram-сессии
сверены с исходным снимком — без изменений.
Реальная сеть, миграция, Docker, Linux/POSIX и Python 3.11 не проверялись.
Предыдущие снимки выше относятся к своим этапам; NewMessage и кросспостинг
теперь реализованы. Этап 3б пока не начинать без новой команды владельца.


## Этап 3б — поиск и очередь подключения

Реализованы app/discovery.py и app/bot/handlers/discovery.py; добавлены модели,
конфиг, callback_data и регистрация четвёртого сервиса growth внутри --run.
Сеть вне транзакций. Один последовательный worker, общий ReaderRPC и cooldown;
дополнительных процессов и зависимостей нет. Публичные контракты Этапов 1–3 сохранены.

Поиск по DISCOVERY_QUERIES (полные значения для семи категорий в app/config.py),
до 50 результатов на запрос. Нужны megagroup, открытая возможность писать,
нет scam/fake, минимум участников и активности. Оценка сообщений/сутки — нижняя
оценка по ограниченной выборке, не статистика всей группы. Неизвестный участник
не считается отдельным известным автором. Ссылки из lh_raw_messages сканируются
пачками; указатель и кандидаты фиксируются вместе, вступления при сборе ссылок нет.

Модели lh_discovery_runs/lh_join_jobs/lh_join_attempts объявлены только в коде.
Рабочая SQLite и её схема не менялись. Не запускать create_all для monitor.sqlite3.
DRY_RUN=true, AUTO_JOIN=false: реальные вступления запрещены по умолчанию.
Кнопка владельца ставит запрос в очередь; AUTO_JOIN=true действует только на
проверенные поиском pending-кандидаты. Отклонённые кандидаты не одобряются повторно
старой кнопкой. Роль владельца повторно проверяется перед действием и активацией.

Состояния pending_approval, needs_captcha, failed и joined различаются.
/candidate_check выполняет только чтение, поэтому можно проверить уже одобренную
Telegram-заявку, вручную пройденную капчу или неоднозначный результат без нового Join.
Тайм-аут после запроса и истёкшая аренда -> ambiguous, автоповтор выключен.
Оплачиваемые инвайты не импортируются. Капча — эвристика, не гарантия обнаружения.
Проверка прав использует реальный ParticipantPermissions установленной Telethon;
несуществующее свойство send_messages не используется.

Учитываются все группы/каналы аккаунта, в том числе архив. Если список диалогов
обрезан DIALOG_SCAN_LIMIT, вступление запрещается. Квота резервируется в БД
перед сетью и консервативно учитывает неоднозначные попытки. Суточная граница UTC.
Пауза и FloodWait сохраняются. Достижение лимита аккаунта останавливает очередь;
/join_resume снимает ручную остановку, но не обходит лимиты. Рекомендации раз в
неделю ничего не удаляют и никого не выводят из чатов; видны через /source_review.
Служебной авторассылки нет, статусы доступны через /candidates и /chats.
Geo — ручной ввод /chat_geo, geo_guess не выдаётся за факт.

Дополнительные настройки:
```
DISCOVERY_SEARCH_LIMIT=50
DISCOVERY_MAX_ATTEMPTS=3
GROWTH_POLL_SECONDS=5
JOIN_MAX_ATTEMPTS=3
JOIN_LEASE_SECONDS=300
DIALOG_SCAN_LIMIT=2000
CANDIDATE_PAGE_SIZE=5
LINK_SCAN_BATCH=100
LINKS_PER_MESSAGE=20
SOURCE_REVIEW_DAYS=30
SOURCE_REVIEW_INTERVAL_DAYS=7
CAPTCHA_WORDS=["captcha","капча","verify","подтвердите","проверку"]
```
DISCOVERY_QUERIES — JSON-словарь slug -> список запросов; до 20 запросов по 100
символов на категорию. Уже существовавшие AUTO_JOIN/MAX_CHATS/DAILY_JOIN_LIMIT/
JOIN_PAUSE_MIN_SECONDS/JOIN_PAUSE_MAX_SECONDS/DISCOVERY_INTERVAL_SECONDS/
MIN_CHAT_PARTICIPANTS/ACTIVITY_SAMPLE_LIMIT/ACTIVITY_WINDOW_DAYS/
MIN_ACTIVE_MESSAGES/MIN_UNIQUE_AUTHORS теперь используются Growth.

Проверка хешей: все 141 защищённых файла (рабочая база и backups), существующая
сессия — SHA-256 без изменений. requirements.txt не менялся.
Сетевые проверки Telegram, реальные вступления, миграции, Linux/Docker и
запуск под Python 3.11 не выполнялись. Следующий этап 4 не начинать без команды.


Приёмка Этапа 3б: check.cmd — 239 тестов, 8 ожидаемых пропусков,
OK; покрытие 86%, app/discovery.py — 86%, Ruff без ошибок.
После последней точечной правки счётчика повторов при MAX_CHATS отдельно
повторён тест daily_limit (1 тест, OK); полный набор после этой правки
повторно не запускался. Изменение не затрагивает другие ветви.
Форматирование новых модулей проверено. Хеши защищённых файлов и сессии
повторно сверены после тестов — без изменений. Реальные Telegram-действия
и миграция не выполнялись. Этап 3б завершён офлайн.
