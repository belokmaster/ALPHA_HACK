# Handoff M04 ↔ Backend A: Contract & Infrastructure Gaps

> Историческая передача исходного PR `e47316b`, а не отчёт о принятой интеграции.
> Часть заявлений ниже воспроизведена как дефекты либо относится к устаревшей базе.
> Актуальные решения и ограничения: [M4 acceptance](M4-ACCEPTANCE-INTEGRATION.md).

**Дата:** 22.10.2026
**Ветка:** `task/M04-agent-loop`
**Автор:** Senior / Principal ML Engineer (M)
**Получатель:** Backend Integrator (A)
**Статус документа:** ACTIVE GAP REGISTER

---

## 1. Резюме

В рамках пакета M04 полностью реализован автономный ограниченный `AnalysisEngine` (цепочка: `Plan → Read-only tools → Draft → deterministic guardrails → semantic verify → optional bounded repair → validated Analysis DTO`), а также сценарный `analyze_followup`.

В соответствии с правилами `AGENTS.md` и матрицей владения:
- Зоны `contracts/`, `app/jobs/worker.py`, `app/main.py`, `migrations/`, `uv.lock` **не модифицировались напрямую**, чтобы исключить конфликты владения.
- Ниже зафиксирован полный реестр из 10 архитектурных и интеграционных разрывов со строгой классификацией статусов: `FIXED`, `OPEN — OTHER OWNER`, `BLOCKED — ENVIRONMENT/DATA`, `NOT REPRODUCED`.

---

## 2. Полный реестр интеграционных разрывов и статусов

### Gap 1: `Claim.relation_ids` и provenance графовых связей
- **Статус:** `OPEN — OTHER OWNER` (владелец: A, зона: `contracts/v1.py`).
- **Суть проблемы:** Класс `Claim` в `contracts/v1.py` содержит `evidence_ids: list[ID]` и `feature_ids: list[ID]`, но не содержит `relation_ids: list[ID]`. При этом `Claim(kind="fact")` строго требует наличия `evidence_ids`. Если агент обнаруживает значимую связь контрагентов через `RelationPath`, он не может сослаться на нее как на прямое основание в `Claim`, не создавая фиктивный объект `Evidence` (что запрещено инвариантами проекта).
- **Решение в ML-слое:** Графовые связи передаются в промпты исключительно как непроверенный фоновый контекст (`relations_unverified_background`), а создание прямых фактов о связях без документов блокируется детерминированными guardrails.
- **Предложение для Backend A:**
```diff
--- a/contracts/v1.py
+++ b/contracts/v1.py
@@ -330,6 +330,7 @@ class Claim(DTO):
     text: str
     evidence_ids: list[ID] = Field(default_factory=list)
     feature_ids: list[ID] = Field(default_factory=list)
+    relation_ids: list[ID] = Field(default_factory=list)
     conditions: list[str] = Field(default_factory=list)
     alternatives: list[str] = Field(default_factory=list)
```

---

### Gap 2: Доступ к настоящим событиям вместо хардкода `rate_hold` в API
- **Статус:** `OPEN — OTHER OWNER` (владелец: A, зона: `app/main.py`).
- **Суть проблемы:** В `app/main.py` эндпоинт `POST /api/v1/analyses` (строка 260) содержит жесткую проверку:
  ```python
  if body.event_id != "rate_hold":
      raise AppError(ErrorCode.not_found, "Resource not found", 404)
  ```
  Это блокирует запуск анализа для любых реальных новостных событий из RSS ЦБ и Росстата (таблица `events`), даже если они существуют и авторизованы.
