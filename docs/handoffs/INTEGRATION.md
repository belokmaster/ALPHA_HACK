# Интеграция G1: фактическая передача

> Актуальная конфигурация этапов, тарифы и политика G1 v2: [LLM-RUNTIME-CONFIG](LLM-RUNTIME-CONFIG.md).
> Значения и проверки ниже относятся к исторической версии этого handoff.

> Продолжение на объединённом main: [приёмка и интеграция M4](M4-ACCEPTANCE-INTEGRATION.md).
> Результаты и SHA этого исторического прохода ниже не переоценены задним числом.

> После аудита 85bf45b актуален [продуктовый проход](G1-UI-PRODUCT-FIXES.md): отдельная ветка `task/g1-ui-product-fixes`, порт 18085, объяснимая лента по признакам и два ограниченных канала. Разделы ниже сохраняют историю технического среза; live и человеческая сверка остаются отдельными gate.

> Объединённый UI/G1: [G1-UI-INTEGRATION.md](G1-UI-INTEGRATION.md), ветка `task/g1-ui-integration`, isolated controlled demo на порту 18084. Исходные аудиты/пакет/F01–F03 сохранены; первый live по-прежнему требует отдельного разрешения.

22.10.2026. Технический первый срез работает с **управляемым offline transport**;
ответ настоящей модели пока не получен, платных вызовов 0. Реальные PDF/CSV/RSS,
E5, PostgreSQL, HTTP API и отдельный worker проверены. Это не приёмка качества M04/M05
или полного gold. Synthetic fixture runner сохранён отдельным режимом.

## Состав версии

Рабочая ветка `task/g1-integration`. Общая исходная база main:
`b5b4cce1cb2da7f010a33d56dde1a8f7343d0718`. Основная реализация среза: `7e03ddc`.
Автор/коммиттер — rennelv из git config; push не выполнялся.

| Компонент | Зафиксированный HEAD |
|---|---|
| M task после согласования с main | `1955108f0b902499b1d0d6be9e28fa4df280e85f` |
| Исправление PDF/retrieval, соответствующее re-audit | `34decbdbc2390e7dfaf7bfc310d5f4e57881b2f8` |
| B02 RSS/статьи/CA | `84c1e372d240d8f0c503b05722214143d56d2f98` |
| A04/M07 gateway/ledger | `6a2640d625c6871bd704d4f9393c114248007005` |

В заголовке [M re-audit](../audits/M01-M03-reaudit.md) указан иной полный SHA с тем же
коротким префиксом 34decbd; выше фактический git object. Исходные аудиты не менялись.
После аудита до общей базы PDF/retrieval код не менялся; merge добавил совместимые
B02 поля DTO. Новые изменения этого прохода проверены отдельно, старые вердикты на
engine/UI не распространяются. Существующие планы/аудиты: [M remediation](../audits/M01-M03-remediation.md),
[B02](../audits/B02.md), [A04](../audits/A04-M07.md).

Один Alembic head **0004**; миграции, выбранное pgvector-хранилище и uv.lock не менялись.
DTO 1.1: additive `DeliveryInfo.mode/original_mode=controlled` отличает симуляцию
transport от live. Экспорт JSON Schema/OpenAPI/examples обновлён. Дополнительный
read-only port `EvidenceProvider` возвращает сохранённые фрагменты и происхождение
по реестру документов; существующие AnalysisEngine/LLMClient сохранены.
Версия policy явно закрепляется API/CLI и проверяется worker, включая follow-up.

## Реализованный путь и границы

Разрешённая компания → immutable case event → нормализованный профиль → local E5/FTS
retrieval → bounded engine → общий gateway → code checks + отдельный semantic check
→ сохранённые Analysis/AgentRun/ToolCall → карточка с раскрываемыми источниками.
В модели нет U-specific ветвления или заранее подготовленного production-ответа.
Управляемый transport — явно подписанная fixture с динамическими IDs, не ML-результат.

Минимальный M04/M05 срез ограничен каналом финансирования для темы rates:

- Код выбирает только наблюдаемые LOA/LEA; отсутствие экспозиции/допустимого события
  останавливает генерацию. Условия договора, пересмотр, остаток и планы неизвестны.
