# Независимая техническая приёмка и интеграция M4

> Актуальная конфигурация этапов, тарифы и политика G1 v2: [LLM-RUNTIME-CONFIG](LLM-RUNTIME-CONFIG.md).
> Значения и проверки ниже относятся к исторической версии этого handoff.

22.10.2026. Ветка `task/M04-agent-loop`, собственный worktree
`project-repo-m4-acceptance`. Платных вызовов **0**, пользовательский ключ не читался.

## Состав версии

| Компонент | Фактический SHA / состояние |
|---|---|
| Main и origin/main на старте | `eabe2e2f6229ada0c551c8f253110492468a1123` |
| Продуктовая ветка task/g1-ui-product-fixes | `adba6e56a4c87acbfbecd1741284db7de992442a` |
| Продуктовые исправления a1a67b1 | Уже предок main; продукт слит через PR #8 / `007a9c8` |
| PR #9, origin/task/M04-agent-loop | `e47316bcedb72819c9a2648700514ad8af915301` |
| Родитель PR head и merge-base с main | `b5b4cce1cb2da7f010a33d56dde1a8f7343d0718` |
| Итоговый проверенный code HEAD | **`25ad163054fda4f741addf5e0bd71409b0494878`** |
| Родители интеграционного merge | main `eabe2e2` и исходный PR `e47316b` |

Последующий коммит добавляет только эту передачу и свидетельства. SHA всей поставки:
`git rev-parse task/M04-agent-loop`; код в нём совпадает с code HEAD выше.
PR найден по `refs/pull/9/head`, SHA совпадает с удалённой веткой. Метаданные GitHub
baseRef недоступны без авторизации (API 404), поэтому имя исходного baseRef не
выдаётся за установленный факт. Родитель коммита, merge-base и целевой main известны.

Main и исходные worktree не переключались и не изменялись. У старых worktree есть
prunable-регистрации после архивирования; repair/prune/reset/clean/stash не выполнялись.
Незакоммиченные чужие отчёты не включены. Собственная ветка создана от main, затем
выполнен обычный merge PR с сохранением истории. Add/add-конфликт engine разрешён
по смыслу: продуктовый контроллер сохранён и расширен проверенным планированием.
Новая версия не меняет UI, миграции, DTO 1.1, корпус, pgvector-хранилище или зависимости.

Прочитаны AGENTS, актуальный план (особенно §§6–7), оригинальный кейс и инструкция
ключей, официальные Q&A, передачи INTEGRATION / G1-UI-PRODUCT-FIXES / M04 / B02 /
A04-M07-ML и независимый `G1-UI-PRODUCT-adba6e5-gemini.md`. Последний положительный
аудит **не заменяет проверку кода**: упоминаемые там счета/остатки/названия компаний,
GPT-модели и подтверждённые тарифы не соответствуют этому дереву. Исторический
текст не исправлен задним числом. Полный человеческий gold — задача команды,
а не ожидаемый вход организаторов; он не блокирует техническую интеграцию.

## Что означает M04 и что принято

По плану M04 — собственный bounded controller: план → read-only tools → draft →
детерминированные проверки → semantic verify → допустимый bounded repair либо
удаление неподтверждённого → сохранение; follow-up с закреплёнными источниками.
Это не новый embeddings/index stage и не замена gateway.

