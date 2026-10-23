# BOOTSTRAP — фактическая передача, 21.10.2026

Этот документ фиксирует историческую приёмку bootstrap. Текущее продолжение после
ML-коммитов и migration 0002: [A02/A05 → ML](A02-A05-ML.md).

## Результат и версии

Рабочий synthetic backend в `project-repo`: PostgreSQL 17.7, FastAPI, server sessions/grants/context CAS,
queue/worker/leases/heartbeat/fencing, идемпотентность, persistence, provenance/time contracts,
pure normalizers, явный CSV audit, atomic budget ledger и минимальный интерактивный DEMO shell.
Контракт **1.0.0**, policy **foundation-1**, миграция **0001**. Python **3.12.14**, uv **0.13.4**,
реальный uv.lock, frozen install. Платных model calls: **0**; ключи не читались.

Base/HEAD: `71518ee990b571a8c3f398f2e50a90414575411b` (main).
Изменения оставлены в рабочем дереве; новых коммитов, push/deploy и изменения remote не было.
Исходные README heading, `src/Dockerfile`, `src/.gitkeep` и инструкции среды сохранены.
Связанные существующие worktree сохранены;
их заготовки проверены и интегрированы. Новых кодовых агентов не запускали.

Изменённые/добавленные области: .env.example, README, docker-compose.yml; Dockerfile/.dockerignore/.gitignore,
Makefile, pyproject.toml/uv.lock/.python-version; app/core/auth/jobs/budgets/agent/llm/ingestion/web/seed/main;
contracts/ (DTO, ports, OpenAPI, JSON Schema, examples); migrations/; fixtures/synthetic/; scripts/;
tests/unit/contracts/integration/e2e/; config/, eval/, docs/, AGENTS.md, API_EXAMPLES.md, THIRD_PARTY_NOTICES.md.

## Что действительно проверено

| Команда / сценарий | Фактический результат |
|---|---|
| uv lock; uv sync --frozen | Установлены закреплённые зависимости, Python 3.12.14 |
| make bootstrap | Собран образ, создан новый dev volume, migrate/seed завершены, app/worker запущены |
| GET /health/ready | `{"status":"ready","mode":"fixture","live_llm":false}` |
| make check | exit 0: Ruff; mypy strict (9 source files); schema/example drift check; все 90 тестов |
| make test | 77 passed: normalization/time/limits/disabled demo auth/contracts/examples/broken evidence refs |
| make test-integration | 11 passed в отдельном новом PostgreSQL tmpfs project: seed twice, revision 0001, budget race/reset/uncertain/overrun, lease recovery/unknown outcome/parallel claim/heartbeat/typed graph |
| make test-e2e | 2 passed в другом новом Compose project: HTTP app → PostgreSQL job → отдельный worker → saved analysis; role/ACL/CSRF/idempotency/followup/isolation/time/notices |
| scripts/persistence_probe.py create → compose restart app worker → verify | 6 сохранённых анализов доступны после настоящего рестарта, mode=saved_replay |
| explicit CSV audit | Полные 3 файла измерены за 44.811 s; [summary](../input-audit-summary.md); raw не импортирован в app |
| git diff --check | Пройден |

Фактическая среда: Linux x86-64 Distrobox, docker wrapper → host Podman **5.8.7**, Docker Compose **5.6.0**.
Это проверка совместимости Compose на Podman, не независимый прогон Docker Engine и не вторая машина.
Host wrapper запускался с путём к этому же репозиторию, видимым на хосте.
Команды ниже выполняются из корня репозитория; `uv` должен быть доступен в PATH.

```bash
make bootstrap
uv sync --frozen
make check
```

Обычной машине команды достаточно `make bootstrap`, `uv sync --frozen`, `make check`.
Test targets сами собирают актуальный образ и создают уникальный Compose project; затем down.
Dev volume не удаляется. Dev приложение оставлено на http://localhost:8000; `/docs` — Swagger.
Тестовый проект alfa-bootstrap-verify остановлен отдельно после проверки рестарта.

## Принятые решения / изменения плана