- Два вызова максимум: draft ≤900 output tokens, verify ≤700. Repair/fallback/retry нет.
  До 6 tools, 12 transitions, 60 s; gateway независимо проверяет 24k/4k tokens,
  8k input/call, общий run reserve ≤5 RUB и gate на ключ между процессами.
- Модель может предложить только условные гипотезы с event/feature IDs и альтернативами.
  Новые факты/числа, неизвестные IDs и ложная атрибуция банка удаляются до verifier.
  Unsupported/conflicting, отсутствующий или дублированный verdict удаляет claim.
  Semantic helper не является доказательством истинности; ручная оценка остаётся.
- Непроверенные численные PDF-фрагменты не становятся применимой BankThesis.
  Сейчас банк `background_only` либо `not_found`, bank_view пуст. Отдельные факты
  копируются дословно, с исходным unverified/conditional, не объявляются verified.
- Follow-up — новый run с pinned context/cutoff и conversation assumptions.
  Введённое условие сохраняется как user_declared и передаётся в draft/verifier.
  Профиль/публичный банковский источник не переписываются по пользовательскому тексту.
- HTTP LLM выполняется без открытого SQL connection/transaction. Проверка scope перед
  платными границами и завершением. При uncertain резерв и gate удерживаются, новый job
  не обходит gate. Полноценный resume промежуточного draft/repair и остальные каналы
  M04 не реализованы; после аварии не переотправляется оплаченный вызов.

[Официальный Q&A](../references/Вопросы%20с%20вебинара%20_%20Новостная%20лента.md) №43–44:
банковское доказательство нужно не каждому инсайту. Нет ложной атрибуции при no-answer;
внешний факт, банк, гипотеза модели и пользовательское условие различаются явно.
Новые новости B02 остаются domain=live, G1 — domain=case; это разные event IDs и cutoff.

## Обнаруженные ошибки и исправления

| Дефект → причина | Исправление | Проверка |
|---|---|---|
| API/worker принимали только rate_hold/fixture | Dispatch сохранённых событий в AnalysisEngine; fixture путь отдельно, запрет смешения synthetic профиля/реального события | Реальные HTTP/API/worker сценарии |
| Общий PDF нельзя было открыть из карточки | Единая source ACL: только зарегистрированный bank/news public scope + текущий sources.read; original PDF hash/path check | PG regression, asset 200, revoke 404 |
| OCR chunk обрывал прогноз до горизонта | Расширение уже найденной страницы полными typed spans; без повышения quality и без правки OCR | Оба прогноза G1 есть в итоговом Analysis и открываются по page/span |
| Private company document мог быть принят за bank candidate | Engine adapter допускает только банковский реестр; происхождение факта определяется реестром, не domain | No-bank controlled fixture через настоящий API/worker |
| Общая case-лента могла раскрыть private title | Перед выдачей Event проверяются его evidence ACL | Отдельная PG регрессия |
| Таблицы Росстата склеивались в строку без границ | news-3: table/row/cell и rowspan/colspan markers; новая версия при изменении нормализованного текста, старые данные не перезаписаны | 636 ячеек (633 непустые), 114 строк, 0 пропусков; сохранены единицы/сноски/«...» |
| Browser POST не проходил на случайном порту | Стенд использует явный loopback port/APP_ORIGIN | Browser smoke, а не только запросы без Origin |

HTML остаётся текстовыми данными и экранируется. Source view показывает сохранённый
текст, конкретную цитату, quality/value_kind/page/span, provenance и ссылку на оригинал.
`/api/v1/features/{id}` открывает raw value, SHA/строку/поле CSV. Успешная ссылка сама
по себе не подтверждает смысл тезиса.

## Воспроизводимый изолированный запуск

Входы по [local-inputs](../local-inputs.md): raw/pdf, raw/csv, локальные pinned models
по models.lock. Веса не скачиваются на startup; их подготовка явная. OCR cache
переиспользуется только при совпадении PDF/runtime/DPI/traineddata/parser; OCR-библиотеки
и Tesseract всё ещё требуются даже для cache reuse (ограничение существующего loader).

