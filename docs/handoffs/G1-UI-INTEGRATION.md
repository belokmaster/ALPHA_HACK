# G1 + UI: объединённая техническая версия

> Следующий проверенный продуктовый проход: [G1-UI-PRODUCT-FIXES.md](G1-UI-PRODUCT-FIXES.md), `task/g1-ui-product-fixes`, порт 18085. Здесь сохранена история объединения исходных веток и прежнего стенда.

22.10.2026. Ветка `task/g1-ui-integration`, worktree `project-repo-g1-ui`.
Это offline controlled demo с настоящими PostgreSQL/API/отдельным worker, PDF/E5,
двумя исходными CSV-профилями и сохранёнными реальными статьями B02. Платных обращений 0.
Качество настоящей LLM и экономических выводов этим проходом не доказано.

## Версии и сохранность

| Вход | Точный SHA |
|---|---|
| main / merge-base обеих задач | b5b4cce1cb2da7f010a33d56dde1a8f7343d0718 |
| Завершённый G1, включая F01–F03 и ограниченную live-команду | 4854ee9506b3eae474ce1eede39a8a48e2dc82b7 |
| UI | 3ebcaf3f119155c7620ac933acdf5e3557e1c059 |

Merge SHA: **8c5a004779f04e23d0eb27f3c05bdeea329320c6** (два parent — исходные G1/UI).
Дополнение provenance: **ea593858551d6e5cc55e2e82244182d91b5c6e89**.
Итоговый code HEAD: **7ecbcacd281be7b3dd5529f35b661f276440d4c7** — отдельный
request token drawer предотвращает подмену новой цитаты поздним ответом, старая
цитата очищается до загрузки. Регрессия сначала воспроизвела claim-a вместо claim-b,
после исправления проходит. Прежние ссылки на CSV provenance и source_version_id сохранены.
Следующий коммит фиксирует только результаты/передачу/снимки. Текущий полный HEAD: `git rev-parse task/g1-ui-integration`.

UI checkout был чистым. В исходном G1 остались незакоммиченные
`docs/audits/G1-agent-review.md`, `docs/reviews/G1-agent-review.md`; в main —
`docs/audits/G1-integration-3900cf1.md` и `docs/reviews/`. Эти чужие материалы не
переносились и не изменялись. Старые prunable worktree B02/M не ремонтировались.
Исходные HEAD/ветки/main сохранены. Reset, clean, stash, push, deployment не выполнялись.

UI перенесён обычным merge истории. Единственный файл, изменённый в обеих ветках,
и единственный текстовый конфликт — `app/web/templates/index.html`.
Сохранены оболочка UI, шесть секций, safe DOM, evidence drawer и явные UI-fixtures;
возвращены G1, открытие сохранённого анализа, все retrieval-кандидаты и ссылка на PDF.

Логические исправления:

- boolean `disabled=false` больше не создаёт HTML-атрибут, блокировавший кнопку;
- реальные case events доступны UI; synthetic runner остаётся отдельным режимом;
- `partial` — терминальный job с result_id; ошибки job читаются как APIError;
- fencing после каждого await POST/poll/result и при любой смене контекста;
  polling прекращается на 4xx. Скрываются старые карточки/drawer и стираются условия клиента;
- follow-up передаёт закреплённый context_version/conversation_id, показывает
  assumptions как user_declared; `fixture_only` — legacy поле DTO, не обещание режима G1;
- controlled/original_mode и OCR ограничения остаются видимыми после replay;
- новости загружают evidence по API, а не ищут его только в активном анализе;
- hash URL не создаёт ложную сессию/не включает fixtures; выход из fixtures требует
  настоящего входа, fixture context не переиспользуется для API;
- ссылки проходят проверку http/https, текст остаётся text nodes. ARIA вкладок обновляется;
- после сетевой неопределённости тот же POST payload использует прежний idempotency key.
  Автоматической повторной отправки запроса/repair/fallback нет.

HTTP-маршруты сохранены: `/api/v1/news/sources`, `/api/v1/followups`,
`/api/v1/analyses/{id}`, `/api/v1/evidence/{id}`, `/evidence/{id}`,
`/api/v1/sources/{document_id}/asset#page=N`. Альтернативных backend routes не добавлено.
UI JS/CSS включаются Jinja из соседних шаблонов; отдельного static mount не требуется.
DTO/экспорт/примеры, engine, prompts, retrieval, gateway, ACL, temporal checks, ledger,
миграции и Python dependencies не менялись. Один Alembic head: **0004**.

## Запуск

Из корня этого worktree, Docker Compose, Python 3.12/uv; для dev `make check`
нужен Node.js 22+ (без npm). Runtime API/worker Node не требует.
Секретный .env не нужен; `config/integration-g1-ui.env` содержит только тестовые настройки.

