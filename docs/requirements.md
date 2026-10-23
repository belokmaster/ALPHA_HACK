# Требования и текущие границы

Это матрица покрытия foundation, а не заявление о приемке всего недельного продукта. План R01–R21 сохраняется в [plan.md](plan.md); W01–W19 — в [decisions.md](decisions.md). Schema: **1.0.0**, policy: **foundation-1**. Команды и фактический результат проверки окончательно фиксирует интегратор в [handoffs/BOOTSTRAP.md](handoffs/BOOTSTRAP.md).

Статусы: `implemented` — foundation-код реализован и проверен в указанной границе; `fixture_only` — только synthetic семантика; `contract_only` — DTO/порт без реального адаптера; `pending` — следующая работа; `blocked` — конкретный gate, например live до проверки тарифов. Фактические результаты: handoffs/BOOTSTRAP.md. Полный продукт не объявляется выполненным.

Обновление 23.10.2026 касается только R05/R07/R09/R11/R14/R15/R19/R20: их текущие
границы проверены в [LLM runtime merge readiness](handoffs/LLM-RUNTIME-CONFIG.md).
Остальные строки ниже сохраняют исторический foundation scope; они не переоценивались.
Ранее оплаченная сессия закрыта, новые платные вызовы в merge-проверке запрещены.

| ID | Foundation статус | Наблюдаемое покрытие / граница | Следующая задача |
|---|---|---|---|
| R01 Компания/группа | fixture_only | Synthetic profiles, features, typed relations, authorized group subset; полный CSV import не выполнен | A02/A05 |
| R02 Представитель/потребности | implemented | Server session/grants, context version, CEO/CFO/unknown и preferences; роль не права | A03/B01 |
| R03 Внешние новости/банк | contract_only | Event/Document/Evidence и provider ports; synthetic source. Реальных CBR/OCR нет | B02/M02 |
| R04 Персональный отбор | fixture_only | Детерминированная synthetic feed с reason codes; nDCG и преимущество не измерены | A05/M04/M06 |
| R05 Интерпретация/6 вопросов | implemented (ограниченно) | Шесть секций и проверяемые conditional claims; реальный G1 adaptive + follow-up сохранены. partial честный: bank_view пуст, качество/gold и human OCR не доказаны | M05/M06: независимая оценка; browser до демонстрации |
| R06 Вклад банка | implemented (ограниченно) | Точная датированная позиция банка из verified forecast/opinion отдельно от персонального эффекта; unverified остаётся фоном. Полнота нового корпуса и bank ablation не приняты | M02/M06; handoffs/AGENT-PRODUCT-FINISH.md |
| R07 Адаптивный агент | implemented (ограниченно) | M04: plan(search/clarify) → retrieval/refinement → draft → verify через отдельный worker; до 3 calls, bounded stop. Live G1 подтверждён предыдущей сессией, two-call smoke не считается adaptive | Независимая оценка обобщения, без расширения каналов |
| R08 Полный путь/вход | implemented | DEMO shell → session → feed → job → worker → saved analysis → evidence/follow-up | B03/B04 |
| R09 Источники/уточнения | implemented (ограниченно) | API saved replay, evidence/page/PDF и содержательный live follow-up с user_declared; ACL/context сохранены. В текущей приёмке HTTP проверен, браузер запрещён политикой | Browser A9/B5 desktop/mobile; ручная сверка PDF |
| R10 Unknown/noisy data | implemented | Sentinel/flags/Decimal/quarantine/stubs/repeated pairs/time; реальная импортная приемка отдельно | A02/M05 |
| R11 Allowlist/лимит | implemented (с ограничениями) | Один gateway/ledger/key gate; runtime pins, ≤5 ₽/run, явная durable session ≤50 ₽/30 calls, day 450/500. Live по умолчанию off; закрытая сессия не возобновляется. Provider usage известен; экспериментальная оценка ×2.23 принята владельцем, внешний spend сверяется отдельно; общий cap принят оператором; все модели allowlist разрешены, отдельного model approval нет | Перед будущим live: общая сессия с бюджетом; daily spend и historical total_spend разделены |
| R12 Безопасность | implemented | Ownership/ACL/context/CSRF/session и negative tests; production SSO/SSRF ingest не выполнены | A06/B02 |
| R13 Новые новости | pending | Synthetic fixtures не являются heldout real news или live import | B02/M01/M06 |
| R14 Происхождение | implemented (ограниченно) | Source/hash/page/span, ACL/temporal и runtime/model/tariff pins по job/run/call; отклонённые claims не публикуются в зависимых секциях. agent_page_checked не human_verified | Независимая проверка смыслового перехода и PDF |
| R15 Метрики/сопровождение | implemented (техническая граница) | Durable usage/reserve/uncertain и отдельная внешняя сверка; 26 исторических live calls одной G1-сессии не являются benchmark. В merge-проверке новых paid calls 0 | Полный gold/качество и причины тарифного расхождения |
| R16 Комплект сдачи | implemented | Repo/Docker/locks/docs/synthetic fixtures; внешний release/jury access не входит в задачу | A08/B07 |
| R17 JSON | implemented | Pydantic extra=forbid, JSON Schema/OpenAPI, примеры и стабильные ошибки | A01 |
| R18 Внешний сервер | pending | Публикация/deploy не выполняются | B07 после отдельной задачи |
| R19 Embeddings | implemented ранее; регрессии сохранены | Существующий локальный E5/pgvector/FTS остаётся подключённым; runtime-ветка не заменяет retrieval. В этом проходе PostgreSQL/fixture регрессии, без нового OCR, загрузки весов или оценки качества E5 | Независимый retrieval/gold; веса не скачиваются при startup |
| R20 SDK/нет регистрации | implemented | Demo bank session без регистрации; общий httpx transport через factory, retries=0, без автоматического fallback/repair. App/worker/CLI используют общий runtime; прямой completion вне gateway не добавлен | Production SSO вне scope; будущий live только по новому разрешению |
| R21 Командная разработка | implemented | AGENTS, owner paths, task cards, ADR, handoff; общие contracts не меняются параллельно | A08/B04 |

