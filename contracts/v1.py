from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Generic, Literal, Self, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION: Literal["1.1.0"] = "1.1.0"
POLICY_VERSION = "foundation-1"


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone required")
    return value.astimezone(timezone.utc)


UTC = Annotated[datetime, AfterValidator(utc)]
ID = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.:-]+$")]


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)
    schema_version: Literal["1.0.0", "1.1.0"] = SCHEMA_VERSION


class Quality(StrEnum):
    verified = "verified"
    unverified = "unverified"
    unknown = "unknown"
    conflict = "conflict"


class Role(StrEnum):
    ceo = "ceo"
    cfo = "cfo"
    unknown = "unknown"


class TemporalMode(StrEnum):
    snapshot_demo = "snapshot_demo"
    strict_as_of = "strict_as_of"


class SourceRef(DTO):
    source_version_id: ID
    original_filename: str
    sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    row: int | None = Field(default=None, ge=1)
    page: int | None = Field(default=None, ge=1)
    span: str | None = None
    field: str | None = None
    parser_version: str = "synthetic-1"
    rule_version: str = "webinar-1"
    semantic_source_ref: str | None = None


class Period(DTO):
    start: date
    end: date | None = None

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.end is None and self.schema_version == "1.0.0":
            raise ValueError("unknown period end requires schema 1.1.0")
        if self.end is not None and self.start > self.end:
            raise ValueError("invalid period")
        return self


class SourceVersion(DTO):
    id: ID
    ref: SourceRef
    domain: Literal["synthetic", "case", "live"]
    available_at: UTC | None = None
    period: Period | None = None
    import_version: str = "1"


class TemporalAssessment(DTO):
    temporal_mode: TemporalMode
    as_of: UTC
    period: Period | None = None
    available_at: UTC | None = None
    temporal_status: Literal["included", "excluded", "snapshot"]
    warnings: list[str] = Field(default_factory=list)
    depends_on_source_versions: list[ID] = Field(default_factory=list)


class DataQualityIssue(DTO):
    id: ID
    source: SourceRef
    code: str
    severity: Literal["warning", "error"]
    safe_behavior: str
    resolved_by_source_ref: SourceRef | None = None


class Grant(DTO):
    company_id: ID
    scopes: list[
        Literal["profile.read", "news.read", "analysis.run", "sources.read", "relations.read"]
    ]


class Preferences(DTO):
    needs: list[Literal["finance", "operations", "growth"]] = Field(
        default_factory=list, max_length=3
    )
    horizon_days: int = Field(default=90, ge=1, le=365)
    detail: Literal["brief", "standard"] = "standard"


class UserAssumption(DTO):
    name: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=500)
    scope: Literal["conversation"] = "conversation"
    source: Literal["user_declared"] = "user_declared"


class UserContext(DTO):
    role_source: Literal["modeled_context", "user_selected"] = "modeled_context"
    id: ID
    principal_id: ID
    company_id: ID
    version: int = Field(ge=1)
    acl_version: int = Field(ge=1)
    role: Role = Role.unknown
    preferences: Preferences = Field(default_factory=Preferences)
    grants: list[Grant]


class ContextPatch(DTO):
    expected_version: int = Field(ge=1)
    company_id: ID | None = None
    role: Role | None = None
    preferences: Preferences | None = None


class SessionRequest(DTO):
    actor: Literal["alice", "bob"]


class SessionResponse(DTO):
    context: UserContext
    csrf_token: str
    expires_at: UTC


class Feature(DTO):
    id: ID
    name: str
    raw_value: str | None
    value: str | bool | None
    quality: Quality
    unit: str | None = None
    period: Period | None = None
    available_at: UTC | None = None
    provenance: list[SourceRef]
    depends_on_source_versions: list[ID]
    reason_codes: list[str] = Field(default_factory=list)


class GroupContext(DTO):
    group_id: ID | None = None
    visible_company_ids: list[ID] = Field(default_factory=list)
    coverage: Literal["authorized_subset"] = "authorized_subset"
    consolidation: Literal["not_performed"] = "not_performed"
    observed_group_revenue_sum: Decimal | None = None
    segment: Literal["unknown", "confirmed"] = "unknown"
    feature_refs: list[ID] = Field(default_factory=list)