| Требование | Реализация и потребители | Проверка | Пробел / граница приёмки |
|---|---|---|---|
| План влияет на реальные действия | DefaultAnalysisEngine в jobs/analysis; строгий AnalysisPlan выбирает первый/уточнённый запрос и фокус недостающих полей | Новый unit: empty → refinement; подходящий кандидат → stop; worker trace содержит adaptive_plan и реальные plan/draft/verify IDs | Только rates и узкий fuel_costs; не универсальный agent runtime |
| Scope/инструменты/достаточность | Контроллер задаёт компанию, канал, cutoff, права, набор инструментов и лимиты. Модель не расширяет их | Чужой канал/поле/аргумент инструмента отклоняется; future/unknown/conflict/missing feature останавливают генерацию | Произвольный выбор get_relations не принят; relation_ids в DTO не добавлены без доказанного потребителя |
| Доказательства до LLM | DBEventProvider наследует авторитетный Events; источники, документ, span, доступность проверяются до передачи в prompt | 6 новых PostgreSQL ACL/time/span случаев, прежние retrieval regressions | Unknown available_at допускается только маркированным snapshot, не строгим backtest |
| Read-only retrieval | Существующий HybridRetriever/BankSearch; до 2 поисков, повтор зависит от результата первого; перезагрузка выбранных ID через защищённый адаптер | Настоящий корпус, E5, 2 различные unit-трассы, реальные worker scenarios | OCR остаётся unverified; refinement не доказывает качественный выбор модели |
| Draft/semantic verify | Существующие продуктовые Draft/Verification; только условные гипотезы, без численного эффекта и выдачи от имени банка | Negative/missing/duplicate verdict, чужие refs, числа, гарантии удаляются | Semantic verify — вспомогательная модельная проверка; live entailment не проверен |
| Budget/ошибки | Настоящий job_id/run_id, единый gateway; обычный plan/draft/verify до 3; stop без repair/retry/fallback | PostgreSQL budget/concurrency/deadline/cancellation/uncertain; отдельный worker 2-call policy | Расширенный repair из PR исключён как небезопасный; это разрешённый bounded stop |
| Follow-up | Новый bounded run через тот же gateway, актуальный вопрос и явные user_declared; pinned parent evidence/context/as_of/snapshot/bank_context | Arbitrary question не превращается в «ставка фиксирована до 2027»; source facts/sections неизменны; смена контекста/ACL запрещает доступ | Пользовательское условие не становится фактом договора |
| Сохранение/API/UI | Старый контракт и маршруты, реальные jobs/results/evidence/source PDF, существующие UI consumers | 2 последовательных набора сценариев API + отдельный worker; 13 DOM/controller tests | Браузер этой версии не проверен: origin gate отказал в доступе |
| Полная quality acceptance | Технические трассы, реальные входы и проверенный пакет ручной сверки сохранены | 24 строки решений пакета остаются пустыми | Полные G2/G3, независимый gold, качество модели и применимые банковские тезисы не приняты |

## Подтверждённые дефекты исходного PR

На **самом PR head** независимо выполнены unit/contracts: 189 passed; Ruff — 10
ошибок. Зелёные unit не ловили интеграционные дефекты. Воспроизводимый probe и его
вывод: [pr-probe.txt](../reviews/M4-acceptance/pr-probe.txt),
[pr-reproduction.json](../reviews/M4-acceptance/pr-reproduction.json). Probe запускать
на checkout исходного PR, с его тестовыми mocks, без ключа и сети; это отрицательное
свидетельство, не скрипт запуска принятого продукта.

| ID | Воспроизведение / причина | Исправление / статус | Свидетельство |
|---|---|---|---|
| M4-01 | Новая функция отдельно от продукта, случайный job/run ID вместо настоящего задания; mocks не проверяют FK/ledger | Принята идея собственного planner, заменена orchestration: реальный DefaultAnalysisEngine вызывается отдельным worker, call IDs привязаны к job | G1 trace и PostgreSQL model_calls; 3 stage IDs, повтор задания идемпотентен |
| M4-02 | Raw DBEventProvider читал evidence без авторитетной document/span/ownership проверки; не проверял pinned context | Заменён защищённым адаптером продукта + get_evidence_at для всей зависимости source/document/evidence | Новые 6 PostgreSQL тестов; current ACL повторяется перед каждой модельной границей |
| M4-03 | Любые features считались достаточными, channels/required_fields/relations плана фактически игнорировались | Known fields/channel schema и общая продуктовая match_event; план используется и не расширяет scope | Schema/foreign channel unit, missing exposure/future/conflict real scenarios |
| M4-04 | «Договор гарантирует рост прибыли» с foreign feature сохранён conditional после unsupported и пустого repair | Неподтверждённые выводы удаляются; numbers/чужие refs/имитация банка блокируются; ремонт не вызывается | Probe сохраняет плохой claim на PR; новые negative verifier unit удаляют его без repair |
| M4-05 | analyze_followup на вопрос «Какова инфляция?» всегда выдаёт фиксацию ставки до конца 2027, без LLM, с delivery=live | Статическая подмена исключена; только реальный вопрос, gateway draft/verify, explicit assumptions и pinned parent | Arbitrary-question unit + real API follow-up/reopen/ACL |
| M4-06 | delivery=live/provider даже при нуле реальных запросов, банковские theses не привязаны к авторитетным evidence | Сохранена честная продуктовая delivery/usage, банк перезагружается и диагностируется; bank_view не повышается по OCR | Probe zero_real_calls_delivery=live; итоговые analyses partial/background_only |
| M4-07 | Грубая оценка input не включала полную JSON-схему, размытые счётчики/repair могли выйти за бюджет | Input byte envelope включает schema/framing; локальные и durable run limits; no repair/retry | Gateway limits и реальные двухвызовные worker проверки |
| M4-08 | PR помечал выдуманные тарифы verified, сославшись на PDF без тарифов | Изменение config/llm-tariffs.json исключено; файл остаётся пустым, как main | Прочитана исходная инструкция; unknown tariff останавливает до сети |
| M4-09 | Новый второй разрешённый URL нельзя было выбрать штатно; reasoning usage игнорировался | Явный allowlist URL, scope зависит только от ключа; canonical reasoning subset учтён однократно, неоднозначный positive top-level count → uncertain | HTTP fixtures обоих адресов, PostgreSQL settlement и общий gate после смены адреса |

