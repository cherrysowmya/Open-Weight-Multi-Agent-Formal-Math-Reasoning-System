from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4


class FailureCategory(StrEnum):
    NONE = "none"
    TASK_MUTATION = "task_mutation"
    UNSAFE_PLACEHOLDER = "unsafe_placeholder"
    LEAN_SYNTAX = "lean_syntax"
    UNKNOWN_IDENTIFIER = "unknown_identifier"
    TACTIC_FAILURE = "tactic_failure"
    UNSOLVED_GOALS = "unsolved_goals"
    VERIFIER_TIMEOUT = "verifier_timeout"
    VERIFIER_UNAVAILABLE = "verifier_unavailable"
    LEAN_LSP_UNAVAILABLE = "lean_lsp_unavailable"
    RETRIEVAL_UNAVAILABLE = "retrieval_unavailable"
    MODEL_UNAVAILABLE = "model_unavailable"
    SPECIALIST_UNAVAILABLE = "specialist_unavailable"
    CONTEXT_BUDGET = "context_budget"
    ITERATION_BUDGET = "iteration_budget"
    UNKNOWN = "unknown"


class InformalVerdict(StrEnum):
    ACCEPT = "accept"
    REVISE = "revise"
    REJECT = "reject"
    MALFORMED = "malformed"


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str

    def as_api_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True, slots=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True, slots=True)
class GenerationResult:
    text: str
    model: str
    usage: TokenUsage = TokenUsage()
    finish_reason: str | None = None
    latency_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class VerificationResult:
    valid: bool
    diagnostics: tuple[str, ...] = ()
    failure_category: FailureCategory = FailureCategory.NONE
    elapsed_seconds: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LeanFeedback:
    available: bool
    diagnostics: tuple[str, ...] = ()
    goal_state: str | None = None
    line: int | None = None
    column: int | None = None
    tool_calls: int = 0
    elapsed_seconds: float = 0.0
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    declaration_id: int
    name: str
    description: str | None = None
    source_text: str | None = None


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    available: bool
    query: str
    hits: tuple[RetrievalHit, ...] = ()
    tool_calls: int = 0
    elapsed_seconds: float = 0.0
    backend_processing_ms: int | None = None
    data_version: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class InformalTaskPacket:
    theorem: str
    current_goal: str = ""
    retrieved_declarations: str = ""
    diagnostics: str = ""
    rejected_strategies: str = ""


@dataclass(frozen=True, slots=True)
class InformalDraft:
    refinement_round: int
    proof: str
    generation: GenerationResult
    estimated_context_tokens: int
    lemma_queries: tuple[str, ...] = ()
    query_error: str | None = None


@dataclass(frozen=True, slots=True)
class InformalReview:
    refinement_round: int
    verdict: InformalVerdict
    critique: str
    generation: GenerationResult
    estimated_context_tokens: int
    issues: tuple[str, ...] = ()
    suggested_fix: str | None = None
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class InformalRequestRecord:
    role: str
    refinement_round: int
    messages: tuple[ChatMessage, ...]
    estimated_context_tokens: int
    max_output_tokens: int
    temperature: float
    top_p: float
    enable_thinking: bool = True
    conversation_id: str = field(default_factory=lambda: uuid4().hex)
    history_messages: int = 0


@dataclass(frozen=True, slots=True)
class InformalReasoningResult:
    triggered: bool
    accepted: bool
    rounds: int
    final_proof: str
    stop_reason: str
    generator_calls: int = 0
    verifier_calls: int = 0
    drafts: tuple[InformalDraft, ...] = ()
    reviews: tuple[InformalReview, ...] = ()
    requests: tuple[InformalRequestRecord, ...] = ()
    elapsed_seconds: float = 0.0
    error_message: str | None = None
    lemma_queries: tuple[str, ...] = ()


@dataclass(slots=True)
class AttemptMetrics:
    wall_clock_seconds: float = 0.0
    iterations: int = 0
    model_calls: int = 0
    formal_call_attempts: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    formal_output_truncations: int = 0
    formal_output_repetitions: int = 0
    formal_output_budget_increases: int = 0
    formal_output_budget_requests: list[dict[str, Any]] = field(default_factory=list)
    retrieval_calls: int = 0
    retrieval_queries: int = 0
    retrieval_latency_seconds: float = 0.0
    strategy_retrieval_queries: int = 0
    rewrite_recovery_prompts: int = 0
    lean_lsp_calls: int = 0
    kimina_checks: int = 0
    formal_specialist_calls: int = 0
    specialist_prompt_tokens: int = 0
    specialist_completion_tokens: int = 0
    specialist_successes: int = 0
    model_transitions: list[dict[str, Any]] = field(default_factory=list)
    model_peak_sampled_rss_mb: dict[str, float] = field(default_factory=dict)
    informal_generator_calls: int = 0
    informal_verifier_calls: int = 0
    informal_prompt_tokens: int = 0
    informal_completion_tokens: int = 0
    informal_context_sizes: list[int] = field(default_factory=list)
    discussion_partner_calls: int = 0
    fresh_subproblem_calls: int = 0
    main_agent_calls: int = 0
    discussion_prompt_tokens: int = 0
    discussion_completion_tokens: int = 0
    v4_context_sizes: list[int] = field(default_factory=list)
    context_compressions: int = 0
    context_sizes: list[int] = field(default_factory=list)
    model_load_seconds: float = 0.0
    model_unload_seconds: float = 0.0
    memory_samples_mb: list[dict[str, float | int | None]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class IterationRecord:
    iteration: int
    candidate: str
    verification: VerificationResult
    generation: GenerationResult
    estimated_context_tokens: int
    lean_feedback: LeanFeedback | None = None
    retrieval: RetrievalResult | None = None
    strategy_retrieval: tuple[RetrievalResult, ...] = ()
    repair_action: str | None = None
    requested_output_tokens: int | None = None
    output_status: str = "not_recorded"
    output_budget_action: str | None = None
    proof_body_normalization: str | None = None
    role: str = "main"


@dataclass(slots=True)
class AttemptResult:
    attempt_id: str
    theorem: str
    success: bool
    final_proof: str
    failure_category: FailureCategory
    stop_reason: str
    error_message: str | None
    iterations: list[IterationRecord]
    metrics: AttemptMetrics
    informal_reasoning: InformalReasoningResult | None = None
    v4_requests: list[V4RequestRecord] = field(default_factory=list)
    specialist_requests: list[V4RequestRecord] = field(default_factory=list)

    @property
    def end_reason(self) -> str:
        if self.stop_reason == "iteration_budget":
            return "MAX_ROUNDS"
        return self.stop_reason.upper()

    @property
    def rounds(self) -> int:
        return self.metrics.model_calls

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["end_reason"] = self.end_reason
        payload["rounds"] = self.rounds
        return payload


@dataclass(slots=True)
class V4RequestRecord:
    role: str
    iteration: int
    messages: tuple[ChatMessage, ...]
    estimated_context_tokens: int
    max_output_tokens: int
    enable_thinking: bool
    conversation_id: str = field(default_factory=lambda: uuid4().hex)
    history_messages: int = 0
    compressed_fields: tuple[str, ...] = ()
    source_iterations: tuple[int, ...] = ()
    status: str = "pending"
    generation: GenerationResult | None = None
    error_message: str | None = None
