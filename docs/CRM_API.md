# Локальный CRM API

## Запуск и проверка

В PowerShell из папки проекта

```powershell
$env:CRM_API_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(32))"
python crm_api.py
```

Сервер слушает только http://127.0.0.1:8765. Владелец берётся из recipient.json. При необходимости можно явно задать CRM_OWNER_ID через окружение. Токен не сохраняется в исходники и не выводится сервером.

Для проверки во втором окне PowerShell задай тот же токен в окружении и выполни

```powershell
$headers = @{Authorization = "Bearer $env:CRM_API_TOKEN"}
Invoke-RestMethod http://127.0.0.1:8765/api/leads -Headers $headers
Invoke-RestMethod http://127.0.0.1:8765/api/stats -Headers $headers
```

Для записи нужен уникальный Idempotency-Key. Пример создания тестовой карточки выполняй только при намерении добавить её в рабочую базу

```powershell
$headers["Idempotency-Key"] = [guid]::NewGuid().ToString("N")
$body = @{title = "Демонстрационная карточка"; original_text = "Ручной пример"} | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8765/api/leads -Method Post -Headers $headers -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
```

Один ключ повторно используется только для точного повтора того же запроса. Другой запрос с тем же ключом получает 409. Это защищает заметки, сделки и другие изменения от повторного выполнения при потере HTTP-ответа.

## Маршруты

Этап 3 добавляет GET /api/quality, GET /api/reviews, GET /api/reviews/{raw_id} и POST /api/reviews/{raw_id}/decision. Для решения передаются decision=approved/rejected/needs_review и необязательная reason. Список проверки принимает limit (1–100), offset и status=pending/approved/rejected.

POST /api/reanalysis с необязательным limit (1–1000, по умолчанию 100) возвращает в очередь сообщения без анализа текущей версии. Все изменяющие маршруты требуют Idempotency-Key. Доступ ограничен владельцем.

Карточка лида содержит analyses с намерением, языком, оценкой правил, объяснениями и признаками. score_origin показывает происхождение поля score. POST /api/leads/{id}/feedback также синхронизирует ручную проверку исходных сообщений.

Все маршруты требуют Authorization с Bearer-токеном. POST и PATCH также требуют Idempotency-Key из 16–128 латинских букв, цифр, подчёркиваний или дефисов.

| Метод и маршрут | Действие |
| --- | --- |
| GET /api/statuses | Все 14 статусов |
| GET /api/leads | Список, фильтрация и поиск |
| POST /api/leads | Ручной импорт карточки |
| GET /api/leads/{id} | Полная карточка со связанными данными |
| PATCH /api/leads/{id} | Описание, приоритет, клиент, оценки, деньги и сроки |
| POST /api/leads/{id}/status | Изменить status с историей |
| POST /api/leads/{id}/sources | Добавить первичный источник |
| GET /api/leads/{id}/history | История действий |
| POST /api/leads/{id}/notes | Заметка с полем text |
| POST /api/leads/{id}/conversations | Ручная запись общения |
| POST /api/leads/{id}/drafts | Создать черновик с text |
| PATCH /api/drafts/{id} | Изменить text или status |
| POST /api/drafts/{id}/sent | Зарегистрировать ручную отправку, confirmed=true |
| POST /api/leads/{id}/reminders | Напоминание с text и due_at |
| PATCH /api/reminders/{id} | Изменить text, due_at, status |
| GET /api/reminders | Ожидающие напоминания, необязательный due_before |
| POST /api/leads/{id}/feedback | decision и необязательный reason |
| POST /api/leads/{id}/deals | Создать сделку |
| PATCH /api/deals/{id} | Изменить сделку, указать результат и выручку |
| GET /api/clients | Список клиентов |
| POST /api/clients | Создать клиента или компанию |
| GET /api/clients/{id} | Клиент с проектами |
| PATCH /api/clients/{id} | Имя, контакт или связь с компанией |
| GET /api/leads/{id}/duplicates | Возможные дубли с причинами |
| POST /api/leads/{id}/merge | Объединить с secondary_id |
| POST /api/merges/{id}/split | Отменить конкретное объединение |
| GET /api/export.csv | CSV с фильтрами списка |
| GET /api/export.xlsx | XLSX с фильтрами списка |
| GET /api/stats | Фактическая статистика |
| GET /api/retention | Отчёт хранения, удаление выключено |

