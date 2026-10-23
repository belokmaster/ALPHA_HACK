# ADR-002: время, происхождение и права независимы

Дата: 21.10.2026. Владельцы: A, M. Основание: W01–W09 и bootstrap §2.3–2.4, §3.5–3.9.

Период платежного агрегата 01.01–31.10.2026 сохранен буквально, хотя конец позже даты подготовки. Выручка относится к 2025 и RUB, но дата доступности snapshot неизвестна. Ни период, ни published_at не доказывают available_at. Объявляем два режима:

- `snapshot_demo`: использование учебного снимка с явным периодом и предупреждениями; это не исторический прогноз и не backtest без временной утечки.
- `strict_as_of`: confirmed available_at ≤ as_of; у агрегата дополнительно period.end ≤ as_of. Unknown availability или future window исключаются с reason codes. Завершение периода само по себе не подтверждает доступность выгрузки.

Групповые/платежные derived features наследуют зависимости на source versions, period и availability. Источники/признаки не становятся historical только потому, что новость старая. Tests разделяют news/document cutoff, snapshot personalization и strict fixtures с явной временной метаинформацией. M не объединяет их в одну метрику backtest.

Principal принадлежит server session. Grants назначены независимо от F/CL_TYPE/GROUP_ID/role и не принимаются из request body или X-Client-Scope. CLIENT — необходимая app eligibility, не достаточное право доступа. Role/preferences меняют представление; компания/контекст меняются с expected_version. Job и analysis фиксируют context/source versions; текущий ACL проверяется снова при исполнении и чтении результата.

GroupContext содержит только разрешенное подмножество, coverage=authorized_subset, consolidation=not_performed. Скрытые участники, их число и агрегаты не возвращаются косвенно. Observed revenue sum не является консолидированной выручкой и не подтверждает segment. Собственные payment records требуют самостоятельного scope; они не открывают закрытый профиль соседа. Unknown узел — stub; payment economic_role=unknown; F — shared_person, не login/owner.

Raw источники и версии immutable. SourceRef хранит original filename/hash, row/page/span, parser/rule version и semantic source reference. Revenue 1000 сохраняется raw и превращается в null/unknown, не 0. Exact payment duplicates отличаются от ambiguous repeated pairs; неодинаковые агрегаты не суммируются. CITY_NAME/RC_NAME сохраняют конфликт трактовок; поле не участвует в экономическом географическом выводе по умолчанию.

Все read-only tools принимают серверный UserContext. Evidence/document/job/result/cache требуют ownership/ACL; private assumptions остаются в conversation. Личный текст не кешируется между principals. Cache identity содержит principal, ACL/context/company/event/source versions, temporal_mode/as_of, assumptions и policy. Отсутствующая дата/доступ/ссылка приводит к явному exclusion/deny/partial, не к восстановлению данных догадкой.
