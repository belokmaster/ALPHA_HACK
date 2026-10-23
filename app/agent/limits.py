from collections.abc import Callable
from dataclasses import dataclass, field
from math import isfinite
from time import monotonic

from app.core.errors import AppError
from contracts.v1 import ErrorCode


@dataclass(frozen=True)
class RunLimits:
    transitions: int = 12
    tools: int = 6
    models: int = 4
    repeated_searches: int = 1
    seconds: float = 60
    input_tokens: int = 24_000
    output_tokens: int = 4_000

    def __post_init__(self) -> None:
        # Synthetic default stays 60 s. Explicit live runtime may wait for delayed
        # provider accounting, while the model/token/tool/money caps stay unchanged.
        bounds = (12, 6, 4, 1, 300, 24_000, 4_000)
        values = (
            self.transitions,
            self.tools,
            self.models,
            self.repeated_searches,
            self.seconds,
            self.input_tokens,
            self.output_tokens,
        )
        counts = (
            self.transitions,
            self.tools,
            self.models,
            self.repeated_searches,
            self.input_tokens,
            self.output_tokens,
        )
        if any(type(value) is not int for value in counts) or not isfinite(self.seconds):
            raise ValueError("Run counters must be integers and deadline must be finite")
        if any(value < 0 or value > bound for value, bound in zip(values, bounds, strict=True)):
            raise ValueError("Run limits may be reduced but cannot exceed foundation caps")


@dataclass
class RunBudget:
    """Check immediately BEFORE each action, including fallback, repair and retries.

    Model tokens are reserved upper bounds. Their accounting is intentionally conservative;
    unused tokens are not automatically returned. Search counts as a tool call.
    This is an accounting primitive, not an implemented adaptive planner or async timeout.
    """

    limits: RunLimits = field(default_factory=RunLimits)
    clock: Callable[[], float] = monotonic
    transitions_used: int = field(default=0, init=False)
    tools_used: int = field(default=0, init=False)
    models_used: int = field(default=0, init=False)
    repeated_searches_used: int = field(default=0, init=False)
    input_tokens_used: int = field(default=0, init=False)
    output_tokens_used: int = field(default=0, init=False)
    _searches: set[str] = field(default_factory=set, init=False)
    _started: float = field(init=False)

    def __post_init__(self) -> None:
        self._started = self.clock()

    def check_deadline(self) -> None:
        if self.clock() - self._started >= self.limits.seconds:
            raise AppError(ErrorCode.deadline_exceeded, "Analysis deadline exceeded", 408)

    def _check(self, used: int, added: int, limit: int, name: str) -> None:
        self.check_deadline()
        if type(added) is not int or added < 0:
            raise ValueError("Usage must be a nonnegative integer")
        if used + added > limit:
            raise AppError(ErrorCode.budget_exceeded, f"Analysis {name} limit exceeded", 429)

    def transition(self) -> None:
        self._check(self.transitions_used, 1, self.limits.transitions, "state transitions")
        self.transitions_used += 1

    def tool(self) -> None:
        self._check(self.tools_used, 1, self.limits.tools, "tool calls")
        self.tools_used += 1

    def model(self, *, input_tokens: int, output_tokens: int) -> None:
        self._check(self.models_used, 1, self.limits.models, "LLM calls")
        self._check(self.input_tokens_used, input_tokens, self.limits.input_tokens, "input tokens")
        self._check(
            self.output_tokens_used, output_tokens, self.limits.output_tokens, "output tokens"
        )
        self.models_used += 1
        self.input_tokens_used += input_tokens
        self.output_tokens_used += output_tokens

    def search(self, query: str) -> None:
        normalized = " ".join(query.casefold().split())
        if not normalized:
            raise ValueError("Search query must not be blank")
        repeated = normalized in self._searches
        self._check(
            self.repeated_searches_used,
            int(repeated),
            self.limits.repeated_searches,
            "repeated searches",
        )
        self.tool()
        self.repeated_searches_used += int(repeated)
        self._searches.add(normalized)
