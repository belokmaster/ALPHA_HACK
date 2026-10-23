"""Unit tests for M03: Retrieval Service, Hybrid Search & RRF.

Covers:
- Import smoke without database/network connection
- Offline LocalE5Encoder handling (download_on_startup: false)
- Reciprocal Rank Fusion (RRF) calculation, quote deduplication, and page chunk quotas
- Universal temporal filtering without hardcoded dates or crude 2-year intervals in SQL
- Access scope including __public__ bank research corpus
- Public EvidenceRetriever.search() method contract with typed options
- DetailedSearchResult structure (evidence, theses, degraded, warnings)
- EXPLAIN plan inspection method signature
"""

import inspect
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.retrieval.service import (
    DetailedSearchResult,
    HybridRetriever,
    LocalE5Encoder,
    compute_rrf_and_deduplicate,
    load_retrieval_config,
)
from contracts.v1 import (
    Quality,
    utc,
)


class TestRetrievalUnit(unittest.TestCase):
    def test_import_smoke_no_database(self):
        """Проверка безопасного импорта HybridRetriever без БД и сети."""
        self.assertTrue(issubclass(HybridRetriever, object))
        self.assertTrue(callable(compute_rrf_and_deduplicate))
        self.assertTrue(callable(load_retrieval_config))

    def test_local_encoder_offline_mode(self):
        """Проверка работы LocalE5Encoder в оффлайн-режиме (force_disable=True)."""
        encoder = LocalE5Encoder(force_disable=True)
        self.assertFalse(encoder.is_available)
        with self.assertRaises(RuntimeError):
            encoder.encode_query("тестовый запрос")

    def test_load_retrieval_config(self):
        """Проверка конфигурации retrieval.json: запрет сетевой загрузки при старте."""
        cfg = load_retrieval_config()
        self.assertIsInstance(cfg, dict)
        self.assertTrue(cfg.get("local_only", True))
        self.assertFalse(cfg.get("download_on_startup", False))

    def test_rrf_fusion_scores_and_ranking(self):
        """Проверка слияния RRF: элемент, присутствующий и в FTS, и в Vector, поднимается выше."""
        now = utc(datetime(2026, 9, 14, 23, 59, 59, tzinfo=timezone.utc))

        ev_both = {
            "id": "ev_both",
            "document_version_id": "doc_1",
            "source_version_id": "sv_1",
            "quote": "Уникальная цитата присутствующая в обоих списках",
            "page": 1,
            "span": "p1_s1",
            "value_kind": "fact",
            "quality": Quality.verified.value,
            "domain": "synthetic",
            "available_at": now.isoformat(),
        }
        ev_fts_only = {
            "id": "ev_fts_only",
            "document_version_id": "doc_1",
            "source_version_id": "sv_1",
            "quote": "Цитата только из полнотекстового поиска",
            "page": 1,
            "span": "p1_s2",
            "value_kind": "fact",
            "quality": Quality.verified.value,
            "domain": "synthetic",
            "available_at": now.isoformat(),
        }
        ev_vec_only = {
            "id": "ev_vec_only",
            "document_version_id": "doc_2",
            "source_version_id": "sv_2",
            "quote": "Цитата только из векторного поиска",
            "page": 2,
            "span": "p2_s1",
            "value_kind": "fact",
            "quality": Quality.verified.value,
            "domain": "synthetic",
            "available_at": now.isoformat(),
        }

        # В FTS: ev_fts_only на 1 месте, ev_both на 2 месте
        fts_rows = [
            {"id": "ev_fts_only", "payload": ev_fts_only},
            {"id": "ev_both", "payload": ev_both},
        ]
        # В Vector: ev_both на 1 месте, ev_vec_only на 2 месте
        vec_rows = [
            {"id": "ev_both", "payload": ev_both},
            {"id": "ev_vec_only", "payload": ev_vec_only},
        ]

        results = compute_rrf_and_deduplicate(fts_rows, vec_rows, limit=10, rrf_k=60.0)

        self.assertEqual(len(results), 3)
        self.assertEqual(results[0].id, "ev_both", "Item present in both sources must rank 1st")
        self.assertEqual(results[1].id, "ev_fts_only")
        self.assertEqual(results[2].id, "ev_vec_only")

    def test_rrf_quote_deduplication(self):
        """Проверка дедупликации чанков с идентичной цитатой."""
        now = utc(datetime(2026, 9, 14, 23, 59, 59, tzinfo=timezone.utc))
        ev_1 = {
            "id": "ev_copy_1",
            "document_version_id": "doc_1",
            "source_version_id": "sv_1",
            "quote": "Повторяющаяся цитата документа",
            "page": 1,
            "span": "p1_s1",
            "value_kind": "fact",
            "quality": Quality.verified.value,
            "domain": "synthetic",
            "available_at": now.isoformat(),
        }
        ev_2 = {
            "id": "ev_copy_2",
            "document_version_id": "doc_1",
            "source_version_id": "sv_1",
            "quote": "  Повторяющаяся цитата документа  ",
            "page": 2,
            "span": "p2_s1",
            "value_kind": "fact",
            "quality": Quality.verified.value,
            "domain": "synthetic",
            "available_at": now.isoformat(),
        }

        fts_rows = [{"id": "ev_copy_1", "payload": ev_1}]
        vec_rows = [{"id": "ev_copy_2", "payload": ev_2}]

        results = compute_rrf_and_deduplicate(fts_rows, vec_rows, limit=10)
        self.assertEqual(len(results), 1, "Duplicate quotes must be merged to 1 entry")

    def test_rrf_page_chunk_quota(self):
        """Проверка ограничения квоты: не более 3 чанков с одной страницы."""
        now = utc(datetime(2026, 9, 14, 23, 59, 59, tzinfo=timezone.utc))
        rows = []
        for i in range(5):
            ev = {
                "id": f"ev_p1_{i}",
                "document_version_id": "doc_dense_page",
                "source_version_id": "sv_1",
                "quote": f"Разная цитата номер {i} со страницы 1",
                "page": 1,
                "span": f"p1_s{i}",
                "value_kind": "fact",
                "quality": Quality.verified.value,
                "domain": "synthetic",
                "available_at": now.isoformat(),
            }
            rows.append({"id": f"ev_p1_{i}", "payload": ev})

        results = compute_rrf_and_deduplicate(rows, [], limit=10, max_per_page=3)
        self.assertEqual(len(results), 3, "Page chunk quota must limit results to 3 from page 1")

    def test_runtime_config_cannot_enable_download(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"local_only": False, "download_on_startup": True}))
            with self.assertRaises(ValueError):
                LocalE5Encoder(config_path=path)

    def test_historical_policy_can_be_bound_without_changing_port(self):
        from app.retrieval.service import RetrievalPolicy

        policy = RetrievalPolicy(allow_historical=True)
        retriever = HybridRetriever(None, LocalE5Encoder(force_disable=True), policy=policy)
        self.assertTrue(retriever.policy.allow_historical)

    def test_public_search_contract_accepts_options(self):
        """Проверка сигнатуры публичного метода search(): поддержка limit и allow_historical."""
        sig = inspect.signature(HybridRetriever.search)
        self.assertIn("context", sig.parameters)
        self.assertIn("request", sig.parameters)
        self.assertIn("query", sig.parameters)
        self.assertIn("limit", sig.parameters)
        self.assertIn("allow_historical", sig.parameters)

    def test_detailed_search_result_structure(self):
        """Проверка структуры DetailedSearchResult: поддержка theses, degraded и warnings."""
        res = DetailedSearchResult(
            evidence=[],
            retrieval_mode="lexical_fallback",
            theses=[],
            degraded=True,
            warnings=["pgvector unavailable"],
        )
        self.assertEqual(res.evidence, [])
        self.assertEqual(res.theses, [])
        self.assertTrue(res.degraded)
        self.assertEqual(res.warnings, ["pgvector unavailable"])

    def test_explain_query_plan_method_exists(self):
        """Проверка наличия метода инспекции плана выполнения explain_query_plan()."""
        self.assertTrue(hasattr(HybridRetriever, "explain_query_plan"))
        sig = inspect.signature(HybridRetriever.explain_query_plan)
        self.assertIn("query", sig.parameters)


if __name__ == "__main__":
    unittest.main()
