"""Unit tests for M01: Gold Evaluation Framework.

Covers:
- Gold corpus validation (eval/validate.py)
- Splits coverage and scenario-split consistency (eval/splits.json)
- Scenario definitions, field validity, and claim rubric matching (eval/cases/gold_v1.py)
- Metric computations: Recall@k, answerable vs no-answer separation, per-split reporting (eval/run.py)
- AnalysisRequest contract conformance
- Quality Gate negative tests (low recall, unexercised mode, document leakage)
"""

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from contracts.v1 import (
    AnalysisRequest,
    Evidence,
    Quality,
    TemporalMode,
    utc,
)
from eval.cases.gold_v1 import (
    GOLD_GRAPH_F_CASES,
    GOLD_RETRIEVAL_QUERIES,
    GOLD_SCENARIOS,
    check_claim_rubric,
)
from eval.run import compute_query_recall_at_k, quality_gate
from eval.validate import validate_gold


class TestGoldEvaluation(unittest.TestCase):
    def setUp(self):
        self.splits_path = Path("eval/splits.json")
        self.corpus_path = Path("config/bank_corpus.json")

    def test_validate_gold_corpus_success(self):
        """Проверка запуска валидатора: 0 ошибок, valid == True."""
        res = validate_gold(self.splits_path, self.corpus_path)
        self.assertTrue(res["valid"], f"Validation failed: {res.get('errors')}")
        self.assertEqual(len(res["errors"]), 0)
        self.assertEqual(res["scenarios_count"], 5)
        self.assertEqual(res["queries_count"], 11)
        self.assertEqual(res["dev_queries"], 4)
        self.assertEqual(res["validation_queries"], 3)
        self.assertEqual(res["test_queries"], 4)

    def test_splits_json_structure_and_disjointness(self):
        """Проверка структуры splits.json: dev, validation, test взаимно не пересекаются."""
        self.assertTrue(self.splits_path.exists())
        data = json.loads(self.splits_path.read_text(encoding="utf-8"))
        splits = data["splits"]
        self.assertIn("dev", splits)
        self.assertIn("validation", splits)
        self.assertIn("test", splits)

        dev_set = set(splits["dev"])
        val_set = set(splits["validation"])
        test_set = set(splits["test"])

        self.assertTrue(dev_set.isdisjoint(val_set), "dev and validation must be disjoint")
        self.assertTrue(dev_set.isdisjoint(test_set), "dev and test must be disjoint")
        self.assertTrue(val_set.isdisjoint(test_set), "validation and test must be disjoint")

    def test_gold_scenarios_specs(self):
        """Проверка 5 сценариев gold-набора: все поля заполнены, UTC timezone, рубрики."""
        self.assertEqual(len(GOLD_SCENARIOS), 5)
        case_ids = {s.case_id for s in GOLD_SCENARIOS}
        self.assertEqual(
            case_ids,
            {
                "G1_CFO_RATE",
                "G2_PAYMENT_FUEL",
                "G3_GOLD_INSUFFICIENT",
                "G4_BOND_CFO",
                "G5_STEEL_HISTORICAL",
            },
        )

        for s in GOLD_SCENARIOS:
            self.assertTrue(s.case_id)
            self.assertTrue(s.event_id)
            self.assertTrue(s.company_id)
            self.assertIsNotNone(s.as_of.tzinfo)
            self.assertEqual(s.as_of.tzinfo, timezone.utc)
            self.assertTrue(s.expected_question_substring)
            self.assertGreater(len(s.forbidden_claims), 0)

    def test_analysis_request_conformance_for_all_gold_scenarios(self):
        """Регрессионный тест: проверка создания AnalysisRequest для каждого gold-сценария."""
        for s in GOLD_SCENARIOS:
            req = AnalysisRequest(
                event_id=s.event_id,
                context_version=1,
                temporal_mode=TemporalMode.strict_as_of,
                as_of=s.as_of,
                scenario_id="rate_hold",
            )
            self.assertEqual(req.event_id, s.event_id)
            self.assertEqual(req.scenario_id, "rate_hold")

    def test_gold_graph_f_case(self):
        """Проверка обязательного графового кейса физлица F_34348 (п. 3.5 и 8.3 плана)."""
        self.assertEqual(len(GOLD_GRAPH_F_CASES), 1)
        f_case = GOLD_GRAPH_F_CASES[0]
        self.assertEqual(f_case["person_id"], "F_34348")
        self.assertEqual(f_case["known_companies"], ["U_14366", "U_70168"])
        self.assertEqual(f_case["stub_companies"], ["U_4193"])
        self.assertIn("бенефициар", f_case["forbidden_inference"])

    def test_check_claim_rubric_clean_statement(self):
        """Проверка рубрики: утверждение без нарушений возвращает пустой список."""
        forbidden = ["платежи по кредиту вырастут", "ставка повышена"]
        text = "Текущий кредитный договор оформлен с фиксированной процентной ставкой."
        violations = check_claim_rubric(text, forbidden)
        self.assertEqual(violations, [])

    def test_check_claim_rubric_violation_detected(self):
        """Проверка рубрики: обнаружение запрещенного утверждения."""
        forbidden = ["платежи по кредиту вырастут", "ставка повышена"]
        text = "Вследствие решения ЦБ ставка повышена до 21%."
        violations = check_claim_rubric(text, forbidden)
        self.assertEqual(violations, ["ставка повышена"])

    def test_check_claim_rubric_negation_safe(self):
        """Проверка рубрики: утверждение с отрицанием ('не повышена') не должно штрафоваться."""
        forbidden = ["ставка повышена"]
        text = "Ключевая ставка не повышена на последнем заседании."
        violations = check_claim_rubric(text, forbidden)
        self.assertEqual(violations, [])

    def test_compute_query_recall_answerable(self):
        """Проверка метрики Recall@k для отвечаемых запросов (answerable=True)."""
        query = next(q for q in GOLD_RETRIEVAL_QUERIES if q.query_id == "Q1_RATE_REVISION")

        matching_evidence = [
            Evidence(
                id="ev1",
                document_version_id="doc_policy_rate_11sep",
                source_version_id="sv_cbr_1",
                quote="Совет директоров Банка России принял решение повысить ключевую ставку",
                page=1,
                span="p1_s1",
                value_kind="fact",
                quality=Quality.verified,
                domain="synthetic",
                available_at=utc(datetime(2026, 9, 15, tzinfo=timezone.utc)),
            )
        ]

        metrics = compute_query_recall_at_k(matching_evidence, query, k=5)
        self.assertTrue(metrics["answerable"])
        self.assertGreater(metrics["recall_at_5"], 0.0)
        self.assertEqual(metrics["matched_groups"], 1)
        self.assertEqual(len(metrics["future_or_retro_leakage"]), 0)

    def test_compute_query_recall_no_answer_clean(self):
        """Проверка Раздела 8.3: для no-answer запроса фоновые кандидаты допустимы, если нет утечки."""
        query = next(q for q in GOLD_RETRIEVAL_QUERIES if q.query_id == "Q3_NO_ANSWER_IT_SUBSIDY")
        self.assertFalse(query.answerable)

        # Кандидаты из разрешенного документа
        candidates = [
            Evidence(
                id="ev_cand_1",
                document_version_id="doc_policy_rate_11sep",
                source_version_id="sv_cbr_1",
                quote="Инфляционное давление сохраняется на высоком уровне",
                page=1,
                span="p1_s2",
                value_kind="fact",
                quality=Quality.verified,
                domain="synthetic",
                available_at=utc(datetime(2026, 9, 15, tzinfo=timezone.utc)),
            )
        ]

        metrics = compute_query_recall_at_k(candidates, query, k=5)
        self.assertFalse(metrics["answerable"])
        self.assertTrue(metrics["no_leakage_clean"])
        self.assertEqual(len(metrics["future_or_retro_leakage"]), 0)

    def test_compute_query_recall_no_answer_leakage_detected(self):
        """Проверка Раздела 8.3: обнаружение запрещённых документов (утечки) для no-answer."""
        query = next(q for q in GOLD_RETRIEVAL_QUERIES if q.query_id == "Q3_NO_ANSWER_IT_SUBSIDY")

        # Кандидат с запрещенным doc_id (doc_alfa_steels_2021)
        bad_evidence = [
            Evidence(
                id="ev_leak",
                document_version_id=query.must_exclude_doc_ids[0],
                source_version_id="sv_steel_1",
                quote="Ретроспективные мультипликаторы...",
                page=1,
                span="p1_s1",
                value_kind="forecast",
                quality=Quality.verified,
                domain="synthetic",
                available_at=utc(datetime(2021, 4, 20, tzinfo=timezone.utc)),
            )
        ]

        metrics = compute_query_recall_at_k(bad_evidence, query, k=5)
        self.assertFalse(metrics["no_leakage_clean"])
        self.assertGreater(len(metrics["future_or_retro_leakage"]), 0)

    def test_negative_gate_logic_on_low_recall_or_mode_mismatch(self):
        """Негативный тест Quality Gate: падение при несовпадении режима или низком Recall."""

        self.assertFalse(
            quality_gate(
                [1] * 5 + [0] * 5,
                mode_exercised=True,
                gold_ready=True,
                no_answer_attribution_verified=True,
            )
        )
        self.assertFalse(
            quality_gate(
                [1] * 10, mode_exercised=False, gold_ready=True, no_answer_attribution_verified=True
            )
        )
        self.assertFalse(
            quality_gate(
                [1] * 10, mode_exercised=True, gold_ready=False, no_answer_attribution_verified=True
            )
        )
        self.assertFalse(
            quality_gate(
                [1] * 10, mode_exercised=True, gold_ready=True, no_answer_attribution_verified=False
            )
        )
        self.assertTrue(
            quality_gate(
                [1] * 10, mode_exercised=True, gold_ready=True, no_answer_attribution_verified=True
            )
        )

    def test_eval_run_exit_code_semantics(self):
        """Проверка семантики кодов завершения: 0=acceptance certified, 1=quality fail, 2=diagnostic only."""
        gate_passed = quality_gate(
            [0.9] * 10,
            mode_exercised=True,
            gold_ready=True,
            no_answer_attribution_verified=True,
        )
        self.assertTrue(gate_passed)

        gate_failed_recall = quality_gate(
            [0.5] * 10,
            mode_exercised=True,
            gold_ready=True,
            no_answer_attribution_verified=True,
        )
        self.assertFalse(gate_failed_recall)

        gate_diagnostic_only = quality_gate(
            [1.0] * 10,
            mode_exercised=True,
            gold_ready=False,
            no_answer_attribution_verified=True,
        )
        self.assertFalse(gate_diagnostic_only)


if __name__ == "__main__":
    unittest.main()