## Поля и форматы

Список поддерживает search, status, platform, category, priority, limit, offset, include_merged=0/1. limit по умолчанию 50, максимум 10000. Поиск учитывает кириллицу без учёта регистра, ищет задачу, оригинал, имя и контакт клиента. Фильтры платформы и категории учитывают присоединённые источники и вторичные карточки.

Новый лид принимает title, original_text, platform, source_key, source_name, external_message_id, url, client_id и categories. Импорт с совпавшим внешним ключом возвращает существующую карточку. Метаданные других платформ можно внести вручную, адаптеры других платформ этим не подключаются.

Источнику нужны platform, source_key, source_name. Дополнительно можно передать external_message_id и url. Источник, уже принадлежащий другому лиду, требует ручной проверки объединения.

Денежные поля передаются десятичными строками, например "25000.50". Обязательна currency, например RUB или USD. score допускает целое 0–100 или null. priority принимает low, normal, high, urgent. Сроки хранятся в estimated_timeline.

Клиент принимает name, contact, platform, public_id, kind=person/company, company_id. Публичные ID различаются по платформам. Для изменения привязки клиента к лиду используй PATCH карточки с client_id. Совпадение имени клиента не приводит к объединению.

Воронка использует new, analyzing, verified, promising, ready, draft_prepared, outreach_sent, client_replied, negotiation, proposal_sent, awaiting_decision, won, rejected, archived. Русские подписи возвращаются GET /api/statuses.

Переписка принимает text, direction=incoming/outgoing/system, необязательный occurred_at. Это вручную подтверждённая запись истории, сетевой отправки нет.

Черновик имеет состояния draft, approved, rejected, sent. Отметка sent допустима только отдельным действием после approved. Повтор подтверждения возвращает прежнюю запись общения.

Напоминание имеет состояния pending, completed, cancelled. due_at и occurred_at принимают ISO 8601 с часовым поясом, например 2026-12-01T12:00:00+03:00. Состояние напоминания меняет владелец. Фоновая доставка напоминаний в этом этапе не подключена.

Сделка принимает title, status=open/won/lost, actual_revenue, currency. Фактическая выручка допустима только для won. Статус сделки и стадия лида изменяются явно и независимо.

Решения feedback принимают approved, rejected, needs_review.

## Аналитика

total и new считаются по основным карточкам, объединённые вторичные исключаются из общего числа. Платформа карточки определяется первичным источником, категории считаются по всем участникам объединения.

outgoing_messages считает зарегистрированные исходящие сообщения, replies — вручную зарегистрированные ответы клиентов. В исходное входящее сообщение заказа replies не включается. won_deals считает отдельные заключённые сделки. actual_revenue группируется по валюте и включает только явно введённые суммы won-сделок.

stage_reached учитывает фактически зафиксированное посещение стадии в истории или текущее состояние. stage_conversion делит это число на количество основных карточек. Это не прогноз вероятности покупки. Для пустой базы значения конверсии null.

## Экспорт и ограничения

CSV/XLSX экспортируют до 10000 карточек с основными полями, клиентом, контактом, источником, категориями и отдельными денежными значениями. Для большего объёма уточни фильтры. История общения и вложения в плоский экспорт не входят.

XLSX записывает пользовательский текст как строки, CSV нейтрализует начало формулы апострофом. Недопустимые XML-символы в XLSX заменяются знаком замены только в экспортируемой копии. Текст больше 32767 символов требует CSV.

Возможные дубли проверяются среди последних 1000 основных карточек. Совпадения текста, URL, клиента или компании являются подсказками. Объединение и разделение всегда выполняются вручную.

HTTP-коды включают 200, 400, 401, 404, 409 и 500. Чужие объекты возвращают 404. Публичный доступ и CORS выключены. Перед публикацией нужны отдельные HTTPS, управление сессиями, защита входа и разрешённый frontend.