Входы не хранятся в Git: исходные девять PDF в `raw/pdf`, CSV
`raw/csv/df_Uid_data_hack.csv`, локальные веса по `models.lock`, pinned OCR caches
в `artifacts/ocr`, сохранённые B02 captures в `artifacts/integration/news`.
В этой рабочей копии входы скопированы без изменения из завершённого G1; код перенесён
через Git merge. Startup ничего не скачивает. Если входов нет — восстановить из
локальной передачи, не подменять синтетикой. Хеши проверяются импортом/manifest.

```bash
mkdir -p artifacts/g1-ui/ocr artifacts/g1-ui/integration/news
cp -n artifacts/ocr/*.json artifacts/g1-ui/ocr/
cp -n artifacts/integration/news/* artifacts/g1-ui/integration/news/
# Для краткости ниже shell-функция. Compose project отдельный; PG — tmpfs.
cg() { docker compose --env-file config/integration-g1-ui.env -f docker-compose.integration.yml -p alfa-g1-ui "$@"; }
cg build app
cg up -d app worker
cg run --rm --no-deps prepare -m app.ingestion.pdf --apply
cg run --rm --no-deps prepare -m scripts.prepare_integration --company U_15728 --company U_11496 --demo-grant alice --apply
cg run --rm --no-deps prepare -m scripts.replay_integration_news
```

Последняя команда не обращается в сеть: читает реальный RSS/HTML capture с проверкой
хешей и временем исходного наблюдения, учитывает конечный URL redirect Росстата.
При повторе нет новой версии. Статусы относятся к **сохранённой загрузке 22.10.2026**,
не к новой проверке доступности сети. Рабочая shared ledger-БД не использовалась.
В стенде LIVE_LLM_ENABLED=false, ключ пустой, transport controlled, worker read-only.

На этой машине Compose wrapper требует абсолютный путь к yml без host mount-префикса;
`make check` запускался с `COMPOSE_HOST_PATH_PREFIX=/run/host`, uv/cache в /tmp.
Это особенность среды, не обязательная конфигурация проекта.

Открыть **http://127.0.0.1:18084/** → Alice → U_15728 → CFO → snapshot_demo,
as_of `2026-09-14T00:00:00Z` → применить → исторический G1 → анализировать.
Текущие новости ЦБ/Росстата находятся в отдельной вкладке.
Для replay ввести ID в «Сохранённый анализ» после входа и выбора той же компании.
`?analysis=ID` лишь заполняет поле, автоматически не авторизует и не запускает модель.

## Проверки

Фактические результаты финального дерева приведены в дополнении ниже.

```bash
make check
# В controlled read-only worker, только тестовые fixtures/managed HTTP:
cg run --rm --no-deps worker /app/.venv/bin/python -m pytest tests/unit tests/contracts -q -p no:cacheprovider
# Дважды на одном PG/API/worker, без reset/restart между прогонами:
cg exec worker /app/.venv/bin/python -m pytest tests/scenarios/test_g1.py -q -s -p no:cacheprovider
cg exec worker /app/.venv/bin/python -m pytest tests/scenarios/test_g1.py -q -s -p no:cacheprovider
python -m scripts.review_g1 --verify
```

Сценарный cleanup требует init-time disposable marker, controlled/no-key и отсутствие
in-flight/unknown outcomes. Отклоняет рабочую БД; общий ledger не очищает. Timeout
uncertain не сбрасывался. Новые safety tests входят в PostgreSQL suite.
`make test-web` исполняет настоящий JS controller с управляемыми async/DOM объектами,
11 unit-проверок, затем валидирует JS fixtures настоящими Pydantic DTO 1.1.
Это отдельно от браузерного smoke.

Browser smoke выполнен против настоящего API, UI-fixtures выключены:
вход; реальная компания; историческое событие; POST queued → polling → partial;
replay; evidence/полный текст/PDF ссылка; follow-up; B02 RSS evidence и статусы;
company switch во время queued job с остановкой/возобновлением только нашего worker;
отсутствие старой карточки; strict_as_of → insufficient_evidence/evidence_only/not_called;
Bob → not_found при открытии анализа Alice. Screenshots в `artifacts/g1-ui/screenshots`.

PDF endpoint возвращает исходные байты с проверкой SHA и inline application/pdf,
ссылка содержит #page=1. Встроенный IAB PDF viewer на этой машине показал пустое тёмное
окно; попытка downloadMedia завершилась таймаутом: **визуальное открытие страницы PDF внутри этого браузера не подтверждено**.
Полный текст и защищённая страница evidence доступны; оригинал можно открыть/скачать
в обычном браузере с PDF viewer. Это ограничение браузерной проверки явно сохраняется.

## Пакет сверки и границы

Исходные аудиты и их вердикты не переписаны. Пакет a629820 сохранён побайтово:
`artifacts/integration/g1-review.zip`, SHA256
`70ee9e85b05080c02200ef7a0dc7a6ffd57a340af60a036b0cb1ba18d6591794`.
24 решения/комментария по закреплённой паре пусты, human_verified не выставлен.
Новый browser demo не подменяет эту историческую пару.
Реальный E5 проверен импортом; OCR переиспользован из pinned cache, заново не выполнялся.
Банковские численные таблицы/графики непроверены, bank_view не выдумывается;
применимость/переходы от предпосылок к выводу и gold остаются за человеком.

