# alpha-hack-2026

Проверяемая основа backend новостной аналитики: FastAPI → PostgreSQL queue → отдельный worker → сохраняемый Analysis.
Полный недельный продукт продолжает команда по [плану](docs/plan.md); [фактическая передача](docs/handoffs/BOOTSTRAP.md).

Объединённый демонстрационный сценарий с настоящими данными, персональной лентой и явно обозначенным offline LLM: [продуктовая передача G1/UI](docs/handoffs/G1-UI-PRODUCT-FIXES.md). Изолированный стенд — <http://127.0.0.1:18085/>; обычный bootstrap ниже сохраняет отдельный synthetic режим.

Подключённый live adaptive rates и follow-up, учёт фактической сессии и сохранённые ответы: [передача live](docs/handoffs/LLM-RUNTIME-CONFIG.md). Разрешённая сессия закрыта, новые генерации выключены; bootstrap остаётся без платных вызовов.

## Запуск

Нужны Docker с Compose v2+ (на текущей машине проверяется Podman compatibility), make и Python 3 для создания .env.
Для локальных проверок нужны uv 0.13.4 и Python 3.12.14 (`uv sync --frozen`). GPU, API key, ML weights и CSV не нужны.

```bash
cd project-repo
make bootstrap
# после готовности /health/ready:
# http://localhost:8000 — DEMO entry; http://localhost:8000/docs — Swagger
make logs
```

`.env` создаётся один раз с локальным APP_SECRET, существующий не перезаписывается и не печатается.
Если вы самостоятельно скопировали .env.example, заполните APP_SECRET локальным случайным значением длиной ≥32.
Compose: db, migrate, seed, app, worker; db не публикует порт, app привязан к 127.0.0.1.
`make down` сохраняет dev volume. `make up`, `make migrate`, `make seed` поддерживаются; seed повторяем.
`src/Dockerfile` и `.gitkeep` из исходного каркаса сохранены; рабочий Dockerfile находится в корне.

## Сценарий

Войти как alice → выбрать demo_a или demo_b → роль cfo/ceo/unknown → применить контекст → анализ учебного события.
Дождаться worker; открыть читаемую карточку с шестью секциями и ссылками на evidence, notices и temporal warnings. Ввести уточнение.
Bob имеет demo_b и demo_hidden; Alice не видит demo_hidden ни через профиль, ни через evidence/source/job.
Выбор actor — намеренно открытая DEMO-функция, непригодная для production authentication.

HTTP: POST `/api/v1/demo/sessions` с `{"actor":"alice"}`; сохранить HttpOnly cookie и csrf_token.
GET `/api/v1/me/context` сообщает версию. PATCH context требует X-CSRF-Token и expected_version.
POST `/api/v1/analyses` требует тот же CSRF и Idempotency-Key; тело:
`{"event_id":"rate_hold","context_version":1,"as_of":"2026-10-21T00:00:00Z","temporal_mode":"snapshot_demo"}`.
Используйте фактическую context_version. Poll возвращённого poll_url → GET `/api/v1/analyses/{result_id}`.
Evidence: GET `/api/v1/evidence/{id}`; текст источника: GET `/api/v1/sources/{document_version_id}`.
Followup: POST `/api/v1/followups`, analysis_id, его pinned context_version, text; assumptions принадлежат conversation.
Валидируемые тела и ответы: [API_EXAMPLES](API_EXAMPLES.md). Ошибки имеют `{error:{code,message,retryable,request_id,job_id,details}}`.

## Что работает и что остаётся

Настоящие session/ACL/CSRF/context CAS, DTO validation, migration, queue/leases/heartbeat/retry cap,
idempotency, persistence, source ownership, normalizers и atomic budget ledger.
По умолчанию доступен synthetic fixture: rate_hold, fixed_rate, bounded fixture_only;
его model_id=null/usage_status=not_called. Отдельный bounded AnalysisEngine принимает
сохранённые реальные/case события и реальные разрешённые профили; без live opt-in
возвращает evidence_only. Управляемый G1 transport отмечается controlled и не является
ответом настоящей модели. Результаты partial/insufficient_evidence, чтение — saved_replay.
[Интеграция G1: данные, проверки, UI, ограничения и live-команда](docs/handoffs/INTEGRATION.md).
Нет межпользовательского кеша; owned stored results проходят ACL повторно. Общий LLM gateway проверен offline; synthetic и bounded analysis dispatch разделены.
Live требует явного флага, ключа, подтверждённых тарифов и отдельного разрешения на smoke.
[Интерфейс A04 → M04/M07 и команды smoke](docs/handoffs/A04-M07-ML.md).
Материалы и их размещение внутри репозитория: [локальные входные файлы](docs/local-inputs.md).
Case CSV audit/import явный: `make import-case INPUT_DIR="raw/csv"` — dry-run;
`APPLY=--apply` включает атомарную запись в настроенный PostgreSQL без выдачи grants.
Profile/graph tools и pgvector/FTS доступны ML; миграции 0002 (ML storage) / 0003 (gateway), PostgreSQL 17 с vector 0.8.2.
[Передача A02/A05 → ML](docs/handoffs/A02-A05-ML.md) описывает команды и ограничения.
Полноразмерная проверка CSV описана в [remediation](docs/audits/d170154-remediation.md).
OCR/corpus quality, настоящий E5, CBR, adaptive planner, semantic verifier
и реальный bank SSO пока не прошли приёмку. Synthetic UI/worker остаются fixture.
`strict_as_of` исключает features с неизвестной доступностью и future aggregate; новости из будущего исключаются в обоих режимах.
`snapshot_demo` показывает платежи с 1 января 2026, неизвестные end/available_at; это не backtest.
Официальные уточнения и переход к contracts1.1 / case-csv-2: [impact review](docs/official-qa-impact.md).

