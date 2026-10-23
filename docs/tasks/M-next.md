# M-next: verified bank chunks, retrieval, bounded engine, eval

Цель: OCR/верификация банковского корпуса, local E5 + PostgreSQL FTS, adaptive AnalysisEngine и semantic verifier.
Inputs: contracts 1.1.0/ports.py, plan G1/G2/G3, sources manifest, config/retrieval.json, limits.py.
Зависимости: A import/source grants и budget gate; B CBR adapter. До них использовать явные offline fixtures.
Текущий параллельный scope M: app/ingestion/pdf/, app/retrieval/, eval/, соответствующие tests.
app/llm/, ledger, worker и общие contracts сейчас у A. Будущий M04 подключается
через [готовый gateway interface](../handoffs/A04-M07-ML.md), не создаёт второй transport.
Запрещено самовольно менять contracts/, migrations/, uv.lock; тяжелые extras согласовать A. Не загружать веса на startup.
Output: проверенные page/span/date/metric chunks, versioned local model manifest, инструменты read-only без SQL/HTTP/code от LLM;
собственный bounded planner/verifier, gold и measured baseline/роль/graph/bank ablations.
Done: шесть вопросов, no-bank=not_found, условный эффект не численный без договора; future/unknown availability исключены;
12/6/4/1/60s/24k/4k caps с fallback/repair; unknown outcome удержан, тарифы подтверждены до live.
Существующие проверки: `make test`, `make typecheck`, `make test-integration`.
Будущие targets: `make eval`, `make test-retrieval`. `make live-smoke` не используется.
Существующий отдельный `make model-smoke` требует явного разрешения и настроенных тарифов/ключа;
M07 live acceptance не засчитан offline-проверками.
Handoff: A→M gateway/usage/error protocol; M→B Analysis states/evidence; eval report с manifests и ограничениями, без выдуманных метрик.

## После независимого аудита M01–M03

Исходный HEAD 500f036; [remediation](../audits/M01-M03-remediation.md),
[интерфейс и измерения](../handoffs/M01-M03.md). Реальные 9 PDF/64 pages, E5 и
pgvector проверены; full gold acceptance ещё BLOCKED. Не начинать M04 в этом проходе.
Приоритет: A/B source manifest и независимые splits/labels; human check ключевых
PDF snippets/таблиц; availability proof либо явно согласованный snapshot benchmark.
15 agent-page-checked spans не считать human labels. No-answer background допустим,
применимая позиция банка не возникает из одной близости OCR. Gateway/B02 не менять.