Полезные решения PR сохранены: строгий тип плана, отдельные planner/schema/guardrail
модули, адаптация локального поиска, UTF-8 в eval.validate и регрессия gold gate.
Широкие PR Draft/Repair/Follow-up и неподключённый controller заменены по причинам
выше. Устаревшие предложения менять уже работающие API/worker не применены.
Исходные передачи M04 сохранены с явной пометкой «исторический PR».

## Фактические проверки объединённого кода

Логи и хеши: [measurements.json](../reviews/M4-acceptance/measurements.json),
[check.log](../reviews/M4-acceptance/check.log). Это отдельные пересекающиеся
прогоны, их количества **не складываются**. Для читаемости у приложенных логов
убраны ANSI/концевые пробелы и нормализованы переводы строк; хеши исходных
локальных и нормализованных файлов записаны отдельно в measurements.

- `make check`: Ruff pass; mypy 21 files pass; **240 unit/contracts**, **13 Node UI**,
  validation 5 DTO UI states, экспорт контрактов без drift, **75 PostgreSQL integration**,
  **2 HTTP E2E**. Изолированные Compose DB; рабочий ledger не использовался.
- Unit/contracts в controlled-окружении с read-only worker filesystem: **240 pass**.
- `make test-agent`: **34 pass**, подмножество unit, а не дополнительные 34 теста.
- G1 + product scenarios на одном стенде дважды: **7 pass / 16.54 s** и
  **7 pass / 10.35 s**. Между тестами восстанавливается только baseline помеченной
  disposable DB, с учётом commit отдельных соединений worker. Uncertain при этом
  не удаляется. В полном наборе: profile.read, персонализация 2 реальных компаний,
  источники/PDF, follow-up, reread, смена контекста, ACL, отсутствие bank proof,
  strict unknown availability, missing/conflict, новый rates и fuel.
- Отдельный **test-only** policy_worker, реальный API/PostgreSQL/ledger и controlled
  HTTP: success **2 calls**, invalid JSON **1**, timeout **1**. Каждый режим: 1 pass.
  Повтор idempotency key возвращает то же задание. Timeout оставил uncertain,
  reserve и gate в **этой изолированной БД**. Они не очищены.
- Импорт **9 настоящих PDF**, **128 evidence / 128 E5 vectors**; повтор:
  **0 imported / 9 unchanged**. OCR взят из закреплённого реального cache,
  повторного OCR нет. Реальный локальный `intfloat/multilingual-e5-small`, revision
  `614241f622f53c4eeff9890bdc4f31cfecc418b3`, dimension **384**; assets проверяются
  models.lock. Corpus/chunk/model version IDs и индекс сохранены; startup downloads нет.
  Новый benchmark FTS/exact/HNSW не заявляется: store/index implementation не менялась.
- B02 CBR/Росстат загружены из hash-pinned настоящих сохранённых RSS/HTML;
  **нового network smoke новостей в этой задаче нет**. Product probe сохранил G1 CFO,
  G1 CEO, unseen rates и Rosstat fuel через API/worker. Все **partial**, 3 controlled
  model hypotheses, **bank_view=0**, diagnosis quality_limited; это честное ограничение.
  Сохранённые ID/версии и различающиеся оценки компаний — в measurements.