## Проверки

```bash
uv sync --frozen
make lint typecheck test export-contracts
make test-integration   # отдельный Compose project + tmpfs PostgreSQL, без worker для queue tests
make test-e2e           # HTTP к отдельным app/worker процессам
make check
make audit-inputs INPUT_DIR="raw/csv"
```

Тестовые проекты удаляются через down, dev DB не затрагивается. Test targets сами собирают актуальный `alfa-foundation:local`.
Audit пишет `artifacts/input-audit.json` (ignored); raw CSV/PDF в Git не включать. Ошибка missing-inputs — проверить путь к самой папке CSV.
В Distrobox запускайте команды из каталога репозитория, доступного host Docker wrapper. Если host и контейнер видят разные префиксы пути к этому же репозиторию, настройте `COMPOSE_HOST_PATH_PREFIX` под свою среду.
Не путать HTTP E2E с browser smoke: браузерная проверка отдельно.

## Конфигурация

| Переменная | Назначение |
|---|---|
| DATABASE_URL | asyncpg PostgreSQL DSN; default compose db |
| APP_SECRET | Secret ≥32 символов; HMAC session/CSRF token storage |
| DEMO_MODE | false в коде; true в локальном example; только фиксированные actors |
| LIVE_LLM_ENABLED | false; явное включение общего gateway; fixture runner не переключается |
| LLM_API_KEY | Секрет только для отдельно разрешённого live; общий ledger и gate на ключ |
| LLM_RUNTIME_FILE | config/llm-runtime.json; строгие модели этапов, token caps, deadlines, modes |
| LLM_TARIFF_FILE | config/llm-tariffs.json; 6 ориентировочных тарифов RUB/1 млн input/output, единица/порядок подтверждены владельцем |
| LLM_BASE_URL | Один из двух разрешённых адресов; автоматического переключения нет |
| LLM_BILLABLE_OUTPUT_CAP_CONFIRMED | false; не означает подтверждённый cap. Неподтверждённый маршрут требует отдельного явного разрешения в durable live session |
| LLM_LIVE_SESSION_ID / APP_CODE_SHA | явный общий ID разрешения и SHA app/worker; не создают сессию автоматически |
| APP_ORIGIN | разрешённый browser Origin, default http://localhost:8000 |
| COOKIE_SECURE | false для localhost HTTP; true при будущем HTTPS |
| APP_PORT | host port (при смене согласовать APP_ORIGIN) |
| SESSION_HOURS | срок server session, default 8 |
| LEASE_SECONDS / HEARTBEAT_SECONDS / MAX_ATTEMPTS | 90 / 10 / 2 |

Модели и параметры этапов заданы в [config/llm-runtime.json](config/llm-runtime.json);
[строгая схема](config/llm-runtime.schema.json). Модель DeepSeek сохранена для
plan/draft/verify; после наблюдавшегося исчерпания reasoning caps — 1000/1400/700,
явный `reasoning_effort=none` всех стадий. Это наблюдавшийся рабочий режим, не
документальная гарантия оплачиваемого output cap. Follow-up использует draft/verify без planner.
Transport/call timeout 25 s, run deadline 300 s, включая ограниченное ожидание внешнего spend.
`Settings` читает environment; Compose передаёт `.env`. Отдельного dotenv-loader нет.
Из environment меняются путь файла, base URL и разрешающие флаги, а не модели этапов.
Unknown model/field/unsafe limit → отказ; отсутствующий тариф в live → budget_unknown.
Обязательные safety caps/allowlist остаются в коде.

```sh
make llm-config                           # offline: effective config + максимальный reserve
make llm-config LLM_MODE=two_call_smoke    # draft + verify, без model smoke/HTTP/ключа
```