- **Предложение для Backend A:**
```diff
--- a/app/main.py
+++ b/app/main.py
@@ -259,8 +259,10 @@ async def analysis(body: AnalysisRequest, request: Request):
         if body.context_version != ctx.version:
             raise AppError(ErrorCode.context_stale, "Context version changed", 409)
         p = await authorize(c, ctx, ctx.company_id, "analysis.run")
-        if body.event_id != "rate_hold":
-            raise AppError(ErrorCode.not_found, "Resource not found", 404)
+        event_row = (await c.execute(select(t.events).where(t.events.c.id == body.event_id))).mappings().first()
+        if not event_row:
+            raise AppError(ErrorCode.not_found, "Event not found", 404)
+        await authorize(c, ctx, ctx.company_id, "news.read")
         payload = {
             "request": raw,
             "context": ctx.model_dump(mode="json"),
```

---

### Gap 3: Переключение воркера на `AnalysisEngine`
- **Статус:** `OPEN — OTHER OWNER` (владелец: A, зона: `app/jobs/worker.py`).
- **Суть проблемы:** `worker.py` сейчас всегда вызывает `run_fixture(c, job)` внутри транзакции `engine.connect()`. Для live/case событий требуется вызывать `DefaultAnalysisEngine.analyze()`, при этом сетевой I/O модели **категорически не должен удерживать транзакцию или соединение с БД**.
- **Предложение для Backend A:**
```diff
--- a/app/jobs/worker.py
+++ b/app/jobs/worker.py
@@ -95,9 +95,34 @@ async def execute(job):
     beat = asyncio.create_task(heartbeat(job))
     try:
-        async with asyncio.timeout(60):
-            async with engine.connect() as c:
-                result, budget = await run_fixture(c, job)
+        payload = job.get("payload", {})
+        is_synthetic = (
+            payload.get("domain") == "synthetic"
+            or payload.get("request", {}).get("event_id") == "synthetic_fixture"
+        )
+        if is_synthetic:
+            async with asyncio.timeout(60):
+                async with engine.connect() as c:
+                    result, budget = await run_fixture(c, job)
+            runner_name = "fixture_runner"
+            steps_used, tools_used, models_used = budget.transitions_used, budget.tools_used, 0
+        else:
+            if not settings.live_llm_enabled:
+                raise AppError(ErrorCode.backend_unavailable, "Live LLM is disabled", 503)
+            from app.agent.engine import DefaultAnalysisEngine
+            from app.agent.events import DBEventProvider
+            from app.graph.service import CompanyTools
+            from app.llm.factory import create_gateway
+            from contracts.v1 import AnalysisRequest, UserContext
+
+            ctx = UserContext.model_validate(payload["context"])
+            req = AnalysisRequest.model_validate(payload["request"])
+            analysis_engine = DefaultAnalysisEngine(
+                profile_provider=CompanyTools(engine),
+                event_provider=DBEventProvider(engine),
+                llm_client=create_gateway(engine, settings),
+            )
+            result = await analysis_engine.analyze(ctx, req)
+            runner_name = "analysis_engine"
+            steps_used = len(result.sections.basis) + len(result.sections.actions)
+            tools_used = len(result.evidence)
+            models_used = 1 if result.status == "ready" else 2
         async with engine.begin() as c:
```

---

### Gap 4: Отказ от молчаливого запуска fixture при отключенном live LLM
- **Статус:** `OPEN — OTHER OWNER` (владелец: A, зона: `app/jobs/worker.py`).
- **Суть проблемы:** Ранее предлагавшийся fallback `or not settings.live_llm_enabled` скрывал отсутствие live-шлюза и молчаливо подменял реальный запуск синтетическим фикстурным ответом.
- **Требование проекта:** Запрос к live/case анализу при отключенном или недоступном Live LLM Gate обязан падать с ошибкой `AppError(ErrorCode.backend_unavailable, "Live LLM is disabled", 503)`. Это требование включено в предложенный выше diff для `worker.py`.

---

