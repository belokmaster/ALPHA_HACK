# API examples · schema 1.1.0 (reads legacy 1.0.0)

Login → save cookie and csrf_token → GET context → PATCH with expected_version → POST analyses with X-CSRF-Token and a unique Idempotency-Key → poll poll_url → GET analyses/result_id. Followups use the original analysis context_version. IDs below are examples; use returned IDs. All bodies below are validated by tests.

## BankContext

```json
{
  "schema_version": "1.1.0",
  "diagnosis": "quality_limited",
  "theses": [],
  "gaps": [
    "bank_ocr_unverified"
  ]
}
```

## FeedRelevance

```json
{
  "schema_version": "1.1.0",
  "policy_version": "company-channels-1",
  "channel": "rates",
  "score": 80,
  "reasons": [
    {
      "schema_version": "1.1.0",
      "code": "observed_credit_product",
      "text": "В профиле есть кредитные продукты",
      "weight": 80,
      "feature_ids": [
        "observed_loan"
      ],
      "caveat": "Параметры договора неизвестны"
    }
  ],
  "order_rule": "Сначала сумма весов, затем дата события и заголовок"
}
```

## DeliveryInfo

```json
{
  "schema_version": "1.1.0",
  "mode": "controlled",
  "generated_at": "2026-10-21T00:00:00Z",
  "original_mode": null,
  "stale": false,
  "stale_reasons": []
}
```

## Evidence

```json
{
  "schema_version": "1.1.0",
  "id": "unclassified_ocr_example",
  "document_version_id": "example_document_version",
  "source_version_id": "example_source_version",
  "quote": "Учебный непроверенный OCR: смесь фактов и прогнозов, не тезис банка.",
  "page": 1,
  "span": "page_chars:0:68",
  "value_kind": "unknown",
  "quality": "unverified",
  "domain": "synthetic",
  "available_at": null
}
```

## NewsSourceState

```json
{
  "schema_version": "1.1.0",
  "id": "cbr-press",
  "url": "https://www.cbr.ru/rss/RssPress",
  "last_attempt_at": null,
  "last_success_at": null,
  "status": "never",
  "error": null,
  "imported": 0,
  "unchanged": 0,
  "rejected": 0
}
```

## LLMRequest

```json
{
  "schema_version": "1.1.0",
  "run_id": "engine_run",
  "call_id": "model_call",
  "model_id": "deepseek/deepseek-v4.1-flash",
  "prompt": "Synthetic schema example",
  "max_output_tokens": 700,
  "purpose": "plan"
}
```

## SessionRequest

```json
{
  "schema_version": "1.1.0",
  "actor": "alice"
}
```

## UserContext

```json
{
  "schema_version": "1.1.0",
  "role_source": "modeled_context",
  "id": "alice_context",
  "principal_id": "alice",
  "company_id": "demo_a",
  "version": 1,
  "acl_version": 1,
  "role": "unknown",
  "preferences": {
    "schema_version": "1.1.0",
    "needs": [],
    "horizon_days": 90,
    "detail": "standard"
  },
  "grants": [
    {
      "schema_version": "1.1.0",
      "company_id": "demo_a",
      "scopes": [
        "analysis.run",
        "profile.read",
        "sources.read"
      ]
    }
  ]
}
```

## ContextPatch

```json
{
  "schema_version": "1.1.0",
  "expected_version": 1,
  "company_id": null,
  "role": "cfo",
  "preferences": null
}
```

## AnalysisRequest

```json
{
  "schema_version": "1.1.0",
  "event_id": "rate_hold",
  "context_version": 1,
  "temporal_mode": "snapshot_demo",
  "as_of": "2026-10-21T00:00:00Z",
  "scenario_id": "rate_hold"
}
```

## Job

```json
{
  "schema_version": "1.1.0",
  "id": "example",
  "kind": "analysis",
  "status": "queued",
  "attempt": 0,
  "result_id": null,
  "conversation_id": null,
  "error": null,
  "poll_url": "/api/v1/jobs/example"
}
```

## Analysis

