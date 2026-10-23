# B-next: UI и CBR RSS/article

Цель: шесть читаемых секций, evidence/source drawer, follow-up, partial/error/saved states; реальный бесплатный CBR adapter.
Inputs: contracts 1.1.0, contracts/openapi.json, API_EXAMPLES.md, synthetic server; W12–W19.
Зависимости: A для новых source/version endpoint полей; M для итоговой semantics. UI можно делать независимо сейчас.
Allowed: app/web/, app/ingestion/news/, tests/e2e/, source fixtures и docs.
Запрещено самовольно менять contracts/, migrations/, uv.lock, grants/budget и ML internals.
Output: доступный UI, маркировки investment/AI/synthetic/time/saved, parser RSS+article с dates/version/correction.
Done: source content не исполняется, SSRF/redirect/size/content-type gates; обновление источника версионируется;
два actor изолированы; time-to-first-insight измерен отдельно от качества; bank attribution не выдумана.
Существующие проверки: `make test-e2e`, `make export-contracts`.
B02: CLI `make news-update` / `make news-status`, DB probe `make news-smoke`.
ЦБ подтверждён сетью/изолированным импортом; live Росстата ждёт исправления TLS-цепочки.
Fixtures и проверки — [handoff B02](../handoffs/B02.md). UI B03 остаётся отдельной задачей.
Handoff: B→M Event/Document contracts, correction cases; B→A изменяемые API поля; HTTP/browser отчет с фактическим запуском.
