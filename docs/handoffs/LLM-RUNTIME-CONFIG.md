# Единая передача: LLM runtime, gateway, стоимость и live G1

23.10.2026. Ветка `task/llm-runtime-config`. Этот файл заменяет отдельные передачи
runtime / first live / merge readiness / cost calibration / output cap. Исторические
аудиты и машинные receipts сохранены; прежние handoff удалены, их содержимое доступно
в истории Git. Main, чужая `.gitignore`, рабочие данные и ledger не изменяются.

## Текущее решение

**После исправления блокера: GO с условиями для технического merge.**
Исходный HEAD исправления — `2e711fe`; итоговый commit определяется через
`git log -1 --format=%H -- app/llm/session.py migrations/versions/0007_session_authorization.py`.
Проверки и условия приведены в конце этого handoff. Browser пропущен по указанию
владельца; положительный browser-вердикт предыдущего аудита не подтверждён.

Все шесть моделей общей allowlist разрешены; отдельных разрешений по каждой модели
или endpoint нет. Два адреса: `https://changellenge.up.railway.app/v1` и
`https://api.alfahack-gateway26.ru/v1`. `LLM_BILLABLE_OUTPUT_CAP_CONFIRMED=true`
в существующем локальном launcher app/worker отражает принятое оператором поведение
общего gateway. Это не новая платная сессия. LIVE_LLM_ENABLED=false, evidence_only,
старый session_id закрыт. Defaults нового окружения остаются false.

Из кода удалён дополнительный список «подтверждённых model/endpoint». Остаются
общая allowlist, валидированные тарифы, model/stage pins, общий ledger/key gate,
лимиты run/session/day, ACL/temporal checks. Разрешение модели не означает, что её
качество или cap были независимо измерены. Непроверенные модели не выдаются за проверенные.

## Конфигурация и происхождение тарифов

`Settings` использует штатное environment, env_file=None; Compose передаёт .env.
Секреты только в environment. `config/llm-runtime.json` — единственный immutable
типизированный runtime; неизвестные поля и unsafe limits отклоняются.
App/worker/CLI читают одинаковые файлы. API закрепляет snapshot/hash при enqueue;
worker не меняет модели queued job из-за редактирования файла. Тарифы закреплены
отдельно по model/version/rates; multiplier входит в effective tariff version.
Изменение цены/коэффициента не подменяет прежний pin молча. Старые queued live jobs
с другим pricing pin должны остановиться; potentially paid запросы не повторять.

`config/llm-tariffs.json` содержит model_id, version, input/output rates, verified
и **один multiplier на модель**, сейчас 2.23. Вложенный cost_estimate и evidence из
конфига удалены. Loader принимает legacy evidence только для совместимости старых
fixture/конфигураций, не использует его как разрешение.

Источник базовых цен: сообщение организаторов, RUB за **1 миллион input/output**,
единица и порядок подтверждены владельцем 22.10.2026. Цены ориентировочные:

| Модель | Input | Output | Multiplier |
|---|---:|---:|---:|
| google/gemma-4-26b-a4b-it | 4.48 | 23 | 2.23 |
| z-ai/glm-5.3-flash | 5.10 | 17 | 2.23 |
| qwen/qwen3.6-35b-a3b | 5.33 | 74 | 2.23 |
| deepseek/deepseek-v4.1-flash | 10 | 21 | 2.23 |
| qwen/qwen3-vl-30b-a3b-instruct | 14.30 | 57.10 | 2.23 |
| moonshotai/kimi-k2.6 | 51 | 270 | 2.23 |

×2.23 получен экспериментально на DeepSeek: 26 вызовов сессии
live-adaptive-g1-20261023, external delta 1.099566479617320 ₽ / nominal0.492946 ₽.
Владелец принял округлённый коэффициент для оценки 23.10.2026. Остальным моделям
он задан временно без собственного замера. Причина расхождения цен не установлена.

Оценка: ceil_6((input×input_rate + output×output_rate)/1e6 × multiplier).
Tariff.estimate используется для quotes/reserve; Tariff.quote и usage settlement
сохраняют nominal cost. Внешний spend сверяется отдельно, не умножается повторно.
Reasoning — часть completion_tokens, не повторная надбавка. INFO llm_cost_estimate
различает nominal/estimated cost. Исторические строки ledger не пересчитываются.