```bash
make ml-image
mkdir -p artifacts
# Public env-файл не содержит ключа и разрешён только с alfa_test + DEMO_MODE.
docker compose --env-file config/integration-controlled.env -f docker-compose.integration.yml -p alfa-g1 up -d app worker
docker compose --env-file config/integration-controlled.env -f docker-compose.integration.yml -p alfa-g1 run --rm prepare -m app.ingestion.pdf --apply
docker compose --env-file config/integration-controlled.env -f docker-compose.integration.yml -p alfa-g1 run --rm prepare -m scripts.prepare_integration --company U_15728 --company U_11496 --demo-grant alice --apply
docker compose --env-file config/integration-controlled.env -f docker-compose.integration.yml -p alfa-g1 run --rm prepare -m scripts.news_integrity --source cbr-press
docker compose --env-file config/integration-controlled.env -f docker-compose.integration.yml -p alfa-g1 run --rm prepare -m scripts.news_integrity --source rosstat
docker compose --env-file config/integration-controlled.env -f docker-compose.integration.yml -p alfa-g1 run --rm prepare -m pytest tests/scenarios/test_g1.py -q -p no:cacheprovider
docker compose --env-file config/integration-controlled.env -f docker-compose.integration.yml -p alfa-g1 run --rm prepare -m scripts.integration_probe
# Сохранённый пакет a629820 не перезаписывать: python -m scripts.review_g1 --verify
```

UI: `http://127.0.0.1:18080`, alice → U_15728 → CFO → применить → исторический G1.
Выбор новой статьи требует текущего cutoff; UI выставляет его отдельно. Открыть
сохранённый ID можно через поле «ID сохранённого анализа» (после входа).
На занятом порту задать INTEGRATION_PORT в отдельном явном env-файле; Origin изменится
согласованно. На этой машине host wrapper не передаёт shell env в Compose; поэтому
использован `--env-file`, а не `ANALYSIS_MODE=controlled docker ...`.

Это отдельная **tmpfs** БД, не рабочие volumes. Subset importer требует alfa_test,
проверяет исходный CSV SHA/конфликтные строки, использует общий normalizer. Он не
маркирует полный CSV snapshot импортированным и не подменяет полный importer.
Grant создаётся только явным --demo-grant. При конфликте с существующим профилем — stop.
`down` удалит эту тестовую БД; для повторного запуска после fault-tests поднять новый
изолированный проект и снова импортировать данные. Не применять эти операции к working DB.

Fault run: заменить env-file на config/integration-invalid-json.env, затем
config/integration-budget.env, последним config/integration-timeout.env. На каждом шаге:

```bash
docker compose --env-file config/integration-invalid-json.env -f docker-compose.integration.yml -p alfa-g1 up -d --no-deps --force-recreate worker
docker compose --env-file config/integration-invalid-json.env -f docker-compose.integration.yml -p alfa-g1 run --rm --no-deps prepare -m pytest tests/scenarios/test_faults.py -q -p no:cacheprovider
# Для timeout использовать integration-timeout.env в ОБОИХ командах; gate останется held.
docker compose --env-file /dev/null -f docker-compose.integration.yml -p alfa-g1 run --rm --no-deps prepare -m scripts.integration_state
```

## Фактические результаты

`make check`: Ruff, mypy (20 modules), contracts drift, **173 unit/contracts +61 PG
integration +2 HTTP E2E =236 tests**, exit 0. Дополнительно 3 real-data/scenario tests и
по одному worker fault-test для invalid JSON / budget / timeout — pass. No-bank и
недостаток полей проверены controlled fixtures/fault injection, не названы реальными
экономическими наблюдениями. Смена роли отклоняет старый follow-up; revoke закрывает
analysis/job/evidence/asset/feature; повторное чтение не перегенерирует ответ.

В изолированную PostgreSQL импортированы **9 настоящих PDF, 128 Evidence, 128 E5 vectors**.
Новых OCR-ошибок на ключевой странице не обнаружено, извлечение не менялось: использован
прежний cache с точным runtime identity, полный OCR не повторялся. Оригинал страницы
визуально сопоставлен агентом; независимой human verification это не является.
G1 probe с real E5 + API/worker: **10.665 s**, peak probe process RSS **854348 KiB**
(не сумма памяти контейнеров). Trace: **6 transitions, 5 tools, 2 model attempts**.
На одинаковом вопросе/данных: FTS 5 кандидатов /0.053 s; exact 8 /0.098 s;
HNSW 8 /0.061 s. EXPLAIN: HNSW index есть только у явно forced ANN; финансовые Recall/
Precision по этому техническому запросу не заявляются.