| ID | Foundation статус | Что проверяется сейчас / что осталось |
|---|---|---|
| W01 | implemented | RUB/2025, sentinel 1000/1000.0, raw/null/0/1001 |
| W02 | implemented | Decimal RUB, целое count, начало 01.01.2026, end=null по официальному Q&A20 |
| W03 | implemented | Self-transfer не supplier; проценты из нового аудита отдельно от слов эксперта |
| W04 | implemented | CLIENT + server grant, CLOSE_CLIENT semantic; не автоматический login из CSV |
| W05 | implemented | Authorized group subset, hidden aggregate недоступен; consolidation not_performed |
| W06 | implemented | Конфликтный U карантинируется; реальный import A02 впереди |
| W07 | implemented | Service-geography/conflict, raw сохранен, не residence |
| W08 | implemented | NEGATIVEMARK не risk, FTS страны без выдуманного направления |
| W09 | implemented | Stub unknown neighbor, payment role unknown |
| W10 | contract_only | Допустимость frameworks документирована; собственный adaptive controller еще впереди |
| W11 | pending | Local E5/reranker разрешены, не подключены; внешних AI calls нет |
| W12 | fixture_only | No-bank → not_found; реальный bank вклад не измерен |
| W13 | implemented | Notices в synthetic responses/UI, partial и replay сохраняют маркировку |
| W14 | implemented | Demo auth/entry внутри shell; не настоящий bank SSO |
| W15 | implemented | Role switch меняет акцент; grants/event/bank facts инвариантны |
| W16 | pending | CBR RSS/article/corrections и PDF ingestion впереди |
| W17 | implemented | Docker/API/worker и интерактивный synthetic путь |
| W18 | pending | Steel 2021 инвентаризирован; OCR и дата/применимость будут проверены M02 |
| W19 | fixture_only | Небольшой demo pool; полный case scale и time-to-insight не измерены |

Критерии приёмки foundation, сведённые из bootstrap-задания:

| Gate | Статус foundation | Проверка / evidence |
|---|---|---|
| F01 Пустая БД, migration=head, seed дважды | implemented | `make bootstrap`, `make migrate`, два раза `make seed`; нет дубликатов |
| F02 API→DB→отдельный worker→result и restart | implemented | `make test-e2e`; result сохраняется после restart worker |
| F03 Идемпотентность | implemented | `make test-e2e`: principal+route+key, replay и 409 другого тела |
| F04 Role/context/grants | implemented | `make test-e2e`: version conflict, неизменные факты и ACL |
| F05 Изоляция | implemented | `make test-integration`, `make test-e2e`: context/assumptions/jobs/results/evidence между actors |
| F06 Нормализация | implemented | `make test`: flags/revenue/payment/quarantine/stub/client types |
| F07 Время | implemented | `make test`: strict future aggregate/unknown availability, snapshot warning |
| F08 Typed graph/ACL | implemented | `make test`, `make test-integration`: self ≠ supplier, F ≠ principal, group ≠ grants |
| F09 Бюджет | implemented | `make test-integration`: concurrent reserve cap, 03:00 Moscow reset, uncertain retained |
| F10 Lease/retry/adapter | implemented | `make test-integration`: bounded recovery, no blind unknown-outcome replay, unavailable errors |
| F11 Контракты | implemented | `make export-contracts`, `make test`: examples valid, broken claim refs rejected |
| F12 Notices/атрибуция | implemented | `make test-e2e`: investment/AI/fixture/time notices, no invented bank_view |
| F13 Code checks | implemented | `make lint`, `make typecheck`, `make check`; проверки фактически фиксируются интегратором |
| F14 Отдельный input audit | implemented | `make audit-inputs INPUT_DIR=raw/csv`; новый artifact с hashes, no auto import |
| F15 Передача/чистый запуск | implemented | Handoff, owner tasks и повторный запуск из чистого synthetic окружения |

`make test-e2e` в этой поставке — HTTP/application integration. Browser smoke, ручная финансовая оценка, OCR, FTS/E5 recall, real CBR import и live LLM smoke не считаются проведенными через эти команды. Ошибка Docker/network блокирует конкретный gate; остальные результаты фиксируются отдельно.

## Источники и владельцы матрицы

R01–R21: исходные страницы, категории О/Р/И и владельцы сохранены в docs/plan.md §1.3; эта таблица задаёт статус именно foundation. W01–W19: конкретные строки/вопросы транскрипции и владелец в docs/decisions.md. F01–F15: критерии bootstrap-задания сохранены в таблице выше; A — storage/security/contracts, B — HTTP path/docs, M — semantic ports. `implemented` не означает production-ready или завершение полного R-требования. Исторический foundation gate не описывает позднюю live-сессию: её provider usage известен,
ориентировочные тарифы подтверждены владельцем, но причина расхождения цены и универсальные гарантии провайдера
не установлены; текущий live запрещён. См. обновлённые R11/R15 и handoff. R18 публикация остаётся pending вне текущего scope.

## Требование → первичный источник → категория → компонент/владелец → проверка

Ниже сохранён исходный traceability план; фактический foundation статус — в таблице выше. Приёмки полного продукта остаются будущими, если указаны contract_only/fixture_only/pending.