## Режимы, лимиты и команды

Runtime llm-runtime-live-6: DeepSeek на plan/draft/verify, caps1000/1400/700,
reasoning.effort=none; transport/call/run=25/25/300 s. Hash:
c4739718208f0e818c09704a55d8bf8475303c843eb1096896a60693b4d77e9d.
Follow-up наследует draft/verify из закреплённого runtime; planner не вызывает.
Adaptive: plan → retrieval/refinement при необходимости → draft → verify, до3 calls.
Two-call smoke: code planning → draft → verify, до2; не проверка adaptive M4.
Run: 12 transitions/6 tools/1 refinement/24k input/4k output/≤5 ₽.
Session: общий счётчик всех jobs, ≤50 ₽/30 calls с headroom5 ₽; закрытие/смена суток
не создаёт разрешение заново. День: 450/500 ₽, reset03:00 Europe/Moscow.
Risk reserve=max(1 ₽,4×оценка). Действует первое достигнутое ограничение.
SDK/HTTP retries=0, никакого automatic repair/fallback. Uncertain удерживает gate/reserve.

Для defaults nominal envelope adaptive0.305100 ₽, оценка0.680373 ₽;
two-call nominal0.204100 ₽, оценка0.455143 ₽. Дополнительный risk reserve нынешней
сессии — до3/2 ₽ соответственно. Оценка не гарантирует точный счёт провайдера.

```sh
python -m scripts.live_session observe  # бесплатные numeric account fields
python -m scripts.live_session status   # read-only состояние текущего session_id
# close — явное закрытие разрешения; open/resume не выполняются при startup.
make llm-config
make llm-config LLM_MODE=two_call_smoke
make lint typecheck test export-contracts
make test-integration
make test-e2e
make test-web
```

Для смены модели изменить stages.<plan|draft|verify>.model_id и version runtime JSON,
проверить llm-config; отдельного model approval нет. Synthetic/controlled работают
без ключа; controlled — только demo/alfa_test/no-key/no-live с MockTransport.
Контракты DTO1.1 не менялись. Один Alembic head0007: 0005 runtime_snapshot,
0006 durable sessions/external expenses, 0007 explicit authorization identity.
Upgrade0004→0007 на изолированной pg_dump/restore копии сохранил все прежние строки,
analysis/uncertain/gate и счётчики closed-сессии. Подробности downgrade ниже.

## Счётчики ключа и задержка обновления

По уточнению владельца spend — текущий день, total_spend — история ключа.
account_spend теперь использует **total_spend для session deltas**, daily balance
остаётся max_budget−spend. Их равенство больше не требуется: дневной reset не выглядит
как списание/неоднозначность и не блокирует сверку. Регрессия проверяет spend0 при
total1.10600500492482. Исторический расход не переносится в новый бюджетный день.

Задержка /key/info учитывается прежним bounded polling: до15 наблюдений через4 s,
общий предел70 s. Неизвестное списание или недоступность после ожидания удерживает
gate и блокирует новые генерации. Нельзя угадывать zero cost или повторять модельный
запрос через другой gateway. Ключ не логируется; only curated numeric /key/info fields.

## Фактический live результат и cap

Исходный main79a95e4; первая live ветка cdaf131, завершённый код7ea773d,
передача488f70c, merge check/fixes f0d9be9/92d6f32, calibration61c21ef,
cap evidence05fde8f, временный scoped guard9d970c5 (снят текущим решением владельца).

Сессия live-adaptive-g1-20261023 закрыта 22.10.2026 23:08:20 UTC,
26/30 paid calls. Baseline0.006438525307500, total после1.106005004924820 ₽;
дельта1.099566479617320 ₽, nominal0.492946 ₽. Reserve0, uncertain0, gate свободен.
Два более ранних прямых probe nominal0.002446 / external0.0064385253075 ₽ учтены
как external expense без вымышленных model_calls. Их отношение ≈2.63 не новый тариф.
Все дальнейшие проходы документации/упрощения — **0 paid calls**. Разрешённые позже
30 ₽ на необходимые cap-тесты не расходовались: свидетельства уже существовали.

