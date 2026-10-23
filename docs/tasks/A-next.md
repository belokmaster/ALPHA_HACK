# A-next: case import и security/live gate

Цель: идемпотентный case import, quarantine, typed graph, group/payment coverage, затем общий live gate.
Inputs: contracts 1.1.0, config/inputs.manifest.json, новый artifacts/input-audit.json, W01–W09, time ADR.
Зависимости: текущая миграция 0003; решения о доступности snapshot и подтвержденные tariffs/usage до live.
Allowed: app/ingestion/csv/, app/auth/, app/budgets/, app/core/, app/llm/, tests/integration/, tests/unit/.
Contracts/migrations/locks — только отдельный согласованный review; нельзя ломать порты B/M, UI и ML.
Output: import manifest/source versions/quarantine, coverage без скрытых group sums, безопасная схема grants revocation,
единый gateway всех членов команды. Advanced shares только после контроля повторов, denominator и отдельного права на платежи.
Done: повторный import не дублирует версии; конфликт U изолирован; новый actor не видит чужие sources/jobs;
concurrent reserve не превышает cap, uncertain не повторяется; live gate закрыт без проверенных тарифов.
Существующие команды: `make audit-inputs INPUT_DIR="raw/csv"`, `make test`, `make test-integration`.
Реализовано: `make import-case INPUT_DIR=...` (audit-only dry-run), `APPLY=--apply` для записи;
read-only ProfileProvider/RelationsProvider, quarantine/idempotence/server ACL.
Статус: [handoff A02/A05 → ML](../handoffs/A02-A05-ML.md).
Полноразмерная проверка apply/rollback/repeat и ресурсов: [remediation](../audits/d170154-remediation.md).
A04 offline gateway реализован; M07 live acceptance ждёт разрешения/ключа/тарифов.
[Передача интерфейса M04/M07](../handoffs/A04-M07-ML.md). Ускорение CSV не входит в этот проход;
повторный аудит принял импорт/worker, 2–4 min не официальный критерий.
Multi-version company snapshots, feed scoring и payment denominator coverage — отдельные следующие задачи;
A05 целиком ещё не принят.
Handoff: schema changes A→B/M, import report и миграция, условия включения live, отрицательные тесты.
