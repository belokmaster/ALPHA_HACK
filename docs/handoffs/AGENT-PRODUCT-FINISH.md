# Агент: содержательный ответ и ограниченное уточнение

23.10.2026. Ветка task/agent-product-finish, база origin/main 275bee40ee4e2a0211ba7f646906c7c1f4e30bbd.
Итоговый код: `git log -1 --format=%H -- app/agent/engine.py`. Main и парсер не менялись.

## Результат и границы

Это законченный ограниченный проход по контроллеру, не универсальный tool-calling агент.
Генерация остаётся plan → локальный поиск/один refinement → draft → verify, максимум три
модельных вызова. План дополнительно выбирает clarify: без поиска и draft/verify возвращает
существенные неизвестные и вопросы, с явной причиной остановки. Follow-up сохраняет pinned
источники и отдельную проверку выводов; произвольный доступ к HTTP/SQL не вводился.

| Подтверждённая причина | Исправление | Проверка |
|---|---|---|
| Три proposals на весь ответ | До шести; verdict indices 0–5, каждый уникальный; unsupported удаляется | Шесть результатов, один отвергнутый не попадает в итог |
| Только поисковый план | next_action search/clarify; clarify останавливает платную последовательность после plan | План уточнения: только один вызов, ноль поисков, пустые consequences и понятный notice |
| bank_view новой карточки всегда пуст | Отдельная детерминированная точная цитата проверенного forecast/opinion с датой; банк не требует feature компании, персональный эффект требует | Verified/unverified unit и PostgreSQL; future исключается |
| Все consequences запрещены при fixed | after_fixed_until допускается только при явном ISO fixed_until и условии «после окончания фиксации»; затем semantic verify | Нет даты/невалидная дата/current отклоняются, условный последующий период проходит code gate |
| Непонятная причина пустого fixed | Notice объясняет ограничение периода | Сохраняется вместе с анализом |
| Требование подтверждения оплаты API | Проверено: явная SessionBudget уже допускает принятый риск без billable_output_cap_confirmed; отдельных model approvals нет. Исправлен текст отказа для standalone | Регрессии gateway/session/preflight; дополнительных блокировок не добавлено |

Позиция банка — самостоятельное атрибутированное мнение, **не персональная рекомендация**.
В draft по-прежнему только персональные условные гипотезы с feature IDs; внешнее событие
и банковская цитата создаются отдельно из источников. Модель не может сама объявить OCR
проверенным или приписать своё предложение банку. Непроверенный материал остаётся
bank_context с quality_limited; конфликт не становится bank_view. Числа в точной цитате
не пересказываются моделью. Дата публикации и неизвестный горизонт видны в conditions.

Парсерный handoff: сохранить существующие Evidence quality/value_kind/span и неизменяемые
версии Document/SourceVersion. Агентская/VLM сверка не превращается в human_verified.
Только реально принятые verified evidence проходят в bank_view. Для проверки нового корпуса
нужны его завершённый импорт и переиндексация; их в этой ветке нет. Переданный pipeline
BankThesis не требует отдельного хранилища для этого среза: позиция восстанавливается из
доступного типизированного evidence с повторной проверкой ACL/времени в существующем provider.

Для API follow-up явные поля пользователя:
`contract_rate_type=fixed`, `fixed_until=2027-12-31` — user_declared, не факты договора.
Свободный текст не превращается этим исправлением в автоматически подтверждённую дату.
Модель должна явно выбрать effect_window=after_fixed_until и условие последующего периода;
это лишь допуск к semantic verify, не доказательство экономического эффекта.

## Лимиты и совместимость

Публичные DTO1.1, Alembic head0007, общий ledger/key gate, модельные defaults, caps1000/1400/700,
бюджет5 ₽/run, session/day limits, ACL/temporal/pinned context неизменны. Шесть proposals —
максимум, а не обязательство заполнять секции. Увеличение caps, retry, repair и fallback
не включены. Новый next_action имеет default search для прежних fixtures.
Вывод JSON проверяется строго; расширены только внутренние Draft/Verification/AnalysisPlan.
Startup/test не открывает сессию. Неизвестная оценка тарифа останавливает сеть; наличие
настроенной оценки не означает точного подтверждения счёта провайдера.

## Проверки и воспроизведение

`make check export-contracts UV=/tmp/alfa-tools/bin/uv`;
Compose wrapper из пакета доказательств выбирает image alfa-agent-finish:local и internal=true.
Все модельные ответы управляемые, ключ пустой, live=false. Изолированные tmpfs PostgreSQL,
API и отдельный worker. Рабочая БД, активные разрешения и расходы не менялись.
Полные логи и receipt вне Git: ../agent-product-finish-20261023/check.log и receipt.json.
Целевые тесты: tests/unit/test_agent_engine.py и tests/integration/test_agent_evidence.py.
Браузер и качество live в этом проходе не проверялись. Платных запросов 0.

После интеграции парсера: финальный корпус → импорт/индекс → прежний API /api/v1/analyses →
worker → сохранённый результат → evidence/PDF → /api/v1/followups. До новой разрешённой
live-сессии нужен обычный бесплатный observe baseline. Закрытое разрешение не переиспользовать.
Новый корпус не переписывает старые analyses. Если bank_view пуст, проверять доступность,
quality/value_kind и дату конкретного evidence, а не отключать фильтры.

Финальный make check export-contracts: exit0, 301 unit/contract, 104 PostgreSQL integration,
2 HTTP E2E с отдельным worker, 14 Node UI и 5 DTO states. Ruff, mypy, export/check exit0.
Числа промежуточных прогонов не суммируются. Дополнительные PostgreSQL fixtures сначала
требовали исправления public storage sentinel и выбора реального BankSearch provider;
финальный прогон после исправления прошёл полностью. Стенд тестов удалён штатным down,
рабочие volumes не затронуты. Merge/push/deployment не выполнялись.