## Продажи и локальная панель

GET `/api/runtime` — heartbeat с возрастом, состояния источников и очередей (без текстов ошибок и секретов).

POST `/api/leads/{id}/sales`: kind = proposal / questions / followup; поля scope, solution, deliverables, stack, milestones, timeline, price, currency, payment, support, extra_costs, exclusions, next_step, due_at. Всё, кроме kind, необязательно; отсутствующие цены и сроки не выдумываются. Результат draft_id, reminder_id, sent:false. Нужен Bearer и Idempotency-Key.

POST `/api/leads/{id}/pricing`: обязательны hours, hourly_rate, currency; дополнительные expenses, risk_percent, margin_percent, discount_percent, save (boolean, по умолчанию false). Денежные значения и проценты — десятичные строки. Себестоимость = часы × ставка + расходы; минимум = себестоимость × (1 + резерв / 100); рекомендация = минимум / (1 − маржа / 100). Скидка уменьшает рекомендацию. Возвращаются стоимость, минимум, рекомендация, цена после скидки и расчётная прибыль. Это расчёт, не прогноз и не рыночные данные. save:true явно сохраняет цены с историей.

POST `/api/leads/{id}/reply-analysis`: conversation_id вручную зарегистрированного входящего ответа. Возвращает intent, suggested_status, draft_id, probability:null и объяснение. Повторный анализ того же сообщения возвращает прежний результат. Стадия автоматически не меняется.

GET `/api/leads/{id}` дополнительно возвращает активные merges, доступные для ручного разделения существующим endpoint.

### Форма сайта

POST `/public/intake` принимает JSON без административного ключа; допускается только same-origin локального сайта. Обязательны name, contact, service, description (15–5000 символов), consent:true, request_id (16–128 символов). Опциональны company, budget, timeline, website (honeypot, должен быть пустым). Возвращает accepted:true и reference. Лимит пять новых заявок в час, превышение — 429; повтор того же request_id не создаёт дубль. Данные меняются атомарно; изменение содержимого при повторном ID — 409. Контакт, оригинал и бюджетный текст сохраняются без выдуманной числовой интерпретации.

Для панели разрешён Origin `http://127.0.0.1:<порт>`; остальные Origin отклоняются. Статические маршруты сайта и формы доступны без ключа, все `/api/*` требуют Bearer. Не открывай текущий HTTP-сервер в интернет.

## Короткая форма заявки

Обязательные пользовательские поля `/public/intake`: contact и description (15–5000 символов). Бюджет budget необязателен. Служебные request_id, consent:true и пустой website остаются необходимыми. Старые name, service, company, timeline поддерживаются для совместимости, но больше не требуются. Без имени сохраняется клиент с name:null, без направления категории не придумываются. Название «Проект с сайта» — обозначение источника, не результат классификации. Бюджет хранится исходным текстом, не превращается автоматически в подтверждённую сумму.

## Этап 7

- GET `/api/insights?days=30` — качество источников и категорий, текущая версия правил, последние оценки, причины, фактические суммы сделок. Период 1–365 дней.
- GET `/api/suggestions` — предложения, evidence и статус. POST с пустым объектом — пересчёт.
- POST `/api/suggestions/{id}/decision`: decision approved/rejected и необязательный note. Сохраняется история, rules_changed:false. Автоматического применения нет.
- GET `/api/operations` — расписание, последние выполнения, проверенные копии и актуальная диагностика.
- POST `/api/operations/run`: kind backup/health/insights. Возвращает queued:true, не имитирует завершение. Для выполнения нужен работающий мониторинг. Уже работающая задача даёт 409.
- GET `/api/backups/{id}/verify` — SHA-256, целостность и FK-проверка своей зарегистрированной копии; restored:false.

Все маршруты требуют авторизации, владельца и для POST — Idempotency-Key. В ответах обслуживания нет внутренних lease-токенов и секретов. Уведомления заказчикам или служебные Telegram-рассылки эти маршруты не отправляют.