Первоначальный gateway-output-probe: 64→31 и128→35 completion, finish=stop — не
проверка отсечения. Позже реальные A2/A4/A5: caps450/1000/900, completion=reasoning=
cap, content0, finish=length. Ledger и первичные llm_reserved/provider_summary
совпали; все26 completion<=output_bound. Отсечение reasoning проверено эмпирически
на DeepSeek/API, универсальная гарантия всего биллинга из этого не выводится.

Готовые сохранённые результаты (localhost18089, demo alice/U_15728):
A9 52db7f5a-a881-4e49-ad79-e8fb3d379c68 — настоящий adaptive, три стадии;
B5 fb455c43-06cd-449c-99c7-32c7610e6cc9 — live follow-up с user_declared fixed до2027.
Оба partial, original_mode=live; evidence/page/PDF открываются, bob получает404.
B5: последствия удалены, осталось условное действие. Fixed guard консервативно
удаляет все consequences, в том числе возможные после fixed_until; UI точную причину
пустоты не поясняет. Bank_view пуст, OCR unverified/quality_limited. Не human_verified.
Финансовое качество/gold и обобщение на G2/G3 не доказаны. Browser запрещён политикой:
desktop/mobile screenshots/DOM не заменяются HTTP E2E.

## Доказательства и проверки

Tracked receipts: docs/reviews/LLM-RUNTIME-CONFIG, LIVE-LLM-FIRST-RUN,
LIVE-LLM-ADAPTIVE. Исторические аудиты не переписаны. Private пакеты вне Git:
llm-runtime-merge-readiness-20261023-0903, llm-cost-calibration-20261023,
gateway-cap-evidence-20261023, gateway-cap-enable-20261023 (в общем каталоге).
В них полные stdout/exit/commands, первичные log hashes, read-only ledger и numeric
account receipts; без ключей/cookies/CSRF/prompts/internal reasoning.

Предыдущий полный check scoped guard:286 unit/contracts,97 PG,2 E2E,14 UI, exit0.
Текущие результаты упрощения — пакет llm-runtime-simplified-20261023, make-check.log
и checks.json. Сети PG/E2E internal=true, key blank; model HTTP отсутствует.
Пересекающиеся прогоны не складываются. Main/push/deployment/working ledger не менялись.

Текущий полный `make check export-contracts`: **293 unit/contracts, 97 PostgreSQL,
2 HTTP E2E, 14 Node**, 5 UI DTO states; Ruff/mypy/export — exit0. Платных вызовов0.
Предыдущий вердикт до сверки нового аудита: GO с условиями. Он пересмотрен ниже.
Main/merge-base проверены:79a95e4; main и чужая .gitignore не менялись.


## Независимая сверка аудита 23.10.2026: исходный вердикт NO-GO (до исправления)

Проверен полный `docs/audits/LLM-RUNTIME-3d8c672-gemini.md` и его частные артефакты
llm-runtime-gemini-audit-20261023-1350. Фактический HEAD943c81c; отличие от аудированного
3d8c672 — только .gitignore, выводы по коду применимы. Дерево исходно чистое.
Аудит не переписан; его положительные вердикты не приняты без доказательств.

**Задача к сдаче — NO-GO. Merge как завершённого live-этапа — NO-GO.**
Наличие fast-forward и проходящие тесты не закрывают функциональный блокер.