```json
{
  "schema_version": "1.1.0",
  "event_title": "",
  "bank_context": {
    "schema_version": "1.1.0",
    "diagnosis": "not_available",
    "theses": [],
    "gaps": []
  },
  "id": "example",
  "job_id": "example",
  "run_id": "example_run",
  "company_id": "demo_a",
  "company_snapshot_id": "demo_a_v1",
  "event_id": "rate_hold",
  "source_version_ids": [
    "synthetic_v1"
  ],
  "context": {
    "schema_version": "1.1.0",
    "role_source": "modeled_context",
    "id": "alice_context",
    "principal_id": "alice",
    "company_id": "demo_a",
    "version": 1,
    "acl_version": 1,
    "role": "unknown",
    "preferences": {
      "schema_version": "1.1.0",
      "needs": [],
      "horizon_days": 90,
      "detail": "standard"
    },
    "grants": [
      {
        "schema_version": "1.1.0",
        "company_id": "demo_a",
        "scopes": [
          "analysis.run",
          "profile.read",
          "sources.read"
        ]
      }
    ]
  },
  "status": "partial",
  "domain": "synthetic",
  "delivery": {
    "schema_version": "1.1.0",
    "mode": "fixture",
    "generated_at": "2026-10-21T00:00:00Z",
    "original_mode": null,
    "stale": false,
    "stale_reasons": []
  },
  "temporal": {
    "schema_version": "1.1.0",
    "temporal_mode": "snapshot_demo",
    "as_of": "2026-10-21T00:00:00Z",
    "period": null,
    "available_at": null,
    "temporal_status": "snapshot",
    "warnings": [
      "snapshot_not_historical_backtest"
    ],
    "depends_on_source_versions": []
  },
  "model_id": null,
  "usage_status": "not_called",
  "policy_version": "foundation-1",
  "sections": {
    "schema_version": "1.1.0",
    "happened": [
      "fact"
    ],
    "relevance": [],
    "consequences": [],
    "actions": [],
    "bank_view": [],
    "basis": [
      "fact"
    ]
  },
  "claims": [
    {
      "schema_version": "1.1.0",
      "impact_kind": "neutral",
      "id": "fact",
      "kind": "fact",
      "origin": "synthetic",
      "text": "Учебное событие. Не реальная новость.",
      "evidence_ids": [
        "demo_a_evidence"
      ],
      "feature_ids": [],
      "conditions": [],
      "alternatives": [],
      "check": "supported"
    }
  ],
  "evidence": [
    {
      "schema_version": "1.1.0",
      "id": "demo_a_evidence",
      "document_version_id": "demo_a_doc",
      "source_version_id": "synthetic_v1",
      "quote": "Учебное событие. Не реальная новость.",
      "page": null,
      "span": "full",
      "value_kind": "fact",
      "quality": "verified",
      "domain": "synthetic",
      "available_at": "2026-10-21T00:00:00Z"
    }
  ],
  "features": [],
  "notices": [
    {
      "schema_version": "1.1.0",
      "id": "investment",
      "kind": "investment_disclaimer",
      "text": "Не является индивидуальной инвестиционной рекомендацией"
    },
    {
      "schema_version": "1.1.0",
      "id": "synthetic",
      "kind": "synthetic_data",
      "text": "Синтетический пример; LLM не вызывалась."
    },
    {
      "schema_version": "1.1.0",
      "id": "ai",
      "kind": "ai_attribution",
      "text": "Не является официальной позицией банка."
    },
    {
      "schema_version": "1.1.0",
      "id": "time",
      "kind": "temporal_limitation",
      "text": "Учебный снимок; не исторический backtest."
    }
  ],
  "missing_fields": [
    "bank_evidence"
  ],
  "questions": [
    "Какие условия договора?"
  ],
  "bank_evidence_status": "not_found",
  "assumptions": [],
  "conversation_id": null,
  "parent_analysis_id": null
}
```

## FollowupRequest

```json
{
  "schema_version": "1.1.0",
  "analysis_id": "example",
  "conversation_id": null,
  "context_version": 1,
  "text": "Какие условия?",
  "assumptions": [],
  "scenario_id": "fixture_only"
}
```

## Evidence

```json
{
  "schema_version": "1.1.0",
  "id": "demo_a_evidence",
  "document_version_id": "demo_a_doc",
  "source_version_id": "synthetic_v1",
  "quote": "Учебное событие. Не реальная новость.",
  "page": null,
  "span": "full",
  "value_kind": "fact",
  "quality": "verified",
  "domain": "synthetic",
  "available_at": "2026-10-21T00:00:00Z"
}
```

## Period

```json
{
  "schema_version": "1.1.0",
  "start": "2026-01-01",
  "end": null
}
```

## Claim

```json
{
  "schema_version": "1.1.0",
  "impact_kind": "neutral",
  "id": "external_fact_example",
  "kind": "fact",
  "origin": "external_source",
  "text": "Пример внешнего факта; источник разрешается через evidence/document publisher.",
  "evidence_ids": [
    "external_example"
  ],
  "feature_ids": [],
  "conditions": [],
  "alternatives": [],
  "check": "supported"
}
```

## Claim

```json
{
  "schema_version": "1.1.0",
  "impact_kind": "neutral",
  "id": "model_example",
  "kind": "ai_inference",
  "origin": "model",
  "text": "Пример вывода модели, не позиция банка.",
  "evidence_ids": [],
  "feature_ids": [],
  "conditions": [
    "Применим при подтверждении исходных условий"
  ],
  "alternatives": [],
  "check": "conditional"
}
```

## Claim

```json
{
  "schema_version": "1.1.0",
  "impact_kind": "neutral",
  "id": "hypothesis_example",
  "kind": "hypothesis",
  "origin": "model",
  "text": "Пример гипотезы; не подтверждённый факт.",
  "evidence_ids": [],
  "feature_ids": [],
  "conditions": [
    "Нужна проверка договора"
  ],
  "alternatives": [],
  "check": "conditional"
}
```
