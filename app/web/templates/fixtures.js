/**
 * UI Fixtures for testing states and edge cases in Alfa-Bank News Analytics Prototype.
 * Used exclusively when UI Fixture Mode is enabled.
 * All DTOs strictly adhere to contracts/v1.py schema 1.1.0.
 */
(function(window) {
  'use strict';

  const FIXTURE_USER_CONTEXT = {
    schema_version: '1.1.0',
    id: 'fixture_context_alice',
    principal_id: 'alice',
    company_id: 'demo_a',
    version: 1,
    acl_version: 1,
    role: 'cfo',
    preferences: {
      schema_version: '1.1.0',
      needs: ['finance'],
      horizon_days: 90,
      detail: 'standard'
    },
    grants: [
      {
        schema_version: '1.1.0',
        company_id: 'demo_a',
        scopes: ['analysis.run', 'profile.read', 'sources.read', 'news.read']
      },
      {
        schema_version: '1.1.0',
        company_id: 'demo_b',
        scopes: ['analysis.run', 'profile.read', 'sources.read', 'news.read']
      }
    ]
  };

  const FIXTURE_COMPANIES = [
    {
      schema_version: '1.1.0',
      company_id: 'demo_a',
      snapshot_id: 'demo_a_v1',
      source_version_id: 'synthetic_v1',
      name: 'ООO «Альфа-Агро» (demo_a)',
      industry: 'Сельское хозяйство',
      client_type: 'CLIENT',
      status: 'valid',
      features: [],
      group: {
        schema_version: '1.1.0',
        group_id: 'G1',
        visible_company_ids: ['demo_a', 'demo_b'],
        coverage: 'authorized_subset',
        consolidation: 'not_performed',
        observed_group_revenue_sum: null,
        segment: 'confirmed',
        feature_refs: []
      },
      market_share: null,
      warnings: []
    },
    {
      schema_version: '1.1.0',
      company_id: 'demo_b',
      snapshot_id: 'demo_b_v1',
      source_version_id: 'synthetic_v1',
      name: 'АО «Альфа-Трейд» (demo_b)',
      industry: 'Оптовая торговля',
      client_type: 'CLIENT',
      status: 'valid',
      features: [],
      group: {
        schema_version: '1.1.0',
        group_id: 'G1',
        visible_company_ids: ['demo_a', 'demo_b'],
        coverage: 'authorized_subset',
        consolidation: 'not_performed',
        observed_group_revenue_sum: null,
        segment: 'confirmed',
        feature_refs: []
      },
      market_share: null,
      warnings: []
    }
  ];

  const FIXTURE_FEED = [
    {
      schema_version: '1.1.0',
      event: {
        schema_version: '1.1.0',
        id: 'rate_hold',
        source_version_id: 'synthetic_v1',
        domain: 'synthetic',
        title: 'Совет директоров Банка России сохранил ключевую ставку на уровне 21,00% годовых',
        available_at: '2026-10-21T00:00:00Z',
        event_at: '2026-10-21T00:00:00Z',
        evidence_ids: ['demo_a_evidence'],
        topics: ['rates', 'monetary_policy'],
        event_time_source: 'publication'
      },
      context_version: 1,
      reason_codes: ['synthetic_scenario', 'same_industry'],
      feature_ids: [],
      delivery: {
        schema_version: '1.1.0',
        mode: 'fixture',
        generated_at: '2026-10-21T00:00:00Z',
        original_mode: null,
        stale: false,
        stale_reasons: []
      }
    }
  ];

  // Scenario 1: Ready Standard Analysis
  const FIXTURE_ANALYSIS_READY = {
    schema_version: '1.1.0',
    id: 'analysis_fixture_ready',
    job_id: 'job_fixture_ready',
    run_id: 'run_fixture_ready',
    company_id: 'demo_a',
    company_snapshot_id: 'demo_a_v1',
    event_id: 'rate_hold',
    source_version_ids: ['synthetic_v1'],
    context: FIXTURE_USER_CONTEXT,
    status: 'ready',
    domain: 'synthetic',
    delivery: {
      schema_version: '1.1.0',
      mode: 'saved_replay',
      generated_at: '2026-10-21T10:00:00Z',
      original_mode: 'fixture',
      stale: false,
      stale_reasons: []
    },
    temporal: {
      schema_version: '1.1.0',
      temporal_mode: 'snapshot_demo',
      as_of: '2026-10-21T00:00:00Z',
      period: null,
      available_at: null,
      temporal_status: 'snapshot',
      warnings: ['snapshot_not_historical_backtest'],
      depends_on_source_versions: []
    },
    model_id: null,
    usage_status: 'not_called',
    policy_version: 'foundation-1',
    sections: {
      schema_version: '1.1.0',
      happened: ['claim_fact_rate'],
      relevance: ['claim_ai_relevance'],
      consequences: ['claim_hypo_costs'],
      actions: ['claim_ai_actions'],
      bank_view: [],
      basis: ['claim_fact_rate']
    },
    claims: [
      {
        schema_version: '1.1.0',
        id: 'claim_fact_rate',
        kind: 'fact',
        origin: 'synthetic',
        text: 'Совет директоров Банка России принял решение сохранить ключевую ставку на уровне 21,00% годовых.',
        evidence_ids: ['demo_a_evidence'],
        feature_ids: [],
        conditions: [],
        alternatives: [],
        check: 'supported'
      },
      {
        schema_version: '1.1.0',
        id: 'claim_ai_relevance',
        kind: 'ai_inference',
        origin: 'synthetic',
        text: 'Для компаний отрасли сельского хозяйства с плавающими ставками кредитования процентные расходы сохранятся на пиковом уровне.',
        evidence_ids: [],
        feature_ids: [],
        conditions: ['При наличии действующих кредитных линий с привязкой к ключевой ставке'],
        alternatives: ['При переходе на фиксированные субсидируемые программы эффект нейтрален'],
        check: 'conditional'
      },
      {
        schema_version: '1.1.0',
        id: 'claim_hypo_costs',
        kind: 'hypothesis',
        origin: 'synthetic',
        text: 'Возможно замедление инвестиционной активности в модернизацию зернохранилищ в 1–2 кварталах 2027 года.',
        evidence_ids: [],
        feature_ids: [],
        conditions: ['Требует сверки с бизнес-планом инвестиций на 2027 год'],
        alternatives: ['Господдержка отрасли может компенсировать до 50% ставки'],
        check: 'conditional'
      },
      {
        schema_version: '1.1.0',
        id: 'claim_ai_actions',
        kind: 'ai_inference',
        origin: 'synthetic',
        text: 'Рекомендуется оценить возможности реструктуризации долговой нагрузки и провести аудит ликвидности на горизонте 90 дней.',
        evidence_ids: [],
        feature_ids: [],
        conditions: [],
        alternatives: [],
        check: 'supported'
      }
    ],
    evidence: [
      {
        schema_version: '1.1.0',
        id: 'demo_a_evidence',
        document_version_id: 'doc_cbr_press_release',
        source_version_id: 'synthetic_v1',
        quote: 'Совет директоров Банка России 25 октября 2026 года принял решение сохранить ключевую ставку на уровне 21,00% годовых. Инфляционное давление остаётся высоким.',
        page: 1,
        span: 'chars:0:154',
        value_kind: 'fact',
        quality: 'verified',
        domain: 'synthetic',
        available_at: '2026-10-21T00:00:00Z'
      }
    ],
    features: [],
    notices: [
      {
        schema_version: '1.1.0',
        id: 'investment',
        kind: 'investment_disclaimer',
        text: 'Не является индивидуальной инвестиционной рекомендацией. Финансовые решения принимаются клиентом самостоятельно.'
      },
      {
        schema_version: '1.1.0',
        id: 'ai',
        kind: 'ai_attribution',
        text: 'AI-выводы и гипотезы сформированы алгоритмами и не являются официальной кредитной или рыночной позицией АО «АЛЬФА-БАНК».'
      },
      {
        schema_version: '1.1.0',
        id: 'synthetic',
        kind: 'synthetic_data',
        text: 'Учебный сценарий rate_hold; LLM выключена, расчет произведен по моделируемым правилам.'
      },
      {
        schema_version: '1.1.0',
        id: 'time',
        kind: 'temporal_limitation',
        text: 'Снимок данных на дату 21.10.2026; не является историческим backtest.'
      }
    ],
    missing_fields: ['bank_evidence'],
    questions: [
      'Какова доля кредитов компании с плавающей процентной ставкой?',
      'Планируется ли привлечение субсидированного финансирования в ближайшие 90 дней?'
    ],
    bank_evidence_status: 'not_found',
    assumptions: [],
    conversation_id: null,
    parent_analysis_id: null
  };

  // Scenario 2: Partial Analysis with Missing Bank Evidence
  const FIXTURE_ANALYSIS_PARTIAL = {
    ...FIXTURE_ANALYSIS_READY,
    id: 'analysis_fixture_partial',
    status: 'partial',
    sections: {
      schema_version: '1.1.0',
      happened: ['claim_fact_rate'],
      relevance: ['claim_ai_relevance'],
      consequences: [],
      actions: [],
      bank_view: [],
      basis: ['claim_fact_rate']
    },
    missing_fields: ['bank_evidence', 'risk_forecast', 'cbr_speech_transcript'],
    bank_evidence_status: 'not_found'
  };

  // Scenario 3: Insufficient Evidence (e.g. Strict as_of)
  const FIXTURE_ANALYSIS_INSUFFICIENT = {
    ...FIXTURE_ANALYSIS_READY,
    id: 'analysis_fixture_insufficient',
    status: 'insufficient_evidence',
    sections: {
      schema_version: '1.1.0',
      happened: [],
      relevance: [],
      consequences: [],
      actions: [],
      bank_view: [],
      basis: []
    },
    claims: [],
    evidence: [],
    temporal: {
      schema_version: '1.1.0',
      temporal_mode: 'strict_as_of',
      as_of: '2026-09-01T00:00:00Z',
      period: null,
      available_at: null,
      temporal_status: 'excluded',
      warnings: ['availability_unknown', 'aggregate_end_unknown'],
      depends_on_source_versions: []
    },
    missing_fields: ['event_within_temporal_window', 'cbr_statement'],
    questions: ['Уточните дату актуальности (as_of) для включения событий октября 2026 года.']
  };

  // Scenario 4: Stale Replay (Source Corrected)
  const FIXTURE_ANALYSIS_STALE = {
    ...FIXTURE_ANALYSIS_READY,
    id: 'analysis_fixture_stale',
    delivery: {
      schema_version: '1.1.0',
      mode: 'saved_replay',
      generated_at: '2026-10-20T08:00:00Z',
      original_mode: 'fixture',
      stale: true,
      stale_reasons: ['news_source_corrected:cbr_press_v2', 'event_recalculated']
    }
  };

  // Scenario 5: Long text & XSS Security Test
  const FIXTURE_ANALYSIS_SECURITY_LONG_TEXT = {
    ...FIXTURE_ANALYSIS_READY,
    id: 'analysis_fixture_security',
    sections: {
      schema_version: '1.1.0',
      happened: ['claim_xss_test'],
      relevance: ['claim_long_text'],
      consequences: [],
      actions: [],
      bank_view: [],
      basis: ['claim_xss_test']
    },
    claims: [
      {
        schema_version: '1.1.0',
        id: 'claim_xss_test',
        kind: 'fact',
        origin: 'synthetic',
        text: 'Тест экранирования: <script>alert("XSS")<\\/script> <b>Жирный текст</b> & "кавычки" \'одинарные\' <img src=x onerror=alert(1)>',
        evidence_ids: ['evidence_xss_test'],
        feature_ids: [],
        conditions: ['<script>if(true){}<\\/script> Условие безопасности'],
        alternatives: ['Альтернатива с <tags> и спецсимволами & < > " \''],
        check: 'supported'
      },
      {
        schema_version: '1.1.0',
        id: 'claim_long_text',
        kind: 'ai_inference',
        origin: 'synthetic',
        text: 'Длинное развёрнутое экономическое описание влияния процентной политики на сельскохозяйственный сектор Российской Федерации. В условиях сохранения жестких денежно-кредитных условий Банка России предприятия агропромышленного комплекса сталкиваются с ростом стоимости обслуживания заемных средств, привлекаемых для пополнения оборотных средств в период уборочной кампании и закупки посевного материала на весенний сезон. В особенности это затрагивает средние и малые предприятия, не имеющие доступа к специализированным государственным субсидиям по льготным ставкам до 5% годовых, что создает неравномерность в структуре себестоимости конечной продукции.',
        evidence_ids: [],
        feature_ids: [],
        conditions: [],
        alternatives: [],
        check: 'conditional'
      }
    ],
    evidence: [
      {
        schema_version: '1.1.0',
        id: 'evidence_xss_test',
        document_version_id: 'doc_security_test',
        source_version_id: 'synthetic_v1',
        quote: '<script>alert("Evidence XSS")<\\/script> Документ с опасным содержимым и длинной цитатой, которая должна быть безопасно отображена исключительно через безопасный textContent без исполнения исполняемого скрипта.',
        page: 42,
        span: 'chars:10:240',
        value_kind: 'unknown',
        quality: 'unverified',
        domain: 'synthetic',
        available_at: '2026-10-21T00:00:00Z'
      }
    ]
  };

  // Mock Document for Evidence Drawer
  const FIXTURE_DOCUMENT = {
    schema_version: '1.1.0',
    id: 'doc_cbr_press_release',
    source_version_id: 'synthetic_v1',
    domain: 'synthetic',
    title: 'Пресс-релиз Банка России по итогам заседания Совета директоров',
    publisher: 'Пресс-служба Банка России',
    published_at: '2026-10-21T10:30:00Z',
    available_at: '2026-10-21T10:31:00Z',
    text: '25 октября 2026 года Совет директоров Банка России принял решение сохранить ключевую ставку на уровне 21,00% годовых. Текущее инфляционное давление остается высоким. Годовая инфляция по итогам 2026 года ожидается в диапазоне 8,0–8,5%. Рост внутреннего спроса продолжает опережать возможности расширения предложения товаров и услуг. Требуется поддержание жестких денежно-кредитных условий продолжительное время.',
    url: 'https://www.cbr.ru/press/PR/?file=example_demo.htm',
    guid: 'cbr-press-2026-10-25-rate-hold',
    supersedes_id: null,
    date_precision: 'timestamp'
  };

  window.AlfaFixtures = {
    USER_CONTEXT: FIXTURE_USER_CONTEXT,
    COMPANIES: FIXTURE_COMPANIES,
    FEED: FIXTURE_FEED,
    DOCUMENT: FIXTURE_DOCUMENT,
    SCENARIOS: {
      ready: { label: 'Готовый анализ (Ready / Saved replay)', analysis: FIXTURE_ANALYSIS_READY },
      partial: { label: 'Частичный анализ (Partial / Missing fields)', analysis: FIXTURE_ANALYSIS_PARTIAL },
      insufficient: { label: 'Недостаточно данных (Insufficient evidence / strict as_of)', analysis: FIXTURE_ANALYSIS_INSUFFICIENT },
      stale: { label: 'Устаревший анализ (Stale / Source corrected)', analysis: FIXTURE_ANALYSIS_STALE },
      security: { label: 'Длинный текст и проверка XSS безопасности', analysis: FIXTURE_ANALYSIS_SECURITY_LONG_TEXT },
      budget_error: { label: 'Ошибка: Превышение бюджета (Budget exceeded 429)', isError: true, status: 429, code: 'budget_exceeded', message: 'Дневной лимит бюджета LLM исчерпан (450 RUB cap reached).' },
      context_stale: { label: 'Ошибка: Конфликт версии контекста (Context stale 409)', isError: true, status: 409, code: 'context_stale', message: 'Context version changed (expected v1, found v2).' }
    }
  };
})(window);