Бесплатная сеть и сохранение (22.10.2026, отдельные от G1 события):

- ЦБ: [отзыв лицензии у НКО «Тайдон»](https://www.cbr.ru/press/PR/?file=639282394585722382BANK_SECTOR.htm),
  опубликовано 22.10 04:51 МСК; 1613 символов, 1 Evidence, таблиц нет.
- Росстат: [цены нефтепродуктов 13–19 октября](https://rosstat.gov.ru/storage/mediabank/163_21-10-2026.html),
  опубликовано 21.10 17:25 МСК по RSS. 10631 символ, 6 Evidence; 636 ячеек,
  633 непустые, 114 строк, пропущено 0. CA/redirect проверены обычным SafeFetcher.

Первое сохранение создало по документу/событию; повтор и последующий полный sync дали
unchanged=1/imported=0/rejected=0 для каждого источника, source state ok. Конкатенация
сохранённых Evidence равна Document.text. Нормализованные числа не повышены до verified.
Оговорка Росстата об исключении четырёх регионов, единицы рублей/литр, даты колонок,
средневзвешенность и символы отсутствующих данных сохранены; это не финансовая разметка.
Hashes/raw captures/планы лежат в ignored artifacts/integration; измерения свернуты
в [машиночитаемый отчёт](../reviews/integration-measurements.json).

Fault snapshot: 17 результатов, 14 controlled model_calls, 1 uncertain с резервом
**0.005096 RUB simulated**, gate удержан. Следующий job — llm_busy, model_calls не
увеличились. Invalid JSON с известным usage settled; budget stop — 0 отправленных
вызовов. Реальных provider charges не было. 429/missing usage/межпроцессная конкуренция
дополнительно проходят существующие gateway integration tests; это fixtures, не live.

## Первый live smoke — что требуется

1. Отдельное явное разрешение владельца: точная модель `deepseek/deepseek-v4.1-flash`,
   максимум **2 calls**, hard reserve не более **5 RUB** на этот запуск.
2. Ключ через защищённое окружение worker/CLI; endpoint/allowlist прежние. Не присылать
   ключ в чат/URL, не хранить в коммите. LIVE_LLM_ENABLED=true, ANALYSIS_MODE=gateway.
3. Подтверждённый тариф именно этой модели с источником/версией в config/llm-tariffs.json.
   Файл по умолчанию пуст: неизвестный тариф блокирует генерацию.
4. Одна **durable общая ledger DB** для всех использующих этот ключ; не tmpfs стенд
   выше и не отдельная БД для обхода gate. В ней должны быть импортированы corpus,
   реальный профиль, case event и явные grants; выбрать компанию в серверном context.
   Runtime worker должен видеть локальные E5 assets (или явно принять FTS degradation),
   а API — исходные PDF. Production SSO в этот срез не входит.

Команда подготовлена, **не выполнялась**. Worker предварительно запускается с теми же
DB/key/tariffs и ANALYSIS_MODE=gateway; CLI проверяет режим и максимальный резерв до enqueue:

```bash
python -m scripts.integration_live_smoke --allow-paid --actor alice --company U_15728 \
  --event case-rate-20260911 --as-of 2026-09-14T00:00:00Z \
  --request-id g1-first-live-001 --max-calls 2 --budget-rub 5
```

Новые IDs не дают права повторить uncertain. Сам факт одного live-ответа не докажет
качество и не снимет ручную сверку. [Пакет G1](../reviews/G1-manual-review.md) готовится
отдельной командой; независимый полный gold остаётся задачей команды и не блокирует
техническую интеграцию. Неподтверждённый bank_view остаётся пустым и на первом live smoke.

## Оставленный demo и артефакты

После fault snapshot disposable стенд пересоздан и весь real-input путь повторён.
Оставлен чистый по ledger offline demo на http://127.0.0.1:18080; платный ключ
в нём отсутствует. Войти alice и открыть сохранённый ID из
`artifacts/integration/probe.json`, либо запустить новый controlled анализ.
Source captures и полный fault snapshot сохранены отдельно; очищалась только наша
tmpfs БД. Browser demo login и чтение итоговой карточки — pass после исправления Origin.

Первоначальный `artifacts/integration/g1-review.zip` содержал один PDF, PNG страницы и
шесть цитат. Этот неполный пакет заменён полной сверкой ниже; исходный состав/SHA
зафиксирован в config/g1-review.json. Материалы остаются локальными ignored артефактами,
а не частью Git или человеческим gold.

«Реальный профиль» в этом отчёте означает запись исходного обезличенного CSV;
название/идентичность юридического лица не восстанавливались. Известные publication
dates сохранены отдельно от unknown available_at; дата события G1 имеет точность день.


## Передача полной ручной сверки G1 (22.10.2026)

Работа начата в task/g1-integration от **3900cf15f370075e099f3694cb6ac60a2800b83c**.
Runtime анализа, retrieval, prompts, contracts, источники и права не менялись.
[Инструкция и команды](../reviews/G1-manual-review.md),
[16 SHA-закреплённых входов](../../config/g1-review.json),
[сборщик](../../scripts/review_g1.py).

Распаковать **artifacts/integration/g1-review.zip**, открыть index.html; готовая
папка — **artifacts/integration/human-review/**. Проверяется весь сохранённый
**8a738788-05dc-4a55-a69b-586871356a1e** и именно его follow-up
**4d2ba23c-c811-4ec8-a215-f3d5823e51cd**: 2+3 claims, все условия/альтернативы,
шесть разделов, вопросы, ограничения и восемь Evidence. Это controlled результат,
не первый live-ответ; bank_view остаётся пустым. Другой g1-final-analysis.json
не подставляется вместо родителя. Общие g1-analysis.json/g1-followup.json были
параллельно обновлены: SHA guard воспроизведён, новые файлы сохранены; пакет использует
независимые замороженные копии в artifacts/integration/review-inputs/.

Таблица содержит **24 строки**, точные claims, их тип/происхождение, Evidence и версии,
файлы/URL, страницы/полуоткрытые spans и координаты, цитаты, изображения, числа/единицы/
периоды/оговорки, вопросы и **пустые** решения/комментарии. Приложены оба исходных PDF
полностью (9 страниц), полный исходный CSV, read-only snapshot хранилища и исходное
сообщение пользователя. Октябрьские ЦБ/Росстат вынесены в отдельное приложение и
не используются для исторического G1. Сохранённый HTML показывается как текст;
визуализации таблиц подписаны как производные. Встроенный PNG Росстата извлечён
побайтово из того же HTML, не восстановлен OCR и не скачан заново.

Автоматика проверяет SHA, DTO, связь parent, покрытие всех claims, точные span/цитаты,
координаты, provenance CSV, версии новостей, файлы/локальные ссылки и ZIP.
Агентские наблюдения отдельно: **agent_page_checked**, человеческих решений **0**.
Macro p1 OCR теряет подписи таблицы/полную сноску; текстовые Evidence Росстата не
содержат чисел встроенного графика. Это открытые вопросы ручной сверки, не исправленные
данные и не положительная оценка. У PDF available_at по-прежнему неизвестен.

Человек оценивает также переход от события/LOA/user_declared к выводу о компании.
Сам факт совпадения слов или наличие фоновых прогнозов не подтверждает такой переход.
В pinned context ACL=1, после прежнего revoke-теста demo ACL=2: API старого результата
может вернуть 404; офлайн-копия открывается без изменения ACL и без перегенерации.

Решения скачать из index.html в **отдельный JSON**, проверить командой --decisions
(при завершении --require-complete) из инструкции. Валидатор ничего не меняет в БД.
Команда хранит отдельную рецензию с автором/датой, packet_id/manifest SHA и ссылками на
claim/Evidence; подтверждённая ошибка оформляется новой версией с provenance. Исходные
доказательства/ответы не переписываются, human_verified автоматически не устанавливается.
Сборка возможна без БД/сети/OCR/весов; ZIP содержит все входы и карту их восстановления.

Фактическая проверка этого пакета: **184 unit/contracts pass** (11 новых проверок
review tooling), Ruff/mypy/contracts drift — pass. Две сборки с отключённой сетью
дали побайтово одинаковый ZIP **41 773 024 bytes**, SHA-256
`70ee9e85b05080c02200ef7a0dc7a6ffd57a340af60a036b0cb1ba18d6591794`.
Проверены 139 ссылок, восстановление 16 входов и браузерный экспорт пустых решений:
скачанный JSON совпадает с шаблоном, answered=0/24. Полная сводка и идентификатор
образа/версии renderer — [g1-review-checks.json](../reviews/g1-review-checks.json).
Эта проверка не повторяет прежнюю приёмку PostgreSQL/retrieval/LLM: runtime не менялся.


## Исправления независимого аудита G1

[Remediation F01–F03](../audits/G1-integration-remediation.md). Начальный HEAD —
`143cef460b2a37190874625435b8cbabcf949f9f`; проверенный HEAD реализации —
`b71eb13be2088d65b65d2e4e5350477701f07039`. Исходный аудит и ZIP ручной сверки сохранены.

- Factory требует engine handle во всех режимах; None → AppError (controlled: 403,
  disabled/live: 503) до чтения тарифов/transport. Это не подключение к БД и не fallback.
- G1-тесты восстанавливают committed состояние API/worker только в БД с init-time
  маркером disposable tmpfs и controlled dummy key. Rollback одного соединения не
  используется как очистка worker commits. Unknown outcome останавливает восстановление.
  На обычной/рабочей/общей ledger-БД без маркера такой код отказывается работать.
- Assertions не пишут JSON. Worker rootfs и workspace read-only; writable только /tmp.
  Новый `python -m scripts.export_g1 --analysis ID --followup ID --output-dir DIR`
  читает сохранённую пару, пишет только в новую явно указанную папку и отказывает
  при её существовании. Использовать prepare с отдельным writable artifacts mount.

Повторение проверок на **новом** проекте (не на оставленном demo):

```bash
mkdir -p artifacts/g1-remediation/ocr
cp -n artifacts/ocr/*.json artifacts/g1-remediation/ocr/
docker compose --env-file config/integration-remediation.env -f docker-compose.integration.yml -p alfa-g1-remediation build app
docker compose --env-file config/integration-remediation.env -f docker-compose.integration.yml -p alfa-g1-remediation up -d app worker
docker compose --env-file config/integration-remediation.env -f docker-compose.integration.yml -p alfa-g1-remediation run --rm --no-deps prepare -m app.ingestion.pdf --apply
docker compose --env-file config/integration-remediation.env -f docker-compose.integration.yml -p alfa-g1-remediation run --rm --no-deps prepare -m scripts.prepare_integration --company U_15728 --company U_11496 --demo-grant alice --apply
# Выполнить эту команду ДВАЖДЫ, без reset/restart между ними:
docker compose --env-file config/integration-remediation.env -f docker-compose.integration.yml -p alfa-g1-remediation exec worker /app/.venv/bin/python -m pytest tests/scenarios/test_g1.py -q -s -p no:cacheprovider
```

Старый tmpfs стенд, созданный до init-marker, для cleanup-тестов не подходит. Не
добавлять маркер в рабочую БД: создать новый Compose project. Сценарные тесты удаляют
свои результаты при восстановлении baseline; для экспорта нужен отдельно выбранный
сохранённый controlled результат, не запуск тестов ради JSON. Импортеры пишут отчёты
в artifacts/g1-remediation; старые артефакты и review-inputs не используются как output.

Для будущего live job добавлена private worker policy **g1-live-two-call-1**:
старый worker отклонит её до модели. Model/tariff version/rates и разрешённый reserve
закреплены в job; worker повторно сверяет их. Только `job_draft` (≤900 output) и
`job_verify` (≤700 output), каждый input ≤8000, общий максимум резервов ≤5 ₽.
Общий gateway/ledger, key gate, daily/run limits и retries=0 сохранены. Verify возможен
только после успешного draft. Повтор request-id получает тот же job; новый ID не
используется для обхода uncertain. Ни model smoke, ни repair/retry/fallback не запускаются.

Фактически: make check — **204 unit/contracts, 66 PG integration, 2 HTTP e2e**;
controlled read-only unit/contracts — **204**; два G1 запуска на одной БД — **по 3**.
Счётчики повторов не суммируются. В отдельном fixture-worker пути operator job
успех: 2 controlled вызова; invalid JSON: 1; timeout: 1, reserve/gate удержаны.
Это управляемые HTTP-ответы на настоящих данных/API/worker, не платный live.
Команда выше с --allow-paid **не выполнялась**. Условия первого реального вызова
остались: явное разрешение, защищённый ключ, подтверждённый точный тариф, новый worker
и одна durable общая ledger-БД с данными/grants. Этот tmpfs Compose для live не применять.
