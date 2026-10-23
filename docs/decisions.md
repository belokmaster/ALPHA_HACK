# Решения и уточнения, 21.10.2026

Основание: полный [план](plan.md), сведённые в план требования bootstrap и предоставленная транскрипция вебинара с SHA-256 `075b5bd04b49ef2aab27c09252d6181e8364ef71bd7d94bdc1bba6e93bc182f1`. Номера строк фиксируют историческое происхождение уточнений; исходная транскрипция отдельно не хранится, решения приведены ниже. Аудиозапись не сверялась; пересказ и Q&A внутри прежней транскрипции не являются независимыми подтверждениями. Отдельный официальный письменный Q&A теперь принят: [impact review](official-qa-impact.md). Manifest и глубина проверки — [sources.md](sources.md). Принятое решение не означает выполненный тест: статусы — [requirements.md](requirements.md), результаты — [handoffs/BOOTSTRAP.md](handoffs/BOOTSTRAP.md).

Сохранённые записи provenance могут содержать исторический идентификатор `сводка.md:Транскрипция вебинара` с говорящим, строками и W-ID. Это идентификатор источника, а не путь для открытия файла; соответствующие решения W01–W19 сведены в таблицу ниже. Его значение не меняется при уборке документации, чтобы не переписывать уже импортированные данные и их происхождение.

| ID | Прямой источник | Решение и безопасная граница | Проверка / владелец |
|---|---|---|---|
| W01 | Максим, 156–159 | Revenue — Decimal RUB за 2025; `1000` и `1000.0` → null/unknown/technical_placeholder с сохранением raw. Настоящий 0, null и 1001 различаются | Normalization unit tests; A |
| W02 | Максим, 186–189 | PAY_AMT — RUB, PAY_CNT — целое количество; официальный Q&A20: с 1 января; end=null. Не придумывать даты отдельных операций | Decimal/count/period tests; A |
| W03 | Максим, 192–195 | Self-edge — перевод между своими счетами. Оставить raw, исключить из межфирменной экспозиции. 82% суммы/около 3% количества — слова эксперта, новый аудит считает отдельно ΣPAY_AMT, ΣPAY_CNT и долю строк | Audit artifact + self-transfer test; A |
| W04 | Максим, 138–147; Денис, 395–398 | CLOSE_CLIENT — бывший клиент с закрытым счетом. App допускает CLIENT только с server grant. PROSPECT/CLOSE_CLIENT сохраняются как разрешенный контекст, не получают login автоматически | Demo actors, denied company tests; A/B |
| W05 | Максим, 132–135, 162–165; Денис, 221–230 | Учитывать юрлицо и разрешенную часть группы. GROUP_ID не устанавливает конкретную долю, консолидацию, гарантию или право доступа. Порог 15 млрд не фильтр app | Authorized subset; hidden member/aggregate tests; A |
| W06 | Максим, 168–171 | Конфликтующий U карантинировать. Объяснение обезличивания не основание выбрать последнюю строку или обнулить все поля | Conflict fixture и case audit A02; A |
| W07 | Максим, 174–177; задание с. 16 | CITY_NAME/RC_NAME трактуются вебинаром как обслуживание юрлица, заданием как проживание F. Обе трактовки и raw сохраняются как semantic conflict. Не использовать для места проживания, производства, доставки или ACL; CITY/REGION компании не переименовывать | Semantic-conflict test; A; Q03 |
| W08 | Максим, 150–153, 180–183 | NEGATIVEMARK говорит о наличии у банка сведений о ключевом собственнике, не о негативном риске. ФТС дает страны, не направление торговли, товар, валюту или объем | Normalization/provenance; A |
| W09 | Максим, 198–213 | Неизвестный U — stub. Платеж — observed_payment с economic_role=unknown; не реконструировать имя, товар, supplier/buyer | Stub/relation tests; A |
| W10 | Ксения, 73–88 | LangChain/LangGraph разрешены. Собственный Python controller — выбор команды, зафиксированный в ADR; fixture runner не выдается за adaptive agent | [ADR-001](adr/001-foundation-boundary.md); M |
| W11 | Ксения, 91–118 | Локальные embeddings/rerankers/classical ML допустимы. Вопрос внешних AI API остался неразъясненным. Local E5 запланирован; ни embedding API, ни дополнительные генеративные модели не подключаются | M03 model manifest; runtime allowlist; A/M |
| W12 | Денис, 317–326, 341–344 | Не каждому инсайту нужен bank_view. При отсутствии evidence — not_found, без ложной атрибуции. Существенный вклад банка в недельный продукт и абляция остаются | No-bank fixture; затем M06 ablation; M |
| W13 | Денис, 365–368 и 323–326 | Обязательны «Не является индивидуальной инвестиционной рекомендацией» и отдельная маркировка AI как неофициальной позиции банка; synthetic/time notices по условиям | Response + UI, включая partial/replay; B/M |
| W14 | Денис, 263–272, 371–428, 544–550 | Веб-раздел авторизованной зоны с заметным входом; bank auth моделируется серверной demo session. Реальных SSO, банковского API и регистрации нет | DEMO selector, CSRF, owned session; A/B |
| W15 | Денис, 263–272, 389–392 | Role switch — наш продуктовый выбор. Current enum: ceo/cfo/unknown. Меняются потребность/акцент, но не grants, факты, банк и ограничения | Pair/ownership tests; A/B/M |
| W16 | Денис, 281–296, 469–472; Максим, 523–526 | Бесплатные законные источники выбирает команда; создание потока входит в продукт. CBR RSS+article — B02; импорт по запросу достаточен для первой версии. Старый PDF не становится актуальной позицией | Future parser correction/source tests; B/M |
| W17 | Максим, 511–514; задание с. 8 | Устное nice to have не отменяет письменный интерактивный прототип. Foundation проверяет рабочий Docker/API/worker/app shell | bootstrap + HTTP E2E; B |
| W18 | Денис, 311–314 | Steel 2021 можно отнести к commodity/steel; это не доказательство, что отдельный Commodities performance найден. Дата 2021 сохраняется | Source inventory; future M02 temporal check; M |
| W19 | Денис, 535–574; эксперты, 583–586 | Короткий путь к инсайту и небольшой demo pool. Только fixture runner ограничен scenario IDs; будущий case алгоритм должен обрабатывать весь допустимый набор | Fixture boundary; затем full import/eval и UX measure; A/B/M |