| Находка | Независимая проверка | Решение |
|---|---|---|
| Новая явная авторизация на следующий день невозможна | Отдельный tmpfs PG, fake account, без HTTP: open → close → clock+day → open нового id приводит к UniqueViolation llm_sessions_scope_id_risk_acceptance_key | Подтверждённый блокер сдачи/merge завершённого этапа |
| Browser «ГОТОВА» | 04_analysis_a9_six_sections / 06_followup_b5_thread / 05_evidence_drawer_open / 07_tab_regulatory_news / 08_tab_status_sources имеют один SHA256. Изображение просмотрено: UI-fixtures, demo_a, ошибка доступа, лента; анализа и drawer нет. Mobile screenshot отсутствует | Положительный browser-вердикт не подтверждён. Это не доказывает поломку UI; требуются настоящие доказательства |
| Текущий runtime якобы равен HEAD | Read-only health: code_sha9d970c5, live=false, schema0006, runtime hash ожидаемый. Контейнер стартовал 13:44:01 Europe/Moscow. Read-only bind с текущими файлами не доказывает версию уже импортированных модулей | Требуется согласовать/проверить SHA процессов перед повторной live-приёмкой |
| A9 plan якобы length при reasoning cap150 | Ledger: output_bound1000/output_tokens150; сохранённая трасса plan_validated_local_search_only | Утверждение аудита неверно; 150 — расход, не cap. Сам исторический adaptive результат этим не опровергнут |
| B5 якобы доказывает устранение рисков фиксированной ставкой | Guard удаляет все consequences по user_declared fixed, независимо от горизонта/доказанности эффекта | Это консервативная политика фильтрации, не финансовое доказательство отсутствия рисков |
| Bank_view пуст из-за отсутствия материалов | Diagnosis quality_limited, OCR unverified и неизвестная availability уже описаны выше | Нельзя подменять исключение по качеству/применимости отсутствием источников |

Независимый acceptance-тест воспроизводит ошибку за0.79s (exit1), migration/up/down
exit0; первый запуск harness остановился до теста из-за отсутствия explicit asyncio
mode, после исправления harness дефект воспроизведён. Пакет вне Git:
`../llm-runtime-audit-review-20261023/`: test_acceptance.py, reproduction.log,
commands.json, screenshots.json, verdict.json. Существующая БД, ledger/gate и сессии
не менялись; новых model calls0. Тестовый scope существует только в isolated fake DB.

Прошлые 293/97/2/14 подтверждены собственными полными логами, а не только JSON аудитора;
код с тех пор не менялся, поэтому полный набор без необходимости не повторялся.
Однако test_session_does_not_resume_on_day_reset_and_id_cannot_bypass_day ожидает
IntegrityError даже на следующий день: такой зелёный тест маскирует конфликт между
антиобходной политикой и требованием нового явного разрешения.

До принятия: отделить durable identity нового авторизованного лимита от постоянного
текста риска; проверить повторную авторизацию, сохранение старой closed-сессии,
конкуренцию, перезапуск, смену суток и невозможность обойти исчерпанное разрешение
новым session_id. Нужна аддитивная миграция с сохранением записей. Просто убрать
unique(scope,risk) или добавить timestamp к тексту недостаточно: это ослабит защиту.
Рекомендованная аудитором unique(scope,session_id) избыточна при уже глобальном PK id
и сама по себе не решает связь с явной авторизацией.

Текущий проход — независимая приёмка, код/схема не менялись. Требуются исправление
и повторная приёмка указанного поведения, затем согласованный runtime и честные
browser-свидетельства (либо явное ограничение browser для технического merge).
PDF/gold и качество остаются отдельными ограничениями, не поводом подменять результаты.


## Исправление блокера и повторная приёмка

**Итог: GO с условиями для merge; подтверждённых блокеров кода не осталось.**
Предыдущий NO-GO выше сохранён как история проверки, исправление не меняет аудит.

| Дефект → причина | Минимальное исправление | Проверка |
|---|---|---|
| Новое разрешение после закрытия старого невозможно: постоянный risk_acceptance уникален на ключ | Отдельный обязательный authorization_id для нового разрешения; уникальность key+authorization, одна open/blocked сессия на ключ, проверка под прежним key gate lock | Независимый acceptance: до исправления UniqueViolation, после exit0; next-day, повтор ID, неизменность старых counters/state, конкуренция двух open |
| 0006 использовала изменяемую текущую metadata | Зафиксирована прежняя схема 0006; добавлена аддитивная 0007 | Полный fresh test bootstrap и upgrade копии main0004→0006→0007; один head0007 |
| Rollback мог потерять новое разрешение | Downgrade0007 отказывается при любой новой authorization или нескольких сессиях ключа | Негативные проверки с одной новой записью и с историей; записи сохраняются |

