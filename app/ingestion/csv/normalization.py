"""Pure, conservative CSV normalization based on webinar decisions W01–W09.

No function creates a grant, a user identity, an external counterparty name, or
consolidated group metrics. Unknown input remains unknown, with its raw value.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable, Literal, Mapping, Sequence

from contracts.v1 import Feature, Period, Quality, SourceRef, TemporalAssessment, TemporalMode, utc

REVENUE_PERIOD = Period(start=date(2025, 1, 1), end=date(2025, 12, 31))
PAYMENT_PERIOD = Period(start=date(2026, 1, 1), end=None)
SEMANTIC_SOURCE = "сводка.md:Транскрипция вебинара"
RULE_VERSION = "official-qa-2"
PAYMENT_SEMANTIC_SOURCE = "Вопросы с вебинара _ Новостная лента.md:20"
FLAG_FIELDS = (
    "NEGATIVEMARK",
    "CL_PRODUCT_RKO_MARK",
    "CL_PRODUCT_GAR_MARK",
    "CL_PRODUCT_LOA_MARK",
    "CL_PRODUCT_LEA_MARK",
    "CL_PRODUCT_DEP_MARK",
    "CL_PRODUCT_ZPP_MARK",
    "FTS_FLAG",
    "FACTORING_MARK",
)
CLIENT_TYPES = frozenset({"CLIENT", "CLOSE_CLIENT", "PROSPECT"})


@dataclass(frozen=True)
class NormalizedValue:
    raw_value: str | None
    value: Decimal | int | bool | str | None
    quality: Quality
    reason_codes: tuple[str, ...] = ()
    unit: str | None = None
    period: Period | None = None
    semantic_source_ref: str | None = None
    interpretations: Mapping[str, str] = field(default_factory=dict)
    usable_for_geographic_impact: bool = False

    def to_feature(self, feature_id: str, name: str, source: SourceRef) -> Feature:
        ref = source.model_copy(
            update={
                "field": name,
                "rule_version": RULE_VERSION,
                "semantic_source_ref": self.semantic_source_ref or source.semantic_source_ref,
            }
        )
        value = self.value
        # The public contract represents Decimal/integer amounts as exact strings.
        serialized = value if value is None or isinstance(value, (str, bool)) else str(value)
        return Feature(
            id=feature_id,
            name=name,
            raw_value=self.raw_value,
            value=serialized,
            quality=self.quality,
            unit=self.unit,
            period=self.period,
            provenance=[ref],
            depends_on_source_versions=[source.source_version_id],
            reason_codes=list(self.reason_codes),
        )


def _raw(value: object) -> str | None:
    return None if value is None else str(value)


def _missing(value: str | None) -> bool:
    return value is None or not value.strip()


def normalize_flag(raw: object) -> NormalizedValue:
    original = _raw(raw)
    text = original.strip() if original is not None else ""
    if text in {"0", "0.0", "1", "1.0"}:
        return NormalizedValue(original, text in {"1", "1.0"}, Quality.verified)
    return NormalizedValue(
        original,
        None,
        Quality.unknown,
        ("missing_value" if _missing(original) else "invalid_flag",),
    )


def _nonnegative_decimal(raw: object, unit: str, period: Period) -> NormalizedValue:
    original = _raw(raw)
    reason = "missing_value"
    if not _missing(original):
        try:
            value = Decimal(original.strip())  # type: ignore[union-attr]
            if not value.is_finite():
                reason = "non_finite_number"
            elif value < 0:
                reason = "negative_value"
            else:
                return NormalizedValue(original, value, Quality.verified, unit=unit, period=period)
        except InvalidOperation:
            reason = "invalid_number"
    return NormalizedValue(original, None, Quality.unknown, (reason,), unit, period)


def normalize_revenue(raw: object) -> NormalizedValue:
    value = _nonnegative_decimal(raw, "RUB", REVENUE_PERIOD)
    semantic = f"{SEMANTIC_SOURCE}:Максим:156-159:W01"
    if value.value == Decimal("1000"):
        return NormalizedValue(
            value.raw_value,
            None,
            Quality.unknown,
            ("technical_placeholder",),
            "RUB",
            REVENUE_PERIOD,
            semantic,
        )
    return NormalizedValue(
        value.raw_value,
        value.value,
        value.quality,
        value.reason_codes,
        value.unit,
        value.period,
        semantic,
    )


def normalize_payment_amount(raw: object) -> NormalizedValue:
    normalized = _nonnegative_decimal(raw, "RUB", PAYMENT_PERIOD)
    return NormalizedValue(
        normalized.raw_value,
        normalized.value,
        normalized.quality,
        normalized.reason_codes,
        normalized.unit,
        normalized.period,
        PAYMENT_SEMANTIC_SOURCE,
    )


def normalize_payment_count(raw: object) -> NormalizedValue:
    normalized = _nonnegative_decimal(raw, "count", PAYMENT_PERIOD)
    value = normalized.value
    if isinstance(value, Decimal):
        if value != value.to_integral_value():
            return NormalizedValue(
                normalized.raw_value,
                None,
                Quality.unknown,
                ("non_integer_count",),
                "count",
                PAYMENT_PERIOD,
            )
        value = int(value)
    return NormalizedValue(
        normalized.raw_value,
        value,
        normalized.quality,
        normalized.reason_codes,
        "count",
        PAYMENT_PERIOD,
        PAYMENT_SEMANTIC_SOURCE,
    )


def normalize_client_type(raw: object) -> NormalizedValue:
    original = _raw(raw)
    text = original.strip() if original is not None else ""
    if text in CLIENT_TYPES:
        return NormalizedValue(
            original,
            text,
            Quality.verified,
            semantic_source_ref=f"{SEMANTIC_SOURCE}:Максим:138-147:W04",
        )
    return NormalizedValue(
        original,
        "unknown",
        Quality.unknown,
        ("missing_value" if _missing(original) else "unknown_client_type",),
    )


def eligible_client(client_type: str, has_server_grant: bool) -> bool:
    """Client status is necessary but never grants access on its own."""
    return client_type == "CLIENT" and has_server_grant


def normalize_service_geography(field_name: str, raw: object) -> NormalizedValue:
    if field_name not in {"CITY_NAME", "RC_NAME"}:
        raise ValueError("service geography applies only to CITY_NAME and RC_NAME")
    original = _raw(raw)
    interpretations = {
        "webinar": "legal_entity_service_city"
        if field_name == "CITY_NAME"
        else "legal_entity_service_region",
        "case_page_16": "individual_residence_city"
        if field_name == "CITY_NAME"
        else "individual_residence_region",
    }
    return NormalizedValue(
        original,
        None if _missing(original) else original,
        Quality.conflict,
        ("semantic_conflict",),
        semantic_source_ref=f"{SEMANTIC_SOURCE}:Максим:174-177:W07",
        interpretations=interpretations,
        usable_for_geographic_impact=False,
    )


def normalize_owner_information(raw: object) -> NormalizedValue:
    """NEGATIVEMARK describes bank information, never negative owner risk."""
    flag = normalize_flag(raw)
    return NormalizedValue(
        flag.raw_value,
        flag.value,
        flag.quality,
        flag.reason_codes,
        semantic_source_ref=f"{SEMANTIC_SOURCE}:Максим:150-153:W08",
        interpretations={"webinar": "bank_has_reliable_key_owner_information"},
    )


def normalize_fts_countries(raw: object) -> NormalizedValue:
    original = _raw(raw)
    return NormalizedValue(
        original,
        None if _missing(original) else original,
        Quality.unknown if _missing(original) else Quality.unverified,
        ("missing_value",) if _missing(original) else ("countries_only",),
        semantic_source_ref=f"{SEMANTIC_SOURCE}:Максим:180-183:W08",
        interpretations={"webinar": "countries_only_no_direction_currency_or_goods"},
    )


@dataclass(frozen=True)
class NormalizedPayment:
    raw: Mapping[str, str | None]
    payer: str | None
    beneficiary: str | None
    amount: NormalizedValue
    count: NormalizedValue
    kind: Literal["self_transfer", "observed_payment"]
    economic_role: Literal["unknown"] = "unknown"
    reason_codes: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return (
            self.payer is not None
            and self.beneficiary is not None
            and (self.amount.quality == Quality.verified and self.count.quality == Quality.verified)
            and not self.reason_codes
        )

    @property
    def intercompany_eligible(self) -> bool:
        return self.valid and self.kind == "observed_payment"


def normalize_payment(row: Mapping[str, str | None]) -> NormalizedPayment:
    payer = (row.get("PAYER") or "").strip() or None
    beneficiary = (row.get("BENEFICIARY") or "").strip() or None
    amount = normalize_payment_amount(row.get("PAY_AMT"))
    count = normalize_payment_count(row.get("PAY_CNT"))
    reasons: list[str] = []
    if payer is None or beneficiary is None:
        reasons.append("missing_endpoint")
    # A zero is preserved; inconsistent zero aggregates are quarantined, not corrected.
    if amount.value is not None and count.value is not None:
        if (amount.value == 0) != (count.value == 0):
            reasons.append("inconsistent_zero_aggregate")
    return NormalizedPayment(
        dict(row),
        payer,
        beneficiary,
        amount,
        count,
        "self_transfer" if payer is not None and payer == beneficiary else "observed_payment",
        reason_codes=tuple(reasons),
    )


@dataclass(frozen=True)
class PaymentDuplicateAssessment:
    exact_duplicate_rows: tuple[int, ...]
    repeated_pairs: tuple[tuple[str, str], ...]
    quarantined_rows: tuple[int, ...]
    normalized_rows: tuple[NormalizedPayment, ...]
    affected_payers: tuple[str, ...]
    issue_codes: tuple[str, ...]


def classify_payment_duplicates(
    rows: Sequence[Mapping[str, str | None]],
) -> PaymentDuplicateAssessment:
    """Small-batch pure helper; row numbers are CSV lines (header is line 1).

    Exact raw duplicates are omitted in the normalized layer. Heterogeneous
    repeated pairs are wholly quarantined, never summed into a false aggregate.
    For full inputs use audit's disk-backed streaming implementation.
    """
    seen: dict[tuple[tuple[str, str | None], ...], int] = {}
    pairs: dict[tuple[str, str], list[tuple[int, NormalizedPayment]]] = defaultdict(list)
    exact: list[int] = []
    rejected: list[int] = []
    for number, row in enumerate(rows, start=2):
        signature = tuple(sorted(row.items()))
        if signature in seen:
            exact.append(number)
            continue
        seen[signature] = number
        payment = normalize_payment(row)
        if payment.payer is None or payment.beneficiary is None:
            rejected.append(number)
            continue
        pairs[(payment.payer, payment.beneficiary)].append((number, payment))
    repeated = tuple(sorted(pair for pair, variants in pairs.items() if len(variants) > 1))
    rejected.extend(n for pair in repeated for n, _ in pairs[pair])
    rejected.extend(
        n
        for pair, entries in pairs.items()
        if pair not in repeated
        for n, p in entries
        if not p.valid
    )
    retained = tuple(
        p for pair, entries in pairs.items() if pair not in repeated for _, p in entries if p.valid
    )
    codes = tuple(
        code
        for condition, code in (
            (exact, "exact_payment_duplicate"),
            (repeated, "heterogeneous_repeated_pair"),
        )
        if condition
    )
    return PaymentDuplicateAssessment(
        tuple(exact),
        repeated,
        tuple(sorted(rejected)),
        retained,
        tuple(sorted({p for p, _ in repeated})),
        codes,
    )


@dataclass(frozen=True)
class CompanyRowAssessment:
    company_id: str
    status: Literal["valid", "quarantined"]
    rows: tuple[Mapping[str, str | None], ...]
    conflicting_fields: tuple[str, ...]
    issue_codes: tuple[str, ...]


def quarantine_companies(
    rows: Iterable[Mapping[str, str | None]],
) -> dict[str, CompanyRowAssessment]:
    groups: dict[str, list[Mapping[str, str | None]]] = defaultdict(list)
    for row in rows:
        company_id = (row.get("SYNTHETIC_U") or "").strip()
        if not company_id:
            raise ValueError("missing_company_id: quarantine source row before grouping")
        groups[company_id].append(dict(row))
    result: dict[str, CompanyRowAssessment] = {}
    for company_id, entries in groups.items():
        fields = set().union(*(entry.keys() for entry in entries))
        conflicts = tuple(sorted(key for key in fields if len({r.get(key) for r in entries}) > 1))
        codes = (
            ("conflicting_company_id",)
            if conflicts
            else (("exact_company_duplicate",) if len(entries) > 1 else ())
        )
        result[company_id] = CompanyRowAssessment(
            company_id,
            "quarantined" if conflicts else "valid",
            tuple(entries),
            conflicts,
            codes,
        )
    return result


@dataclass(frozen=True)
class CounterpartyNode:
    company_id: str
    status: Literal["known", "stub"]
    economic_role: Literal["unknown"] = "unknown"
    name: None = None


def counterparty_node(company_id: str, known_company_ids: set[str]) -> CounterpartyNode:
    """A payment identifier is not an external name or an economic relationship."""
    return CounterpartyNode(company_id, "known" if company_id in known_company_ids else "stub")


def assess_time(
    mode: TemporalMode | str,
    as_of: datetime,
    available_at: datetime | None,
    period: Period | None,
    source_versions: Iterable[str],
) -> TemporalAssessment:
    """Conservative source assessment, suitable for derived-feature dependencies.

    Availability must be provided independently of a period. For several sources,
    callers assess each dependency and exclude the derived value if any is excluded.
    """
    mode = TemporalMode(mode)
    as_of = utc(as_of)
    available_at = utc(available_at) if available_at is not None else None
    warnings: list[str] = []
    if available_at is None:
        warnings.append("availability_unknown")
    elif available_at > as_of:
        warnings.append("future_availability")
    if period is not None:
        if period.end is None:
            warnings.append("aggregate_end_unknown")
        elif period.end > as_of.date():
            warnings.append("future_aggregate")
    if mode == TemporalMode.snapshot_demo:
        warnings.append("snapshot_not_historical_backtest")
        status: Literal["included", "excluded", "snapshot"] = "snapshot"
    else:
        status = "excluded" if warnings else "included"
    return TemporalAssessment(
        temporal_mode=mode,
        as_of=as_of,
        available_at=available_at,
        period=period,
        temporal_status=status,
        warnings=warnings,
        depends_on_source_versions=list(dict.fromkeys(source_versions)),
    )