class CompanyProfile(DTO):
    company_id: ID
    snapshot_id: ID
    source_version_id: ID
    name: str
    industry: str | None
    client_type: Literal["CLIENT", "CLOSE_CLIENT", "PROSPECT", "unknown"]
    status: Literal["valid", "quarantined", "stub"]
    features: list[Feature]
    group: GroupContext = Field(default_factory=GroupContext)
    market_share: Decimal | None = None
    warnings: list[str] = Field(default_factory=list)


class RelationPath(DTO):
    id: ID
    kind: Literal["shared_person", "group", "observed_payment", "self_transfer"]
    nodes: list[ID]
    provenance: list[SourceRef]
    quality: Quality
    economic_role: Literal["unknown"] = "unknown"


class Document(DTO):
    id: ID
    source_version_id: ID
    domain: Literal["synthetic", "case", "live"]
    title: str
    publisher: str
    published_at: UTC | None
    available_at: UTC | None
    text: str
    url: str | None = None
    guid: str | None = None
    supersedes_id: ID | None = None
    date_precision: Literal["timestamp", "day", "unknown"] = "unknown"


class Evidence(DTO):
    id: ID
    document_version_id: ID
    source_version_id: ID
    quote: str
    page: int | None = Field(default=None, ge=1)
    span: str
    value_kind: Literal["fact", "forecast", "opinion", "unknown"]
    quality: Quality
    domain: Literal["synthetic", "case", "live"]
    available_at: UTC | None


class BankThesis(DTO):
    id: ID
    evidence_ids: list[ID]
    text: str
    issued_at: UTC | None
    horizon: str | None
    status: Literal["applicable", "historical", "conflicting", "unverified"]


class Event(DTO):
    id: ID
    source_version_id: ID
    domain: Literal["synthetic", "case", "live"]
    title: str
    available_at: UTC | None
    event_at: UTC
    evidence_ids: list[ID]
    topics: list[str] = Field(default_factory=list)
    event_time_source: Literal["publication", "event", "unknown"] = "unknown"


class NewsSourceState(DTO):
    id: str
    url: str
    last_attempt_at: UTC | None = None
    last_success_at: UTC | None = None
    status: Literal["never", "ok", "partial", "error"] = "never"
    error: str | None = None
    imported: int = 0
    unchanged: int = 0
    rejected: int = 0


class Notice(DTO):
    id: ID
    kind: Literal[
        "investment_disclaimer", "ai_attribution", "synthetic_data", "temporal_limitation"
    ]
    text: str


class DeliveryInfo(DTO):
    mode: Literal["fixture", "live", "cache", "saved_replay", "evidence_only", "controlled"]
    generated_at: UTC
    original_mode: Literal["fixture", "live", "evidence_only", "controlled"] | None = None
    stale: bool = False
    stale_reasons: list[str] = Field(default_factory=list)


class RelevanceReason(DTO):
    code: str
    text: str
    weight: int = Field(ge=0)
    feature_ids: list[ID]
    caveat: str


class FeedRelevance(DTO):
    policy_version: str
    channel: str
    score: int = Field(ge=0)
    reasons: list[RelevanceReason]
    order_rule: str


class FeedItem(DTO):
    relevance: FeedRelevance | None = None
    event: Event
    context_version: int
    reason_codes: list[str]
    feature_ids: list[ID]
    delivery: DeliveryInfo


class AnalysisRequest(DTO):
    event_id: ID
    context_version: int = Field(ge=1)
    temporal_mode: TemporalMode = TemporalMode.snapshot_demo
    as_of: UTC
    scenario_id: Literal["rate_hold"] = "rate_hold"