| ID | Источник | Категория | Компонент / владелец | Проверка | Демонстрация |
|---|---|---|---|---|---|
| R01 Контекст компании и группы, масштаб, деятельность | Задание, с. 4, 6, 15–16 | О | Версионные профили, типизированные связи; A02/A05, A | Импорт всех CSV; неизвестная доля рынка не вычисляется | Раскрыть признаки и ограничения |
| R02 Пользователь — представитель с потребностями | С. 5; уточнение №2 | О | Сессия, роль, задачи, ACL отдельно; A03/B01, A/B | Две роли, неизвестная роль, неизменные права | Одна компания, CEO → CFO |
| R03 Парсер внешних новостей и банковских материалов | С. 6 | О | CBR RSS+HTML; PDF/OCR; B02/M02, B/M | Реальный импорт новой публикации, страницы и hashes | Новая новость не из кеша |
| R04 Персональные отбор и ранжирование | С. 6; с. 9 критерий 1 | О/К | Объяснимый скоринг; A05/M04 | Контрфактические тесты, nDCG@5 против baseline | Две компании одной отрасли |
| R05 Содержательная интерпретация и шесть вопросов | С. 6, 11, 14; с. 9 критерии 5–6 | О/К | Шесть разделов, механизм, условия, альтернативы; M04/M05, M | Ручная рубрика, отсутствие неподтвержденных эффектов | Раскрыть карточку |
| R06 Значимый вклад банка и отличие от AI | С. 9 критерий 2; с. 11 | О/К | BankThesis и отдельные claims; M02/M04/M06 | Абляция банковского корпуса | Сравнить прежний и новый прогноз |
| R07 Адаптивный многошаговый агент | С. 7; с. 10 критерий 10 | О/К | Собственный контроллер, инструменты, проверки; M04/A04 | Разные трассы, повторный поиск, остановка | Показать краткий журнал действий |
| R08 Полный путь и вход из Альфа-Бизнеса | С. 6, 8; с. 10 критерий 8; уточнение №2 | О/К | Смоделированный главный экран и сессия; B01/B03/B04 | Сквозной тест | Пройти от входа до ответа |
| R09 Источники и уточняющие вопросы | С. 10 критерий 9; с. 14 | К/О | Evidence drawer, PDF page, диалог; B03/M04 | Ссылка поддерживает утверждение; контекст диалога изолирован | Уточнить условия кредита |
| R10 Неизвестные/шумные данные, конкретная компания | С. 12 | О | Null/unknown/conflict, карантин, conditional claims; A02/M05 | Неизвестная валюта, конфликт профиля, неподходящий тезис | Корректный частичный результат |
| R11 Только разрешенные LLM и общий лимит | С. 12, 18; инструкция, с. 1 | О | Gateway allowlist, ledger, лимиты; A04/M07 | Нельзя вызвать чужую модель/URL; бюджет останавливает все процессы | Сбой/исчерпание бюджета |
| R12 Безопасность | С. 12 | О — описать и показать место достаточно | Реализовать ACL, секреты, read-only tools, лимиты; A03/A06 | Межпользовательская изоляция, prompt injection, SSRF | Краткая схема; технические тесты |
| R13 Качество на новых новостях | С. 9 критерий 4 | К | Temporal holdout и live import; M01/M06/B05 | Отложенные события и некешированная публикация | Новость выбирает проверяющий |
| R14 Трассируемость, объединение источников | С. 9 критерий 3; с. 10 критерий 11 | К | Evidence IDs, версии, trace; A01/M05/B03 | Восстановить цепочку каждого существенного вывода | Открыть источник и строку CSV |
| R15 Метрики и сопровождение | С. 10 критерии 7, 12 | К | Прогон eval, latency/cost/errors, feedback; A06/M06/B07 | Отчет с знаменателями и limitations | Таблица измеренных результатов |
| R16 Комплект сдачи | С. 8, 19 | О | GitHub, commit, README, Docker, locks, notices, synthetic fixtures; A08/B07 | Чистый запуск другим человеком | Самостоятельный сценарий жюри |
| R17 Структурированный JSON | С. 13 | Рк → И | Pydantic/JSON Schema/OpenAPI; A01 | Contract tests | Ошибки и partial имеют схему |
| R18 Внешний сервер | С. 8 | Рк | Только после локальной приемки; B07 | Доступность демо, ключ на сервере | URL при наличии |
| R19 Локальные embeddings | Прямое уточнение №3 | Рз → И | E5 локально; M03 | Retrieval ablation, offline fallback | Источники находятся перефразированным вопросом |
| R20 SDK и отсутствие регистрации | Уточнения №1–2; инструкция, с. 1 | Рз/уточнение | SDK только transport; mock bank session | Нет отдельной регистрации/внешних AI API | Вход одной кнопкой |
| R21 Командная разработка и кодовые агенты | Текущий запрос пользователя | П/И | Контракты, worktrees, handoff; A01/A08 | Проверяемые пакеты и ревью | В README development process |

Официальный Q&A: [impact](official-qa-impact.md). Контракт 1.1.0 различает origin/kind/hypothesis;
bank evidence применимо по смыслу конкретного инсайта (№43–44), роли/формальная агентность необязательны.
