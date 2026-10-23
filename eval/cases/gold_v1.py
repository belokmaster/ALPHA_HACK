import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from contracts.v1 import Role, TemporalMode


def check_claim_rubric(text: str, forbidden_claims: list[str]) -> list[str]:
    """Проверяет текст на запрещенные выводы с учетом отрицания (например 'не повышена')."""
    violations = []
    lowered = text.lower()
    for phrase in forbidden_claims:
        phrase_low = phrase.lower()
        if phrase_low in lowered:
            # Защита от false positive: если перед фразой стоит 'не ' или 'нет ' — это не нарушение
            pattern = rf"(?<!не\s)(?<!нет\s){re.escape(phrase_low)}"
            if re.search(pattern, lowered):
                violations.append(phrase)
    return violations


class GoldEvidenceGroup(BaseModel):
    """Смысловая группа доказательств (например, страница отчета или таблица со сноской)."""

    group_id: str
    acceptable_doc_ids: list[str] = Field(min_length=1)
    acceptable_pages: list[int] = Field(default_factory=list)
    acceptable_spans: list[str] = Field(default_factory=list)


class GoldRetrievalQuery(BaseModel):
    query_id: str
    event_id: str
    split: Literal["dev", "validation", "test"]
    as_of: datetime
    query_text: str
    is_paraphrase: bool = False
    gold_groups: list[GoldEvidenceGroup] = Field(default_factory=list)
    must_exclude_doc_ids: list[str] = Field(default_factory=list)
    answerable: bool = True
    allow_historical: bool = False
    temporal_mode: TemporalMode = TemporalMode.strict_as_of


class GoldScenarioSpec(BaseModel):
    case_id: str
    company_id: str
    role: Role
    as_of: datetime
    event_id: str
    split: Literal["dev", "validation", "test"]
    expected_status: Literal["ready", "partial", "insufficient_evidence"]
    required_missing_fields: list[str]
    forbidden_claims: list[str]
    expected_question_substring: str


# Exposed development scenarios; not independent human gold (see docs/gold/guide.md).
GOLD_SCENARIOS: list[GoldScenarioSpec] = [
    GoldScenarioSpec(
        case_id="G1_CFO_RATE",
        company_id="U_15728",
        role=Role.cfo,
        as_of=datetime(2026, 9, 14, 23, 59, 59, tzinfo=timezone.utc),
        event_id="case-rate-20260911",
        split="dev",
        expected_status="partial",
        required_missing_fields=["loan_rate_type", "repricing_date"],
        forbidden_claims=["платежи по кредиту вырастут", "ставка повышена", "доля рынка"],
        expected_question_substring="плавающ",
    ),
    GoldScenarioSpec(
        case_id="G2_PAYMENT_FUEL",
        company_id="U_5639",
        role=Role.ceo,
        as_of=datetime(2026, 9, 3, 23, 59, 59, tzinfo=timezone.utc),
        event_id="case-fuel-20260902",
        split="dev",
        expected_status="partial",
        required_missing_fields=["payment_contract_purpose", "transport_fuel_type"],
        forbidden_claims=["расходы на логистику вырастут на 0,91%", "поставщик"],
        expected_question_substring="контрагент",
    ),
    GoldScenarioSpec(
        case_id="G3_GOLD_INSUFFICIENT",
        company_id="U_199017",
        role=Role.unknown,
        as_of=datetime(2026, 9, 10, 23, 59, 59, tzinfo=timezone.utc),
        event_id="case-gold-20260907",
        split="validation",
        expected_status="insufficient_evidence",
        required_missing_fields=["product_assortment_gold", "position_direction"],
        forbidden_claims=["компания получит выгоду от роста цен на золото"],
        expected_question_substring="ассортимент",
    ),
    GoldScenarioSpec(
        case_id="G4_BOND_CFO",
        company_id="U_14366",
        role=Role.cfo,
        as_of=datetime(2026, 9, 15, 23, 59, 59, tzinfo=timezone.utc),
        event_id="case-bond-20260914",
        split="test",
        expected_status="partial",
        required_missing_fields=["debt_structure", "bond_maturity_schedule"],
        forbidden_claims=["доходность облигаций гарантирована", "рейтинг ухудшился"],
        expected_question_substring="облигац",
    ),
    GoldScenarioSpec(
        case_id="G5_STEEL_HISTORICAL",
        company_id="U_15728",
        role=Role.ceo,
        as_of=datetime(2026, 9, 14, 23, 59, 59, tzinfo=timezone.utc),
        event_id="case-steel-historical",
        split="test",
        expected_status="insufficient_evidence",
        required_missing_fields=["current_year_steel_forecast", "steel_price_actual_2026"],
        forbidden_claims=["прогноз 2021 года актуален в 2026 году", "рост ebitda"],
        expected_question_substring="ретроспектив",
    ),
]