authorization_id — ссылка оператора на **новое явное разрешение владельца**, а не
случайный UUID, timestamp или новый session_id для обхода лимита. CLI требует её
вместе с подтверждением риска и baseline; startup её не создаёт. Прежние записи
получают зарезервированный `legacy:<session_id>`, counters/spend/state не меняются.
Один approval нельзя повторно использовать даже после смены дня. Закрытая сессия
не открывается заново; незакрытая/blocked или reserve не допускают новое разрешение.
Граница доверия прежняя: оператор с доступом к CLI обязан иметь разрешение владельца;
криптографический сервис выдачи разрешений в эту задачу не вводился.

Только после нового явного разрешения, бесплатной сверки baseline и миграции:
```sh
python -m scripts.live_session open \
  --authorization-id OWNER_APPROVAL_REFERENCE \
  --expected-spend HISTORICAL_TOTAL_SPEND \
  --max-calls AUTHORIZED_CALL_LIMIT --budget-rub AUTHORIZED_RUB_LIMIT \
  --accept-unconfirmed-cap-and-price
```
Это шаблон, **не выполненная команда**. session_id задаётся штатным Settings;
автоматически новый ID не генерируется. observe/status остаются read-only.

Финальный `make check export-contracts` — exit0: **293 unit/contract, 101 PostgreSQL
integration, 2 HTTP E2E, 14 Node**, 5 DTO states; Ruff/mypy/export exit0.
Пересекающиеся промежуточные прогоны не суммируются. DB/E2E Compose network internal,
live=false, ключ пуст; host Python socket guard. Платных запросов **0**.

Все новые логи вне Git: `../llm-session-remediation-20261023/`.

| Команда | Exit | Артефакт |
|---|---:|---|
| make check export-contracts через check.py/compose.py с изолированными project | 0 | make-check.log, checks.json |
| python3 upgrade.py: восстановить main0004 fixture dump, upgrade0006, добавить legacy closed, upgrade head | 0 | upgrade-commands.json, copy-0006.log, copy-0007.log |
| Сравнить все старые public table rows до/после (кроме нового authorization_id и alembic_version) | 0 | upgrade-receipt.json, copy-before.log, copy-after.log |
| Независимый pytest test_acceptance.py | 0 | acceptance-green.log; исходный red в ../llm-runtime-audit-review-20261023/reproduction.log |
| alembic heads | 0 | heads.log:0007 |

Копия содержит main Analysis, uncertain reservation, занятый key gate и добавленную
legacy closed-сессию с26 вызовами. Сохранность всех прежних строк проверена точным
сравнением, а не только row counts. Новые tests включают restart/day-reset/uncertain
регрессии прежнего набора; миграционный downgrade с новыми approvals намеренно
forward-only. Рабочую схему, ledger, gates и сессию не меняли.

Условия после merge / до запуска обновлённого приложения:
- Штатно применить `alembic upgrade head` (0007) с резервной копией БД; затем app и
  отдельный worker перезапустить на одном SHA и runtime hash. Рабочая БД остаётся0006,
  наблюдавшиеся процессы9d970c5 не выдаются за проверку итогового кода.
- Browser desktop/mobile пропущен по разрешению владельца. HTTP E2E не заменяет его;
  ошибочные screenshots аудитора не используются как доказательство A9/B5/drawer.
- Старое разрешение закрыто; новые платные вызовы в этом проходе не выполнялись,
  новый внешний spend не запрашивался. Исторический total1.106005004924820 ₽ — прежнее
  наблюдение, не новая сверка API.
- ×2.23 — оценочный экспериментальный множитель DeepSeek, не подтверждённый тариф
  всех моделей; billable output cap не универсально доказан. Требования нового
  разрешения, общий ledger/key gate и остановка uncertain сохранены.
- Bank_view/OCR quality_limited, независимый PDF/gold и точное UI-пояснение пустых
  consequences при fixed остаются ограничениями качества/демонстрации; guard не менялся.

Main, чужие worktree, исторический аудит и пакет ручной сверки не изменялись;
merge/push/deployment не выполнялись. Изменения готовятся только в task/llm-runtime-config.