По текущим ориентировочным тарифам максимум defaults: adaptive **0,305100 ₽**,
two-call smoke **0,204100 ₽**. Это расчёт по тарифной таблице, не оплаченный результат.
Задание получает immutable snapshot/hash при enqueue; worker, gateway и трасса
используют его. Редактирование файла влияет на последующие задания. Неизвестные
legacy pins не восстанавливаются догадкой. Новая миграция `0005` добавляет nullable
snapshot к llm_runs и сохраняет существующие записи, reserves, uncertain и gates.
Миграция `0006` добавляет явные live-сессии и внешние расходы, не сбрасывая ledger.
Сессия ограничена 30 запросами / 50 ₽, запас 5 ₽; резерв каждого запроса
`max(1 ₽, 4 × тарифный envelope)` — консервативная мера, не подтверждённый тариф.
Обычный дневной ledger и key gate действуют одновременно; метаданные spend могут запаздывать.
Закрытая сессия не возобновляется при рестарте или смене суток.
[Фактический live-проход, учёт, открытие/закрытие сессии](docs/handoffs/LLM-RUNTIME-CONFIG.md).

При разных моделях `Analysis.model_id=null`, usage_status остаётся фактическим;
модели этапов видны в trace и каждой ledger-записи. `primary/reserve_model_id`
удалены: fallback в продукте не использовался и сейчас не включён.
[Схема, переключение на GLM, совместимость и точные команды smoke](docs/handoffs/LLM-RUNTIME-CONFIG.md).

Ledger общий, 450/500 RUB, 03:00 Europe/Moscow; uncertain reserve не освобождается автоматически.
Отдельные API ключи, raw prompt и клиентские payload в application logs не выводятся.

A продолжает [import/security/budget](docs/tasks/A-next.md), B — [UI/CBR](docs/tasks/B-next.md), M — [retrieval/engine/eval](docs/tasks/M-next.md).
Общие правила: [AGENTS.md](AGENTS.md), [decisions](docs/decisions.md), [requirements](docs/requirements.md), [источники](docs/sources.md).

## Локальный банковский корпус и retrieval (M01–M03)

Необязательный ML extra: `uv sync --frozen --extra ml`; явная подготовка E5:
`python -m scripts.prepare_e5 --download`. Веса на startup не скачиваются.
Отдельный PDF import: `python -m app.ingestion.pdf --pdf-dir raw/pdf --apply`;
требуются девять исходных банковских PDF и Tesseract rus+eng. Рабочие данные
автоматически не меняются. [Handoff, изолированный probe и результаты](docs/handoffs/M01-M03.md).
Реальные OCR/embeddings/pgvector проверены; полная приёмка gold и ключевых PDF spans
ожидает человеческой разметки. `python -m eval.validate --acceptance` пока возвращает 2.

## Явный сбор внешних новостей (B02)

ЦБ RSS RssPress/eventrss и Росстат RSS → статья → сохранённые live Event/Document/Evidence.
Команда после миграции 0004: `docker compose run --rm worker python -m app.ingestion.news update --source cbr-press --limit 5`.
`make news-update NEWS_ARGS='--source all --limit 5'` доступен при локальной DATABASE_URL;
`make news-status` показывает последнюю успешную загрузку и ошибки. Scheduler отсутствует.
API: `/api/v1/news`, `/api/v1/news/sources`, существующие evidence/sources с проверкой grants.
Коррекция создаёт новую версию, сохраняет предыдущую и помечает зависимые результаты stale.
[Команды, безопасность, передача и фактические smoke-проверки B02](docs/handoffs/B02.md).
ЦБ и Росстат подтверждены реальным импортом в изолированную PostgreSQL и повтором без дублей.
Для Росстата включён отдельный [набор CA Минцифры](config/certs/README.md), без отключения TLS
или изменения системного доверия. Интерфакс и EIA пока только в [плане подключения](docs/plan.md#43-источники-и-обновление).

Объединённый UI/G1: [передача и isolated offline запуск](docs/handoffs/G1-UI-INTEGRATION.md).
Для `make check`/`make test-web` дополнительно нужен Node.js 22+ без npm-зависимостей; runtime API/worker Node не требует.

### Экспериментальная оценка стоимости LLM

В `config/llm-tariffs.json` у каждой модели есть отдельный `cost_estimate.multiplier`:
сейчас **2.23** у всех шести. Для DeepSeek это округлённое наблюдение 26 вызовов
`live-adaptive-g1-20261023`; для остальных — временно перенесённая оценка без замера.
Владелец подтвердил эту модель **оценки** стоимости 23.10.2026. `make llm-config`
показывает nominal tariff и откалиброванную оценку отдельно; резервирование использует
оценку. Подтверждённый usage, исходный номинальный settlement и внешний spend
не умножаются повторно и не подменяются оценкой. Live остаётся выключен.
Подробности: [калибровка](docs/handoffs/LLM-RUNTIME-CONFIG.md).