- Пакет ручной сверки не изменён: ZIP SHA256
  `70ee9e85b05080c02200ef7a0dc7a6ffd57a340af60a036b0cb1ba18d6591794`;
  verify_packet pass, **24 незаполненных решения**, старый frozen source `3900cf1`.
  Этот пакет не сертифицирует новые модельные ответы; human_verified не выставлялся.

Browser smoke предпринят на `http://127.0.0.1:18086`, но автоматический origin
approval gate отказал в доступе («user declined permission»). Обход и другая
браузерная поверхность не использовались; **скриншотов и браузерного pass нет**.
UI/templates/CSS/JS байт-в-байт совпадают с main, поэтому полного нового визуального
аудита не требуется, но реальный браузерный путь после M4 остаётся непроверенным.
13 DOM tests и HTTP сценарии не называются браузерной приёмкой.

## Запуск и миграции

Один Alembic head: **0004**, новых миграций/зависимостей нет. Изолированный стенд:

```sh
docker compose --env-file config/integration-m4.env \
  -f docker-compose.integration.yml -p alfa-m4-acceptance build app
docker compose --env-file config/integration-m4.env \
  -f docker-compose.integration.yml -p alfa-m4-acceptance up -d app worker
make check
make test-agent
```

Тестовые входы и pinned weights предоставляются локально по существующим manifest,
не скачиваются startup. На свежем помеченном disposable стенде из корня репозитория
(до fault timeout), при доступном `alfa-ml:local` и локальных входах:

```sh
cpd() {
  docker compose --env-file config/integration-m4.env \
    -f docker-compose.integration.yml -p alfa-m4-acceptance "$@"
}
cpd run --rm --no-deps prepare -m app.ingestion.pdf --apply --cache-dir artifacts/ocr
cpd run --rm --no-deps prepare -m scripts.prepare_integration \
  --company U_15728 --company U_11496 --demo-grant alice --apply
cpd run --rm --no-deps prepare -m scripts.replay_integration_news
cpd run --rm --no-deps prepare -m scripts.prepare_product
cpd run --rm --no-deps prepare -m pytest \
  tests/scenarios/test_g1.py tests/scenarios/test_product.py -q -p no:cacheprovider
# Повторить именно ту же команду pytest на том же стенде.
cpd run --rm --no-deps prepare -m scripts.product_probe \
  --output artifacts/product-snapshot.json
```

Writable экспорт явно отделён от assertions. `INTEGRATION_ARTIFACTS_DIR` сужает
запись до `artifacts/m4-acceptance`, worker остаётся read-only. Для двухвызовной
политики сначала `cpd stop worker`, дождаться остановки, затем отдельно:

```sh
cpd run -d --no-deps --name alfa-m4-policy --entrypoint /app/.venv/bin/python \
  worker tests/scenarios/policy_worker.py
cpd run --rm --no-deps prepare -m pytest \
  tests/scenarios/test_live_policy_worker.py -q -s -p no:cacheprovider
```

Каждый следующий fault mode требует остановки предыдущего test-only worker;
`CONTROLLED_LLM_RESPONSE=invalid_json`, затем `timeout` передаётся через `-e` и
worker, и prepare. Timeout **последний**, gate после него не сбрасывать.

Сейчас app доступен на 18086; worker после fault проверки остановлен, тестовый
uncertain gate сохранён. Не использовать этот disposable ledger для live и не
чистить его ради повторения. Policy worker — только тестовый fixture, **не**
способ включить live. На рабочей общей ledger ни миграций, ни reconciliation нет.

## Следующий ограниченный live G1: команда подготовлена, запуск заблокирован

Модель **`deepseek/deepseek-v4.1-flash`**, primary base URL
**`https://changellenge.up.railway.app/v1`**; альтернативный разрешённый адрес —
`https://api.alfahack-gateway26.ru/v1`. URL выбирается вручную до запуска, не
переходом после неопределённого запроса. Одна существующая durable PostgreSQL
ledger для app/worker/CLI/обоих URL и один scope по ключу обязательны. Изолированная
тестовая DB не подходит для live; в этой задаче рабочая конфигурация/ключ не читались.