class Claim(DTO):
    impact_kind: Literal["neutral", "risk", "opportunity"] = "neutral"
    id: ID
    kind: Literal["fact", "bank_view", "ai_inference", "hypothesis"]
    origin: Literal[
        "external_source", "bank_analysis", "model", "user_declared", "synthetic", "rule", "unknown"
    ] = "unknown"
    text: str
    evidence_ids: list[ID] = Field(default_factory=list)
    feature_ids: list[ID] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    check: Literal["supported", "conditional", "unsupported", "conflicting"]

    @model_validator(mode="after")
    def attribution(self) -> Self:
        if self.schema_version == "1.1.0" and self.origin == "unknown":
            raise ValueError("claim origin must be explicit")
        if self.kind == "bank_view" and self.origin not in ("bank_analysis", "unknown"):
            raise ValueError("bank_view requires bank attribution")
        if self.schema_version == "1.1.0":
            allowed = {
                "fact": {"external_source", "bank_analysis", "synthetic"},
                "bank_view": {"bank_analysis"},
                "ai_inference": {"model", "synthetic"},
                "hypothesis": {
                    "model",
                    "user_declared",
                    "synthetic",
                    "rule",
                    "bank_analysis",
                    "external_source",
                },
            }
            if self.origin not in allowed[self.kind]:
                raise ValueError("claim kind and origin disagree")
        if self.origin == "rule" and (not self.feature_ids or not self.evidence_ids):
            raise ValueError("rule inference requires observed features and source")
        if (
            self.kind == "hypothesis"
            and self.origin in {"bank_analysis", "external_source"}
            and not self.evidence_ids
        ):
            raise ValueError("source forecast requires evidence")
        if self.kind == "hypothesis":
            if self.schema_version == "1.0.0":
                raise ValueError("hypothesis requires schema 1.1.0")
            if not self.conditions or self.check not in ("conditional", "unsupported"):
                raise ValueError("hypothesis requires explicit conditions and non-factual status")
        return self


class Sections(DTO):
    happened: list[ID]
    relevance: list[ID]
    consequences: list[ID]
    actions: list[ID]
    bank_view: list[ID]
    basis: list[ID]


class BankContext(DTO):
    diagnosis: Literal[
        "not_available",
        "not_found",
        "excluded_by_time",
        "quality_limited",
        "conflicting",
        "available",
    ] = "not_available"
    theses: list[BankThesis] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class Analysis(DTO):
    event_title: str = ""
    bank_context: BankContext = Field(default_factory=BankContext)
    id: ID
    job_id: ID
    run_id: ID
    company_id: ID
    company_snapshot_id: ID
    event_id: ID
    source_version_ids: list[ID]
    context: UserContext
    status: Literal["ready", "partial", "insufficient_evidence", "failed"]
    domain: Literal["synthetic", "case", "live"]
    delivery: DeliveryInfo
    temporal: TemporalAssessment
    model_id: str | None = None
    usage_status: Literal["not_called", "provider", "unknown"] = "not_called"
    policy_version: str = POLICY_VERSION
    sections: Sections
    claims: list[Claim]
    evidence: list[Evidence]
    features: list[Feature]
    notices: list[Notice]
    missing_fields: list[str]
    questions: list[str]
    bank_evidence_status: Literal["found", "background_only", "not_found", "conflicting"]
    assumptions: list[UserAssumption] = Field(default_factory=list)
    conversation_id: ID | None = None
    parent_analysis_id: ID | None = None

    @model_validator(mode="after")
    def references(self) -> Self:
        claim_ids = {c.id for c in self.claims}
        evidence_ids = {e.id for e in self.evidence}
        feature_ids = {f.id for f in self.features}
        if len(claim_ids) != len(self.claims):
            raise ValueError("duplicate claim")
        for key in Sections.model_fields:
            if key != "schema_version" and not set(getattr(self.sections, key)) <= claim_ids:
                raise ValueError("missing section claim")
        for thesis in self.bank_context.theses:
            if not thesis.evidence_ids or not set(thesis.evidence_ids) <= evidence_ids:
                raise ValueError("bank context needs saved evidence")
            fragments = [e for e in self.evidence if e.id in thesis.evidence_ids]
            if thesis.status == "unverified" and thesis.text not in {e.quote for e in fragments}:
                raise ValueError("unverified bank context must be a verbatim stored quote")
            if thesis.status == "applicable" and any(
                e.quality != Quality.verified for e in fragments
            ):
                raise ValueError("unverified OCR cannot become an applicable bank thesis")
        for c in self.claims:
            if c.kind == "fact" and any(
                e.value_kind in {"forecast", "opinion"}
                for e in self.evidence
                if e.id in c.evidence_ids
            ):
                raise ValueError("source forecast/opinion cannot be an event fact")
            if not set(c.evidence_ids) <= evidence_ids or not set(c.feature_ids) <= feature_ids:
                raise ValueError("missing evidence or feature")
            if c.kind in ("fact", "bank_view") and not c.evidence_ids:
                raise ValueError("factual claim needs evidence")
            if c.kind == "bank_view" and self.bank_evidence_status == "not_found":
                raise ValueError("false bank attribution")
        if self.domain == "synthetic" and (
            self.model_id is not None or self.usage_status != "not_called"
        ):
            raise ValueError("synthetic outputs cannot claim model use")
        return self