# Обязательный графовый тест через реальное физлицо F_34348 (раздел 3.5 и 8.3 плана)
GOLD_GRAPH_F_CASES = [
    {
        "person_id": "F_34348",
        "csv_rows": [3754, 7047, 9310],
        "known_companies": ["U_14366", "U_70168"],
        "stub_companies": ["U_4193"],
        "forbidden_inference": ["владелец", "бенефициар", "общая долговая ответственность"],
    }
]

# Эталонный набор retrieval-запросов (покрывает все splits и события)
GOLD_RETRIEVAL_QUERIES: list[GoldRetrievalQuery] = [
    # --- DEV SPLIT ---
    GoldRetrievalQuery(
        query_id="Q1_RATE_REVISION",
        event_id="case-rate-20260911",
        split="dev",
        as_of=datetime(2026, 9, 14, 23, 59, 59, tzinfo=timezone.utc),
        query_text="Пересмотр прогноза ключевой ставки ЦБ на конец 2026 года с 13,5% до 14,0%",
        gold_groups=[
            GoldEvidenceGroup(
                group_id="rate_11sep_p1",
                acceptable_doc_ids=["doc_policy_rate_11sep"],
                acceptable_pages=[1],
            )
        ],
        must_exclude_doc_ids=["doc_alfa_steels_2021"],
        answerable=True,
        temporal_mode=TemporalMode.snapshot_demo,
    ),
    GoldRetrievalQuery(
        query_id="Q2_FUEL_PARAPHRASE",
        event_id="case-fuel-20260902",
        split="dev",
        as_of=datetime(2026, 9, 5, 23, 59, 59, tzinfo=timezone.utc),
        query_text="Удорожание моторного горючего и риски цен на нефтепродукты на рубеже лета и осени",
        is_paraphrase=True,
        gold_groups=[
            GoldEvidenceGroup(
                group_id="fuel_tracker_p1",
                acceptable_doc_ids=["doc_inflation_tracker_02"],
                acceptable_pages=[1],
            )
        ],
        must_exclude_doc_ids=[
            "doc_policy_rate_11sep",
            "doc_gold_digest_aug26",
            "doc_alfa_steels_2021",
        ],
        answerable=True,
        temporal_mode=TemporalMode.snapshot_demo,
    ),
    GoldRetrievalQuery(
        query_id="Q6_LABOR_MACRO_7M26",
        event_id="case-labor-20260907",
        split="dev",
        as_of=datetime(2026, 9, 8, 23, 59, 59, tzinfo=timezone.utc),
        query_text="Макростатистика за 7 месяцев 2026 года: дефицит кадров на рынке труда и динамика зарплат",
        gold_groups=[
            GoldEvidenceGroup(
                group_id="labor_7m26_p2",
                acceptable_doc_ids=["doc_alfa_7m26"],
                acceptable_pages=[2, 3],
            )
        ],
        must_exclude_doc_ids=["doc_policy_rate_11sep", "doc_alfa_steels_2021"],
        answerable=True,
        temporal_mode=TemporalMode.snapshot_demo,
    ),
    GoldRetrievalQuery(
        query_id="Q10_MACRO_GDP_1H26",
        event_id="case-macro-gdp-1h26",
        split="dev",
        as_of=datetime(2026, 8, 1, 23, 59, 59, tzinfo=timezone.utc),
        query_text="Динамика ВВП России в первом полугодии 2026 года и темпы роста",
        gold_groups=[
            GoldEvidenceGroup(
                group_id="gdp_1h26_p1",
                acceptable_doc_ids=["doc_macro_stat_1h26"],
                acceptable_pages=[1],
            )
        ],
        must_exclude_doc_ids=["doc_policy_rate_11sep", "doc_alfa_steels_2021"],
        answerable=True,
        temporal_mode=TemporalMode.snapshot_demo,
    ),
    # --- VALIDATION SPLIT ---
    GoldRetrievalQuery(
        query_id="Q7_CONSUMPTION_RETAIL",
        event_id="case-retail-20260805",
        split="validation",
        as_of=datetime(2026, 8, 6, 23, 59, 59, tzinfo=timezone.utc),
        query_text="Динамика потребления домохозяйств и потребительские настроения в августе 2026 года",
        gold_groups=[
            GoldEvidenceGroup(
                group_id="consumption_p1",
                acceptable_doc_ids=["doc_alfa_consumption"],
                acceptable_pages=[1, 2],
            )
        ],
        must_exclude_doc_ids=[
            "doc_policy_rate_11sep",
            "doc_inflation_tracker_02",
            "doc_alfa_steels_2021",
        ],
        answerable=True,
        temporal_mode=TemporalMode.snapshot_demo,
    ),
    GoldRetrievalQuery(
        query_id="Q8_GOLD_SCENARIOS",
        event_id="case-gold-20260907",
        split="validation",
        as_of=datetime(2026, 9, 8, 23, 59, 59, tzinfo=timezone.utc),
        query_text="Сценарии цены на золото и закупки центральных банков в августе 2026 года",
        gold_groups=[
            GoldEvidenceGroup(
                group_id="gold_p2",
                acceptable_doc_ids=["doc_gold_digest_aug26"],
                acceptable_pages=[2, 3, 7],
            )
        ],
        must_exclude_doc_ids=["doc_policy_rate_11sep", "doc_alfa_steels_2021"],
        answerable=True,
        temporal_mode=TemporalMode.snapshot_demo,
    ),
    GoldRetrievalQuery(
        query_id="Q11_FI_DEBT_STRATEGY",
        event_id="case-fi-debt-3q26",
        split="validation",
        as_of=datetime(2026, 7, 25, 23, 59, 59, tzinfo=timezone.utc),
        query_text="Доля корпоративных кредитов с плавающей ставкой в банковском секторе в 3-м квартале 2026 года",
        gold_groups=[
            GoldEvidenceGroup(
                group_id="fi_debt_p10",
                acceptable_doc_ids=["doc_fi_strategy_3q26"],
                acceptable_pages=[10],
            )
        ],
        must_exclude_doc_ids=["doc_policy_rate_11sep", "doc_alfa_steels_2021"],
        answerable=True,
        temporal_mode=TemporalMode.snapshot_demo,
    ),
    # --- TEST SPLIT ---
    GoldRetrievalQuery(
        query_id="Q4_BOND_MARKET_OVERVIEW",
        event_id="case-bond-20260914",
        split="test",
        as_of=datetime(2026, 9, 14, 23, 59, 59, tzinfo=timezone.utc),
        query_text="Итоги недели на рынке корпоративных облигаций и доходности ОФЗ",
        gold_groups=[
            GoldEvidenceGroup(
                group_id="bond_brief_p1",
                acceptable_doc_ids=["doc_bond_brief_14sep"],
                acceptable_pages=[1, 2],
            )
        ],
        must_exclude_doc_ids=["doc_alfa_steels_2021"],
        answerable=True,
        temporal_mode=TemporalMode.snapshot_demo,
    ),
    GoldRetrievalQuery(
        query_id="Q5_HISTORICAL_STEEL_2021",
        event_id="case-steel-historical",
        split="test",
        as_of=datetime(2021, 4, 20, 23, 59, 59, tzinfo=timezone.utc),
        query_text="Ретроспективные мультипликаторы и прогноз EBITDA стального сектора за 1 квартал 2021 года",
        gold_groups=[
            GoldEvidenceGroup(
                group_id="steel_2021_p1",
                acceptable_doc_ids=["doc_alfa_steels_2021"],
                acceptable_pages=[1, 2],
            )
        ],
        must_exclude_doc_ids=["doc_policy_rate_11sep", "doc_bond_brief_14sep"],
        answerable=True,
        temporal_mode=TemporalMode.snapshot_demo,
        allow_historical=True,  # Явный допуск исторического документа (устранение эвристики "2021 in query")
    ),
    GoldRetrievalQuery(
        query_id="Q3_NO_ANSWER_IT_SUBSIDY",
        event_id="case-no-answer-it",
        split="test",
        as_of=datetime(2026, 9, 14, 23, 59, 59, tzinfo=timezone.utc),
        query_text="Позиция Альфа-Банка по льготным ставкам ИТ-ипотеки и грантам Минцифры в 2026 году",
        gold_groups=[],
        must_exclude_doc_ids=["doc_alfa_steels_2021"],
        answerable=False,
    ),
    GoldRetrievalQuery(
        query_id="Q9_GOLD_STRICT_ABSTAIN",
        event_id="case-gold-strict-abstain",
        split="test",
        as_of=datetime(2026, 9, 8, 23, 59, 59, tzinfo=timezone.utc),
        query_text="Строгая позиция банка по золоту при неподтвержденной дате доступности",
        gold_groups=[],
        must_exclude_doc_ids=["doc_alfa_steels_2021"],
        answerable=False,
        temporal_mode=TemporalMode.strict_as_of,  # В strict_as_of документ с available_at=null обязан быть отсечен
    ),
]
