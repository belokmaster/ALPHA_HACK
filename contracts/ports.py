from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

from contracts.v1 import (
    Analysis,
    AnalysisRequest,
    BankContext,
    CompanyProfile,
    Event,
    Evidence,
    LLMRequest,
    LLMResult,
    RelationPath,
    UserContext,
)


class ProfileProvider(Protocol):
    async def get_profile(self, context: UserContext, company_id: str) -> CompanyProfile: ...


class RelationsProvider(Protocol):
    async def get_relations(self, context: UserContext, company_id: str) -> list[RelationPath]: ...


class EventProvider(Protocol):
    async def get_event(self, context: UserContext, event_id: str) -> Event: ...


class EvidenceRetriever(Protocol):
    async def search(
        self, context: UserContext, request: AnalysisRequest, query: str
    ) -> list[Evidence]: ...


class EvidenceProvider(Protocol):
    async def get_evidence(self, context: UserContext, ids: list[str]) -> list[Evidence]: ...

    async def origins(
        self, context: UserContext, ids: list[str]
    ) -> dict[str, Literal["bank_analysis", "external_source"]]: ...


class TemporalEvidenceProvider(EvidenceProvider, Protocol):
    """Production adapter: ACL/address checks plus document/source/evidence cutoff."""

    async def get_evidence_at(
        self, context: UserContext, request: AnalysisRequest, ids: list[str]
    ) -> list[Evidence]: ...


class AnalysisEngine(Protocol):
    async def analyze(self, context: UserContext, request: AnalysisRequest) -> Analysis: ...


Output = TypeVar("Output", bound=BaseModel)


class LLMClient(Protocol):
    async def start_run(self, run_id: str, *, job_id: str | None = None) -> None: ...

    async def complete(
        self, request: LLMRequest, response_model: type[Output]
    ) -> LLMResult[Output]: ...


class BankContextProvider(Protocol):
    async def describe_bank(
        self,
        context: UserContext,
        request: AnalysisRequest,
        channel: str,
        evidence_ids: list[str],
        searched: bool,
    ) -> "BankContext": ...