Схема: обычный `POST /v1/chat/completions`, messages содержат JSON schema;
`stream=false`, `max_tokens` 900 draft / 700 verify, Pydantic Draft/Verification.
Нативный structured output не предполагается. Каждый input с schema/framing ≤8000,
суммарный input reserve ≤16000, output reserve ≤1600. Нет модельного planner в
этом явно выбранном operator smoke, дополнительного model smoke, repair, retry,
SDK retry или fallback. Отрицательный/некорректный draft может завершить run одним
вызовом; uncertain draft **никогда** не вызывает verify. Idempotency key сохраняется.

Тариф **не подтверждён**: исходный PDF содержит allowlist и 500 ₽/день, но не единицу
тарификации/порядок input-output. Пользовательские ориентиры не повышены до verified:

| model_id | Ориентировочная пара ₽, единица/порядок ещё не подтверждены |
|---|---:|
| google/gemma-4-26b-a4b-it | 4,48 / 23 |
| z-ai/glm-5.3-flash | 5,10 / 17 |
| qwen/qwen3.6-35b-a3b | 5,33 / 74 |
| deepseek/deepseek-v4.1-flash | 10 / 21 |
| qwen/qwen3-vl-30b-a3b-instruct | 14,30 / 57,10 |
| moonshotai/kimi-k2.6 | 51 / 270 |

Запросы публичного `/openapi.json` **без ключа**: первый адрес ConnectTimeout,
второй 404; это не диагноз общей недоступности сервиса, а отсутствие подтверждения
тарифов этими попытками. [Фактический результат](../reviews/M4-acceptance/public-docs-probe.json).
`/key/info` не вызывался; остаток сам по себе не подтверждает тариф.

**Условный расчёт**, только если организаторы подтвердят 10/21 **₽ за 1 млн
input/output tokens** для этой модели: `(16000×10 + 1600×21)/1000000 = 0,193600 ₽`.
Если это за 1000 tokens, резерв **193,6 ₽**, запуск ≤5 ₽ невозможен. Поэтому
config/llm-tariffs.json остаётся пустым, unknown tariff блокирует сеть. Нужны
дата/версия официального подтверждения единицы и порядка цен, точный model_id и
подтверждение, что output cap охватывает **все оплачиваемые reasoning tokens**.
Canonical completion_tokens_details.reasoning_tokens — subset completion_tokens:
расход по completion_tokens один раз. Неоднозначный отдельный positive
reasoning_tokens или отсутствующий usage → uncertain, reserve/gate удерживаются,
автоматического повтора через второй адрес нет.

После отдельного разрешения, подтверждения тарифа и настройки существующего
app/worker на `ANALYSIS_MODE=gateway`, `LIVE_LLM_ENABLED=true`, тот же durable
DATABASE_URL, owner-managed key и pinned ML assets, **внутри настроенного worker**:

```sh
python -m scripts.integration_live_smoke --allow-paid \
  --actor alice --company U_15728 --event case-rate-20260911 \
  --as-of 2026-09-14T00:00:00Z --request-id m4-first-approved \
  --max-calls 2 --budget-rub 5
```

Предварительно выбрать U_15728 в серверной сессии alice. Команда локально сверяет
точный тариф и полный двухвызовный reserve ≤5 до очереди/сети. Нельзя запускать
дополнительный `model-smoke`, менять request-id ради повторения uncertain, сбрасывать
gates или использовать другую DB/endpoint для обхода отказа. При превышении
byte envelope завершится safe partial; будущий live не объявляется гарантированно
полным по offline шаблонам. Ключ/подтверждённый тариф/разрешение и проверка фактической
общей runtime ledger остаются условиями запуска, не сделанными действиями.

## Итоговые границы

1. **M4 принят технически в ограниченном объёме**: рабочее планирование, адаптация
   локального поиска, строгие проверки, pinned follow-up и bounded stop. Исходный PR
   без исправлений не принят. Полная автономная графовая агентность и quality gate
   G1/G2/G3 не объявлены выполненными.
2. **Код подготовлен к merge**, общие проверки и реальные API/worker сценарии проходят;
   история PR сохранена. Браузерная регрессия этой версии остаётся открытой из-за
   отказа origin gate. Main/GitHub/push/deployment не менялись.
3. **Live smoke пока заблокирован**, хотя команда/лимиты проверены offline:
   нет документированного тарифа с единицей/порядком и полным output cap, отдельного
   разрешения/настроенного общего live runtime. После снятия этих условий проверить
   реальный JSON/usage/latency/семантику модели; offline transport этого не доказывает.
