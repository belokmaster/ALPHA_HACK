# ADR-001: исполняемая основа и граница synthetic

Дата: 21.10.2026. Решение принято для bootstrap; результаты исполнения проверяются отдельно. Владельцы: A — contracts/storage/security, B — UI/integration, M — semantic engines.

Полный недельный план сохраняется, но первая поставка должна доказать рабочий путь без внешнего AI, весов и кейсовых raw данных. Схемы без worker/persistence не удовлетворяют этой цели; готовый fixture текст не доказывает качество RAG.

Используем Python 3.12.14, uv, FastAPI/Pydantic v2/pydantic-settings, SQLAlchemy 2/Alembic, asyncpg/PostgreSQL 17, Jinja2 и простой JavaScript. DB queue с transactional claim и lease выполняется отдельным worker из того же image. Redis/Celery, frontend toolchain, HTMX, heavyweight ML/OCR в foundation не добавляются. Зависимости фиксируются в uv.lock; фактические версии и лицензии описывает интегратор.

Исполняемые DTO — `contracts/v1.py`, schema 1.0.0; порты — `contracts/ports.py`: ProfileProvider, RelationsProvider, EventProvider, EvidenceRetriever, AnalysisEngine, LLMClient. Строковые ошибки и enum берутся из кода. Эскизы TypeScript/HTTP недельного плана не являются второй схемой. Изменение общего контракта согласует A с владельцем потребителя, затем меняются artifact exports/examples и targeted checks.

Fixture runner читает только synthetic sources и зарегистрированные scenario IDs. Он делает настоящие scope/time checks, сохраняет trace/result и проходит те же ownership/idempotency/queue границы. Выдача: domain=synthetic, delivery.mode=fixture, model_id=null, usage_status=not_called; произвольный follow-up честно ограничен fixture_only. Ни готовая строка, ни model_id=null не называются живой агентностью. Настоящие adapters отсутствуют до B/M задач; отсутствующий backend возвращает типизированную ошибку, не успешную пустую аналитику.

По W10 LangChain/LangGraph разрешены организаторами. Собственный Python controller выбран командой для явных limits/checkpoints и малого объема. Это выбор зависимости, не внешнее правило. По W11 local embeddings разрешены, но E5 не скачан и не протестирован. Будущий AnalysisEngine ограничен 12 transitions, 6 tools, 4 model calls вместе с fallback/repair, одним repeated search, 60 секундами и 24k input/4k output tokens.

Live LLM по умолчанию выключен. `https://changellenge.up.railway.app/v1` и model IDs берутся из предоставленной инструкции, не заменяются другим endpoint. Primary `deepseek/deepseek-v4.1-flash`, reserve `qwen/qwen3.6-35b-a3b` — выбор конфигурации, доступность и тариф не измерены. Общий ledger: 450 ₽ internal / 500 ₽ provider, reset 03:00 Europe/Moscow. Unknown tariff блокирует вызов; uncertain outcome удерживает резерв. Future SDK transport использует max_retries=0 и единственный контроллер попыток; reserve не расширяет run limits. Synthetic not_called не записывается как реальная бесплатная генерация.

Следствие: bootstrap воспроизводим без ключей и ML assets, команда получает настоящую точку интеграции. Приемка adaptive planner, verifier, bank corpus, live tariff и качества остается отдельной; она не закрывается foundation тестами.
