"""M04 planner contract, narrowed to implemented read-only channels.

No model-selected company, dates, tools, roles, permissions, budgets or source IDs.
Draft/verify keep the existing product schemas and strict hypothesis-only policy.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

UnknownField = Literal[
    "contract_rate_type",
    "repricing_date",
    "refinancing_plans",
    "loan_balance",
    "fuel_cost_share",
    "fuel_contract_terms",
    "fleet_or_transport_purchases",
]
Query = Annotated[str, Field(min_length=1, max_length=500)]


class AnalysisPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)
    channels: list[Literal["rates", "fuel_costs"]] = Field(min_length=1, max_length=1)
    required_fields: list[UnknownField] = Field(default_factory=list, max_length=7)
    next_action: Literal["search", "clarify"] = "search"
    bank_query: Query
    refined_bank_query: Query | None = None
    if_missing: list[UnknownField] = Field(default_factory=list, max_length=7)