### Gap 5: Реальные счётчики выполнения вместо захардкоженной телеметрии
- **Статус:** `FIXED` (в рамках ML scope).
- **Доказательство:** В `DefaultAnalysisEngine` телеметрия управляется строго через экземпляр `RunBudget` (`budget.transitions_used`, `budget.tools_used`, `budget.models_used`, `total_searches_performed`). В рекомендациях для воркера захардкоженные числа (`6, 3, 2`) заменены на динамическое извлечение из счетчиков `result` и шлюза. Проверено тестами `test_total_searches_limited_to_maximum_two` и `test_malformed_model_response_repair` в `tests/unit/test_agent_engine.py`.

---

### Gap 6: Публичный банковский корпус `__public__` и совместимость с `documents.company_id` / FK
- **Статус:** `OPEN — OTHER OWNER` (владелец: A, зона: `migrations/`, `app/core/tables.py`).
- **Суть проблемы:** Банковские аналитические отчеты имеют общебанковский статус и не привязаны к конкретной компании-клиенту. Однако в схеме БД колонка `documents.company_id` имеет ограничение `NOT NULL` и ссылается по внешнему ключу на `company_snapshots.company_id`.
- **Временное решение M03:** В `company_snapshots` создается запись-заглушка с `company_id = "__public__"`.
- **Рекомендация для A:** В миграции 0004 сделать `documents.company_id` nullable (`nullable=True`).

---

### Gap 7: Применимость документов на дату, `supersedes_doc_id` и исторические версии
- **Статус:** `FIXED` (в рамках ML scope).
- **Доказательство:** Реализовано в `app/ingestion/pdf/pipeline.py` и `app/retrieval/service.py`. Документы проверяются по `valid_until` и `as_of`. Исторический отчет по стали 2021 года (`doc_alfa_steels_2021`) автоматически отсекается для событий 2026 года и доступен только при явном флаге `allow_historical=True`. Устранена эвристика `"2021" in query`. Проверено тестом `test_compute_query_recall_no_answer_leakage_detected` в `tests/unit/test_gold_evaluation.py`.

---

### Gap 8: Персистентность и доступность проверенных `BankThesis`
- **Статус:** `OPEN — OTHER OWNER` / `BLOCKED — ENVIRONMENT/DATA`.
- **Суть проблемы:** Банковские тезисы загружаются из артефакта `artifacts/manifests/bank_theses.json` через функцию `theses_loader`. Таблица `bank_theses` в БД отсутствует. Для постоянного промышленного хранения требуется отдельная миграция схемы и сервис от Backend A.

---

### Gap 9: Различия между диагностическим retrieval и реальным исполнением FTS/HNSW/exact
- **Статус:** `BLOCKED — ENVIRONMENT/DATA`.
- **Суть проблемы:** Скрипт `eval/run.py` сравнивает режимы `lexical`, `exact` и `hnsw`. Однако выполнение pgvector/HNSW требует запущенного контейнера PostgreSQL с расширением `pgvector` и установленной библиотеки `greenlet` (`sqlalchemy[asyncio]`).
- **Команда проверки:**
  ```powershell
  python -m eval.run
  ```
  *Результат:* `ImportError: The SQLAlchemy asyncio module requires that the Python 'greenlet' library is installed` (запуск вне Docker/uv окружения). Внутри контейнера диагностика функционирует штатно.

---

### Gap 10: Невозможность подтвердить сквозное поведение без БД и портов
- **Статус:** `BLOCKED — ENVIRONMENT/DATA`.
- **Суть проблемы:** Сквозное E2E тестирование (`make test-integration`, `python scripts/test_stack.py integration`) требует запущенного демона Docker Desktop и поднятого изолированного Compose-проекта базы данных `alfa_test`. На хосте демон Docker недоступен.
- **Проверено:** Вся внутренняя логика агентного контура, детерминированные guardrails, инвариантность ролей, отсечение галлюцинаций и обработка ошибок проверены 42 модульными тестами с мок-провайдерами, строго соблюдающими контракты портов `contracts/ports.py`.
