UV ?= uv
COMPOSE ?= docker compose
RUN = $(UV) run --frozen
.PHONY: bootstrap up down logs migrate seed lint typecheck test test-integration test-e2e check export-contracts audit-inputs import-case test-graph
bootstrap:
	python3 scripts/bootstrap_env.py
	$(COMPOSE) build app
	$(COMPOSE) up -d
up:
	$(COMPOSE) up -d
down:
	$(COMPOSE) down
logs:
	$(COMPOSE) logs --tail=100
migrate:
	$(COMPOSE) run --rm migrate
seed:
	$(COMPOSE) run --rm seed
lint:
	$(RUN) ruff check .
typecheck:
	$(RUN) mypy
test:
	$(RUN) pytest tests/unit tests/contracts
test-integration:
	$(RUN) python scripts/test_stack.py integration
test-e2e:
	$(RUN) python scripts/test_stack.py e2e
check: lint typecheck test test-web
	$(RUN) python scripts/export_contracts.py --check
	$(MAKE) test-integration
	$(MAKE) test-e2e
export-contracts:
	$(RUN) python scripts/export_contracts.py
	$(RUN) python scripts/export_contracts.py --check
audit-inputs:
	$(RUN) python -m app.ingestion.csv.audit --input-dir "$(INPUT_DIR)" --output artifacts/input-audit.json

# Audit/manifest only unless APPLY=--apply is explicitly provided. Never grants access.
import-case:
	$(RUN) python -m app.ingestion.csv.importer --input-dir "$(INPUT_DIR)" $(APPLY)
test-graph: test-integration

# Explicit paid opt-in; never part of check/bootstrap. Requires configured key/tariffs.
.PHONY: model-smoke
model-smoke:
	$(RUN) python -m scripts.model_smoke $(SMOKE_ARGS)

# Optional offline ML runner. Downloads are explicit; never part of bootstrap/check.
.PHONY: validate-gold test-pdf test-retrieval ml-prepare ml-image import-pdf eval-retrieval
validate-gold:
	$(RUN) python -m eval.validate --acceptance
test-pdf:
	$(RUN) pytest tests/unit/test_pdf_pipeline.py -q
test-retrieval:
	$(RUN) pytest tests/unit/test_retrieval_unit.py tests/unit/test_gold_evaluation.py -q
ml-prepare:
	$(UV) run --frozen --extra ml python -m scripts.prepare_e5 --download
ml-image:
	$(COMPOSE) build app
	docker build -f src/Dockerfile.ml -t alfa-ml:local .
import-pdf:
	$(UV) run --frozen --extra ml python -m app.ingestion.pdf $(PDF_ARGS)
eval-retrieval:
	$(UV) run --frozen --extra ml python -m eval.run

# Explicit free network fetch; never part of bootstrap/check.
.PHONY: news-update news-status
news-update:
	$(RUN) python -m app.ingestion.news update $(NEWS_ARGS)
news-status:
	$(RUN) python -m app.ingestion.news status

.PHONY: news-smoke
news-smoke:
	$(RUN) python -m scripts.news_smoke $(NEWS_ARGS)

# Dependency-free JavaScript controller/DOM tests; Node.js 22+ for development/CI only.
.PHONY: test-web
test-web:
	node tests/web/ui.test.cjs
	$(RUN) python -m scripts.validate_ui_fixtures

.PHONY: test-agent
test-agent:
	$(RUN) pytest tests/unit/test_agent_engine.py tests/unit/test_analysis_engine.py -q

.PHONY: llm-config
LLM_MODE ?= adaptive
llm-config:
	$(RUN) python -m scripts.llm_config --mode "$(LLM_MODE)"