class Conversation(DTO):
    id: ID
    analysis_id: ID
    principal_id: ID
    assumptions: list[UserAssumption]


class FollowupRequest(DTO):
    analysis_id: ID
    conversation_id: ID | None = None
    context_version: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=2000)
    assumptions: list[UserAssumption] = Field(default_factory=list, max_length=10)
    scenario_id: Literal["fixed_rate", "fixture_only"] = "fixture_only"


class FollowupResponse(DTO):
    conversation_id: ID
    job_id: ID
    parent_analysis_id: ID


class ErrorCode(StrEnum):
    validation = "validation"
    forbidden = "forbidden"
    not_found = "not_found"
    context_stale = "context_stale"
    idempotency_conflict = "idempotency_conflict"
    budget_exceeded = "budget_exceeded"
    budget_unknown = "budget_unknown"
    deadline_exceeded = "deadline_exceeded"
    backend_unavailable = "backend_unavailable"
    feature_not_implemented = "feature_not_implemented"
    data_conflict = "data_conflict"
    internal = "internal"
    llm_busy = "llm_busy"
    llm_invalid_response = "llm_invalid_response"
    llm_outcome_unknown = "llm_outcome_unknown"
    llm_rate_limited = "llm_rate_limited"


class APIError(DTO):
    code: ErrorCode
    message: str
    retryable: bool = False
    request_id: str
    job_id: ID | None = None
    details: dict[str, str | int | list[str]] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error: APIError


class Job(DTO):
    id: ID
    kind: Literal["analysis", "followup"]
    status: Literal["queued", "running", "succeeded", "partial", "failed", "cancelled"]
    attempt: int
    result_id: ID | None = None
    conversation_id: ID | None = None
    error: ErrorEnvelope | None = None
    poll_url: str


class AgentRun(DTO):
    id: ID
    job_id: ID
    runner: Literal["fixture_runner", "analysis_engine"]
    steps_used: int = Field(ge=0, le=12)
    tool_calls_used: int = Field(ge=0, le=6)
    model_calls_used: int = Field(ge=0, le=4)
    checkpoints: list[str]


class ToolCall(DTO):
    id: ID
    run_id: ID
    tool: Literal[
        "get_profile", "get_relations", "get_event", "get_evidence", "search_bank", "describe_bank"
    ]
    status: Literal["ok", "empty", "partial", "denied", "error"]
    source_ids: list[ID]
    reason_code: str


class ModelUsage(DTO):
    model_id: str | None
    usage_status: Literal["not_called", "provider", "unknown"]
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_rub: Decimal | None = Field(default=None, ge=0)


class LLMRequest(DTO):
    """Server-created request. Never accept scope, tariff or limits from the model."""

    run_id: ID
    call_id: ID
    model_id: str
    prompt: str = Field(min_length=1, max_length=24_000)
    max_output_tokens: int = Field(default=700, strict=True, ge=1, le=4_000)
    purpose: Literal["plan", "draft", "verify", "repair", "retry", "fallback"] = "plan"


LLMOutput = TypeVar("LLMOutput", bound=BaseModel)


class LLMResult(DTO, Generic[LLMOutput]):
    call_id: ID
    value: LLMOutput
    usage: ModelUsage


class BudgetReservation(DTO):
    id: ID
    budget_day: date
    amount_rub: Decimal = Field(gt=0)
    status: Literal["reserved", "settled", "released", "uncertain"]
    tariff_version: str