Полный [plan.md](../plan.md) сохраняет R01–R21, A/B/M WBS, G1/G2/G3, baseline/абляции, 126+21 часов и недельные gates.
Включены W01–W19: RUB/2025 и sentinel 1000; RUB/count/платежное окно 01.01–31.10.2026;
self-transfer отдельно; CLIENT + server grant; F/group не principal/ACL; authorized group subset без консолидации;
карантин конфликтного U; service geography semantic conflict; NEGATIVEMARK не риск, ФТС только страны;
unknown counterparty stubs. LangChain/LangGraph разрешены, собственный controller — выбор команды.
Local E5 допустим, внешние AI endpoints не добавлены. No-bank insight допустим без ложной атрибуции.
Investment/AI/synthetic/time notices, demo bank entry, CEO/CFO/unknown, текущие новости + dated bank PDFs сохранены.
Steel 2021 не объявляется отсутствующим Commodities performance. Snapshot не historical backtest;
strict_as_of требует независимую available_at и завершённый period.

Новый аудит: self-transfer 81.66% суммы / 13.29% ΣPAY_CNT / 1.99% строк. Это не «3%» из вебинара;
вопрос Q-AUDIT зарегистрирован, данные не исправлялись под устный процент. Один quarantined U (12 строк),
1 exact payment duplicate, 9 heterogeneous repeated pairs, 114552 payment stub nodes.
Advanced shares остаются A05, никакого расчёта закупочной/выручечной зависимости.

## Границы fixture / contract-only и риски

- Fixture runner: rate_hold, fixed_rate; произвольный follow-up → честное fixture_only/partial.
  Все outputs synthetic; model_id=null, usage_status=not_called; нет настоящих тезисов Альфа-Банка.
  Секции consequences/bank_view могут быть пустыми, причины в missing_fields; это ограниченный foundation.
- Persisted Analysis пинит context/company/event/source/policy; follow-up пинит исходный context, assumptions conversation-scoped.
  Сохранённый ответ возвращает saved_replay/original_mode=fixture. Персонального кеша нет; права повторно проверяются.
- Protocol ports готовы для ProfileProvider, RelationsProvider, EventProvider, EvidenceRetriever, AnalysisEngine, LLMClient.
  Настоящие retrieval/CBR/OCR/E5/adaptive planner/semantic verifier/full case import отсутствуют.
  UnavailableLLMClient возвращает backend_unavailable/feature_not_implemented, даже если выставлен live flag.
- Бюджет протестирован с fake tariff/clock; реальные цены/usage/model availability неизвестны, live gate закрыт.
  В будущем все участники обязаны использовать один gateway/ledger; SDK max_retries=0, uncertain reserve не освобождать.
- DEMO actor selector сознательно открытый и не является production authentication. Реальный bank SSO не реализован.
- Browser smoke не проводился; проверены HTTP path и HTML shell. OCR/таблицы аналитических PDF не перепроверялись.
  Польза аналитики, heldout quality, bank ablation, peak RAM/latency и стоимость продукта не измерены.
- Контейнеры закреплены exact version tags; фактические RepoDigests записаны в config/container-images.json.
  Повторная сборка Debian/system components по digest — release-hardening A08, не заявляем bit-for-bit build.

## Найденные и исправленные ошибки

Первоначальный sandbox DNS/proxy отказ PyPI устранён разрешённой загрузкой вне sandbox; Docker wrapper exit 127
разрешён доступом к host runtime. Исправлены missing greenlet (sqlalchemy[asyncio]), pytest import path,
строгие annotations/Literal и форматирование. У CSV audit automatic SQLite index выбирал только versions,
создавая дорогой join; первый прогон остановлен, добавлены явные row/first_row indexes, полный повтор успешен.
Sync TestClient зависал в этой среде; unit auth test переведён на async HTTPX ASGITransport и проходит.
Секреты и raw prompts в сообщения/логи не выводились.

## Передача

[AGENTS.md](../../AGENTS.md) — владельцы/allowed paths/инварианты.
[contracts/v1.py](../../contracts/v1.py), [ports](../../contracts/ports.py), [API_EXAMPLES](../../API_EXAMPLES.md),
[миграция](../../migrations/versions/0001_foundation.py), [sources](../sources.md), [requirements](../requirements.md).

- A: [case import, coverage, security/budget live gate](../tasks/A-next.md).
- B: [UI/evidence/states и CBR RSS+article](../tasks/B-next.md).
- M: [verified bank chunks, local retrieval, bounded engine, eval](../tasks/M-next.md).

Открытые вопросы: available_at snapshot/платежного агрегата и конец окна после даты подготовки;
географический конфликт; знаменатель 3%; тарифы/usage gateway; точный deadline/канал сдачи.
Они не препятствуют проверенному synthetic пути и не маскируются фиктивной реализацией.