До первого live ответа нужны отдельное явное разрешение, доступный ключ,
подтверждённый тариф/модель и существующий общий ledger с разрешённым gate.
Использовать только ограниченную команду из INTEGRATION.md (2 draft+verify, reserve ≤5 ₽,
без model-smoke/repair/retry/fallback); здесь она **не выполнялась**.
Default UI кнопка не является отдельным разрешением на платный запуск.

## Последующее слияние в main

Имеющийся checkout main сейчас содержит чужие untracked отчёты, в том числе
`docs/audits/G1-integration-3900cf1.md` по пути, присутствующему во входящей истории.
Перед merge их владелец должен сохранить/согласовать эти материалы; не удалять,
не stash/reset автоматически и не включать их молча в integration. Это операционное
условие слияния, а не конфликт code-веток.

После ревью и при чистом checkout main, без чужих незафиксированных файлов:

```bash
git merge-base --is-ancestor main task/g1-ui-integration
git merge --ff-only task/g1-ui-integration
```

Сейчас main не переключался и не менялся. Если main продвинется и ancestry check не
пройдёт — слить новый main в отдельной integration-ветке, разрешить по смыслу и
повторить затронутые проверки; не применять reset к чужому worktree. Push отдельно.


## Итоговые свидетельства

Финальная saved-пара (Alice, U_15728, CFO, context v5):

- G1 `45e4b67e-5abf-4bb3-8549-5d97bc1578d4`;
- follow-up `2e370636-66de-4380-897b-883587e1d487`, assumption
  `contract_statement = ставка фиксирована до конца 2027`, source=user_declared.

Стенд оставлен работающим на 18084. После перезапуска контейнера db tmpfs утратится;
повторить подготовку и получить новые ID. Сами app/worker можно перезапускать, сохраняя db.
Read-only экспорт этой пары: `artifacts/g1-ui/exports/g1-ui-final`.

```bash
cg run --rm --no-deps prepare -m scripts.export_g1 \
  --analysis 45e4b67e-5abf-4bb3-8549-5d97bc1578d4 \
  --followup 2e370636-66de-4380-897b-883587e1d487 \
  --output-dir artifacts/exports/g1-ui-final
# Не перезаписывает существующую папку; повтору дать другое имя output-dir.
```

Экспорт SHA256: analysis.json `2be9461929c9370c6046cd929ae3309f297b9601f47f736e3748f583436472fb`,
followup.json `7387f58cad8754d47477d7c72162bd2fc0f02c126f33ae1c8695fdef0be8411e`.
Переключённый в браузере job `8964bd82-9212-4c27-a3c1-39ab8f102fe2`
после запуска worker завершился `failed / context_stale`, result_id отсутствует.
Strict job `6c7e2c2d-55c1-4bb6-b7e0-5b50b92d1d94`: job partial,
Analysis insufficient_evidence, original_mode=evidence_only, usage=not_called.

Сохранённые в Git [снимки](../reviews/g1-ui/README.md) дополняют полные локальные
artifacts; изображения не являются human_verified.

Готовность: код совместим с текущим main и готов к техническому merge offline-версии.
Остаётся **визуальная проверка PDF viewer в обычном браузере**; внутри IAB её зачесть
нельзя. Успех HTTP/source SHA/ссылки не подменяет эту проверку. Live/ручная сверка/gold —
отдельные границы приёмки, не объявлены завершёнными.


| Проверка | Итог без суммирования пересекающихся прогонов |
|---|---|
| `make check` | Ruff; mypy 21 modules; contracts export drift; 208 unit/contracts; 11 JS controller/DOM tests; 5 Analysis fixtures по настоящим DTO; 66 PostgreSQL integration; 2 HTTP E2E |
| unit/contracts в controlled read-only worker | 208 passed (тот же Python набор, отдельно не прибавлять) |
| Два финальных G1 прогона на одной PG/API/worker | 3 passed (7.90 s), 3 passed (4.53 s), после каждого restored baseline; сохранялись и уже существовавшие demo jobs/news |
| Review packet | `scripts.review_g1 --verify` pass; исходный ZIP SHA совпадает |
| Сборка и миграции | app build pass; Alembic 0004 единственный head; свежая tmpfs PG мигрирована обычной цепочкой |

Полные локальные логи: `artifacts/g1-ui/check-final.log`, `unit-controlled.log`,
`scenarios-final-first.log`, `scenarios-final-second.log`, `web-tests-final.log`,
`review-verify.log`, `pdf-import.log`, `news-replay-final.log`, `job-observations.log`.
При первой полной проверке E2E нашёл несовпадение прежней DEMO-плашки; после её
восстановления оба E2E проходят. Capture replay сначала отказался на отсутствующем
input mount/конечном URL Росстата; исправлена подготовка, не parser/runtime news.
В финале реальный CBR capture idempotent (unchanged=1), Росстат imported=1;
оба status=ok, отбраковки 0. Нового network smoke здесь не было.