Дополнительные инженерные решения:

| ID | Решение | Последствие |
|---|---|---|
| D-TIME | `snapshot_demo` явно использует снимок, `strict_as_of` требует подтвержденную available_at ≤ as_of и period.end ≤ as_of | Неизвестный конец исключается как aggregate_end_unknown; unknown availability исключается. Revenue 2025 не сообщает доступность snapshot. См. Q01/Q02 |
| D-GRANTS | Principal и grants серверные; F, GROUP_ID, CL_TYPE, роль и headers не создают права | Каждый API/tool/result/cache/job/evidence повторяет ownership/ACL. Сумма/размер скрытой части группы тоже закрыты |
| D-PAY | Advanced shares — A05 после контроля duplicate/conflicting pairs, полного сопоставимого denominator и независимого доступа к собственным платежам | Foundation делает нормализацию/диагностику; никакой доли выручки, закупок или финансовой зависимости |
| D-PROV | Raw и source hashes неизменяемы; semantic rule ссылается на транскрипцию; normalized версии отдельны | Никакое поле неизвестного происхождения не повышается до verified; null не равен 0 |
| D-FIXTURE | Единственный bootstrap domain — synthetic; output fixture/model_id=null/not_called | Настоящие queue/ACL/persistence проверяемы; качество RAG/LLM, OCR и банк ими не доказаны |
| D-BUDGET | Live выключен; общая atomic reservation, cap 450 ₽ при provider 500 ₽, reset 03:00 Europe/Moscow | Unknown tariff → budget_unknown; uncertain outcome удерживает резерв, не вызывает слепой повтор. Fake usage не считается расходом |
| D-SCHEMA | Исполняемая schema 1.1.0 (чтение legacy1.0.0) — `contracts/v1.py`; интерфейсы — `contracts/ports.py` | Старые TypeScript/HTTP snippets плана — эскизы будущего продукта; клиент использует экспорт OpenAPI и API_EXAMPLES |

Ни одно из решений не требует автоматического live smoke. Отдельный явный запуск пользователя, проверенные тарифы/usage, согласованный gateway и лимиты необходимы до живого вызова.
