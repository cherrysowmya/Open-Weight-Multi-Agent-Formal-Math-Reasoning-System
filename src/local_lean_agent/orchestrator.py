from __future__ import annotations

import os
import re
import resource
import subprocess
import time
from dataclasses import replace
from itertools import zip_longest
from uuid import uuid4

from .backends.base import ModelBackend
from .config import AppConfig
from .contexts import (DISCUSSION_SYSTEM, FRESH_INSTRUCTION, PacketBudgetError,
    TaskPacket, conservative_tokens, parse_discussion, prepare_request, summarize_failures)
from .feedback.base import LeanFeedbackProvider
from .informal.base import InformalReasoner
from .output_budget import (RECOVERY_INSTRUCTION, choose_output_budget,
                            output_status, repetitive_output)
from .prompts import SYSTEM_PROMPT, initial_prompt, repair_prompt, reset_repair_prompt, rewrite_repair_prompt
from .proof_body import BODY_CONTRACT, ProofSlot, ProofSlotError, body_prompt, target_assignment
from .portfolio import try_portfolio
from .retrieval.base import SemanticRetriever
from .telemetry import JSONLTelemetry
from .types import (
    AttemptMetrics,
    AttemptResult,
    ChatMessage,
    FallbackAttempt,
    FailureCategory,
    GenerationResult,
    InformalReasoningResult,
    InformalTaskPacket,
    IterationRecord,
    LeanFeedback,
    RetrievalResult,
    VerificationResult,
    V4RequestRecord,
)
from .verification.base import LeanVerifier


class ContextBudgetError(ValueError):
    pass


class ProofAgent:
    def __init__(
        self,
        backend: ModelBackend,
        verifier: LeanVerifier,
        config: AppConfig,
        telemetry: JSONLTelemetry | None = None,
        feedback_provider: LeanFeedbackProvider | None = None,
        retriever: SemanticRetriever | None = None,
        informal_reasoner: InformalReasoner | None = None,
    ):
        self.backend = backend
        self.verifier = verifier
        self.config = config
        self.telemetry = telemetry or JSONLTelemetry(config.agent.log_path)
        self.feedback_provider = feedback_provider
        self.retriever = retriever
        self.informal_reasoner = informal_reasoner

    def solve(self, theorem: str) -> AttemptResult:
        attempt_id = uuid4().hex
        started = time.monotonic()
        metrics = AttemptMetrics()
        records: list[IterationRecord] = []
        fallback_attempts: list[FallbackAttempt] = []
        attempted_fallbacks: set[str] = set()
        retrieval_cache: dict[str, RetrievalResult] = {}
        informal_result: InformalReasoningResult | None = None
        strategy_retrieval: tuple[RetrievalResult, ...] = ()
        v4_requests: list[V4RequestRecord] = []
        discussion = ""
        candidate = ""
        last_failure = FailureCategory.UNKNOWN
        stop_reason = "iteration_budget"
        error_message: str | None = None
        model_loaded = False
        proof_slot = None
        self.telemetry.emit("attempt_started", attempt_id, {"theorem": theorem})

        try:
            if self.config.generation.proof_format == "proof_body":
                proof_slot = ProofSlot.from_source(theorem)
            should_generate = True
            if has_concrete_proof_candidate(theorem):
                candidate = theorem
                self.telemetry.emit("lean_check_started", attempt_id, {"source": "input candidate"})
                verification = self.verifier.verify(
                    candidate, attempt_id=f"{attempt_id}-input"
                )
                metrics.kimina_checks += 1
                self.telemetry.emit("lean_check_completed", attempt_id, verification)
                lean_feedback = self._inspect_failure(
                    candidate,
                    verification,
                    attempt_id=f"{attempt_id}-input",
                    metrics=metrics,
                )
                last_failure = verification.failure_category
                seed_record = IterationRecord(
                    iteration=0,
                    candidate=candidate,
                    verification=verification,
                    generation=GenerationResult(
                        text="",
                        model="input_candidate",
                        finish_reason="input",
                    ),
                    estimated_context_tokens=0,
                    lean_feedback=lean_feedback,
                )
                records.append(seed_record)
                self.telemetry.emit("input_candidate_checked", attempt_id, seed_record)
                if verification.valid:
                    last_failure = FailureCategory.NONE
                    stop_reason = "verified"
                    should_generate = False
                elif verification.failure_category == FailureCategory.VERIFIER_UNAVAILABLE:
                    stop_reason = "verifier_unavailable"
                    error_message = (
                        verification.diagnostics[0]
                        if verification.diagnostics
                        else "Lean verifier is unavailable"
                    )
                    should_generate = False
                elif self._lsp_required_but_unavailable(lean_feedback):
                    last_failure = FailureCategory.LEAN_LSP_UNAVAILABLE
                    stop_reason = "lean_lsp_unavailable"
                    error_message = lean_feedback.error_message
                    should_generate = False

            first_retrieval: RetrievalResult | None = None
            if should_generate:
                first_retrieval = self._retrieve_for_prompt(
                    theorem,
                    previous=records[-1] if records else None,
                    attempt_id=attempt_id,
                    metrics=metrics,
                    cache=retrieval_cache,
                )
                if self._retrieval_required_but_unavailable(first_retrieval):
                    last_failure = FailureCategory.RETRIEVAL_UNAVAILABLE
                    stop_reason = "retrieval_unavailable"
                    error_message = first_retrieval.error_message
                    should_generate = False

            if should_generate:
                load_started = time.monotonic()
                self.telemetry.emit("model_loading", attempt_id, {"model": self.config.mlx.model_id})
                metrics.model_load_seconds = self.backend.load_model(
                    self.config.mlx.model_id
                )
                model_loaded = True
                self.telemetry.emit("model_loaded", attempt_id, {"model": self.config.mlx.model_id})
                if metrics.model_load_seconds == 0.0:
                    metrics.model_load_seconds = time.monotonic() - load_started
                metrics.memory_samples_mb.append(
                    _memory_sample(self.backend, self.retriever)
                )

            for iteration in (
                range(1, self.config.agent.max_iterations + 1)
                if should_generate
                else ()
            ):
                reset_context = False
                retrieval = (
                    first_retrieval
                    if iteration == 1
                    else self._retrieve_for_prompt(
                        theorem,
                        previous=records[-1] if records else None,
                        attempt_id=attempt_id,
                        metrics=metrics,
                        cache=retrieval_cache,
                    )
                )
                if self._retrieval_required_but_unavailable(retrieval):
                    last_failure = FailureCategory.RETRIEVAL_UNAVAILABLE
                    stop_reason = "retrieval_unavailable"
                    error_message = retrieval.error_message
                    break
                retrieved_declarations = render_retrieval(
                    retrieval, self.config.lean_explore.max_context_chars
                )
                if self._should_invoke_informal(records, informal_result):
                    informal_result = self._run_informal_reasoning(
                        theorem,
                        records=records,
                        retrieved_declarations=retrieved_declarations,
                        attempt_id=attempt_id,
                        metrics=metrics,
                    )
                    strategy_retrieval = self._retrieve_for_strategy(
                        informal_result, attempt_id=attempt_id, metrics=metrics,
                        cache=retrieval_cache,
                    )
                unavailable = next((r for r in strategy_retrieval
                                    if self._retrieval_required_but_unavailable(r)), None)
                if unavailable is not None:
                    last_failure = FailureCategory.RETRIEVAL_UNAVAILABLE
                    stop_reason = "retrieval_unavailable"
                    error_message = unavailable.error_message
                    break
                if strategy_retrieval:
                    retrieved_declarations = render_strategy_retrieval(
                        retrieval, strategy_retrieval, self.config.lean_explore.max_context_chars
                    )
                informal_guidance = render_informal_guidance(
                    informal_result,
                    max_proof_chars=self.config.informal_reasoning.max_proof_chars,
                    max_critique_chars=self.config.informal_reasoning.max_critique_chars,
                )
                v4 = self.config.v4
                failures = sum(r.iteration > 0 and not r.verification.valid for r in records)
                summary, source_iterations = summarize_failures(records, v4.summary_max_chars)
                latest = records[-1] if records else None
                packet = TaskPacket(
                    task=task_skeleton(theorem),
                    goal_and_hypotheses=(latest.lean_feedback.goal_state or "")
                        if latest and latest.lean_feedback else "",
                    diagnostics="\n".join(latest.verification.diagnostics) if latest else "",
                    retrieved_declarations=retrieved_declarations,
                    informal_outline=informal_guidance,
                    failed_attempts=summary,
                    discussion=discussion,
                )
                if (v4.enabled and v4.discussion_enabled
                    and metrics.discussion_partner_calls < v4.max_discussion_calls
                    and failures >= v4.trigger_after_failures * (metrics.discussion_partner_calls + 1)):
                    advice = self._discuss(packet, iteration, source_iterations,
                                           attempt_id, metrics, v4_requests)
                    if advice:
                        discussion = advice
                        packet = replace(packet, discussion=advice)
                if v4.enabled and discussion:
                    informal_guidance += "\nDiscussion partner advice (unverified):\n" + discussion
                previous = records[-1] if records else None
                repetitive = bool(candidate and (is_degenerate_candidate(candidate)
                    or candidate_repeats_rejected_attempt(records)))
                if self.config.generation.output_budget_policy != "legacy":
                    repetitive = repetitive or repetitive_output(candidate)
                budget_decision = choose_output_budget(self.config.generation, previous,
                                                       repetitive=repetitive)
                reset_context = budget_decision.reset
                if not candidate:
                    repair_action = "initial"
                    prompt = initial_prompt(
                        theorem, retrieved_declarations, informal_guidance
                    )
                else:
                    previous = records[-1]
                    diagnostics = "\n".join(previous.verification.diagnostics)[
                        : self.config.agent.diagnostics_max_chars
                    ]
                    lean_lsp_feedback = render_lean_feedback(previous.lean_feedback)
                    rejected_history = render_rejected_history(records)
                    prompt = (
                        reset_repair_prompt(
                            theorem,
                            diagnostics,
                            iteration - 1,
                            lean_lsp_feedback,
                            rejected_history,
                            retrieved_declarations,
                            informal_guidance,
                        )
                        if reset_context
                        else repair_prompt(
                            theorem,
                            candidate,
                            diagnostics,
                            lean_lsp_feedback,
                            rejected_history,
                            retrieved_declarations,
                            informal_guidance,
                        )
                    )
                    repair_action = "fresh_reset" if reset_context else "repair"
                    if (self.config.informal_reasoning.enabled
                        and self.config.informal_reasoning.rewrite_recovery_enabled
                        and budget_decision.action not in {"expand_truncated", "retry_at_ceiling"}
                        and has_rewrite_failure(previous.verification.diagnostics)):
                        prompt = rewrite_repair_prompt(
                            task_skeleton(theorem), diagnostics, lean_lsp_feedback,
                            retrieved_declarations, informal_guidance,
                        )
                        reset_context = True
                        repair_action = "invalid_rewrite_reset"
                        metrics.rewrite_recovery_prompts += 1
                        self.telemetry.emit("formal_repair_strategy_changed", attempt_id, {
                            "iteration": iteration, "action": repair_action,
                            "reason": "Lean rejected a rewrite argument or could not find its pattern",
                        })
                output_recovery = budget_decision.action in {"expand_truncated", "retry_at_ceiling"}
                if output_recovery:
                    prompt += RECOVERY_INSTRUCTION
                    repair_action = "output_budget_recovery"
                formal_system = SYSTEM_PROMPT
                if proof_slot is not None:
                    formal_system = body_prompt(formal_system) + BODY_CONTRACT
                    prompt = body_prompt(prompt)
                messages = [
                    ChatMessage("system", formal_system),
                    ChatMessage("user", prompt),
                ]
                fresh = (v4.enabled and v4.fresh_context_enabled
                    and metrics.fresh_subproblem_calls < v4.max_fresh_calls
                    and failures >= v4.trigger_after_failures * (metrics.fresh_subproblem_calls + 1))
                request_record = None
                budget_decision = choose_output_budget(self.config.generation, previous,
                    repetitive=repetitive, fresh=fresh, fresh_limit=v4.fresh_max_output_tokens)
                output_limit = budget_decision.tokens
                if reset_context and self.config.generation.output_budget_policy == "legacy":
                    output_limit = min(self.config.generation.reset_output_tokens, output_limit)
                if v4.enabled:
                    context_size = conservative_tokens(tuple(messages))
                    if fresh or (v4.compression_enabled and context_size + output_limit > self.config.generation.max_context_tokens):
                        try:
                            request_record = prepare_request(packet,
                                system=formal_system + "\n"
                                    + (body_prompt(FRESH_INSTRUCTION) if proof_slot else FRESH_INSTRUCTION)
                                    + ((body_prompt(RECOVERY_INSTRUCTION) if proof_slot else RECOVERY_INSTRUCTION)
                                       if output_recovery else ""),
                                role="fresh_subproblem" if fresh else "main_compressed",
                                iteration=iteration, context_limit=(v4.max_context_tokens if fresh
                                    else self.config.generation.max_context_tokens),
                                output_limit=output_limit, thinking=False,
                                compress=v4.compression_enabled, sources=source_iterations)
                        except PacketBudgetError as exc:
                            raise ContextBudgetError(str(exc)) from exc
                        messages = list(request_record.messages)
                        context_size = request_record.estimated_context_tokens
                        repair_action = "fresh_subproblem" if fresh else "context_compression"
                        metrics.context_compressions += 1
                    else:
                        request_record = V4RequestRecord("main", iteration, tuple(messages),
                            context_size, output_limit, self.config.generation.enable_thinking)
                    v4_requests.append(request_record)
                    metrics.v4_context_sizes.append(context_size)
                    self.telemetry.emit("v4_request_started", attempt_id, request_record)
                else:
                    context_size = (estimate_message_tokens(messages)
                        if self.config.generation.output_budget_policy == "legacy"
                        else conservative_tokens(tuple(messages)))
                if context_size + output_limit > self.config.generation.max_context_tokens:
                    raise ContextBudgetError(
                        f"Estimated request ({context_size} input + "
                        f"{output_limit} output tokens) exceeds "
                        f"the {self.config.generation.max_context_tokens}-token limit"
                    )
                budget_request = {
                    "iteration": iteration, "policy": self.config.generation.output_budget_policy,
                    "proof_format": self.config.generation.proof_format,
                    "action": budget_decision.action, "previous_status": budget_decision.previous_status,
                    "role": "fresh_subproblem" if fresh else "main",
                    "requested_output_tokens": output_limit, "estimated_input_tokens": context_size,
                    "context_limit": min(v4.max_context_tokens, self.config.generation.max_context_tokens)
                        if fresh else self.config.generation.max_context_tokens,
                }
                metrics.formal_output_budget_requests.append(budget_request)
                metrics.formal_output_budget_increases += int(budget_decision.action == "expand_truncated")
                self.telemetry.emit("formal_output_budget_selected", attempt_id, budget_request)
                metrics.formal_call_attempts += 1
                if fresh:
                    metrics.fresh_subproblem_calls += 1
                else:
                    metrics.main_agent_calls += 1
                generation = self.backend.chat(
                    messages,
                    max_tokens=output_limit,
                    temperature=self.config.generation.temperature,
                    top_p=self.config.generation.top_p,
                    extra={
                        "chat_template_kwargs": {
                            "enable_thinking": (request_record.enable_thinking if request_record
                                                else self.config.generation.enable_thinking)
                        }
                    },
                )
                if request_record is not None:
                    request_record.generation = generation
                    request_record.status = "candidate_generated"
                    self.telemetry.emit("v4_request_completed", attempt_id, request_record)
                metrics.model_calls += 1
                metrics.prompt_tokens += generation.usage.prompt_tokens
                metrics.completion_tokens += generation.usage.completion_tokens
                metrics.context_sizes.append(
                    generation.usage.prompt_tokens or context_size
                )
                candidate = extract_lean_code(generation.text)
                integrity_error = None
                proof_body_normalization = None
                if proof_slot is not None:
                    try:
                        candidate, proof_body_normalization = proof_slot.assemble_response(candidate)
                    except ValueError as exc:
                        proof_body_normalization = "rejected"
                        integrity_error = str(exc)
                        # Keep a task-preserving failed candidate for diagnostics;
                        # the rejected raw response remains in generation.text.
                        candidate = theorem
                    self.telemetry.emit("proof_body_response_normalized", attempt_id, {
                        "iteration": iteration, "action": proof_body_normalization,
                        "error": integrity_error,
                    })
                integrity_error = integrity_error or validate_task_preserved(theorem, candidate)
                if integrity_error:
                    lean_feedback = None
                    verification = VerificationResult(
                        valid=False,
                        diagnostics=(integrity_error,),
                        failure_category=FailureCategory.TASK_MUTATION,
                    )
                elif repeated_record := find_rejected_duplicate(records, candidate):
                    lean_feedback = repeated_record.lean_feedback
                    previous_diagnostics = repeated_record.verification.diagnostics
                    verification = VerificationResult(
                        valid=False,
                        diagnostics=(
                            "Candidate exactly repeats a previously rejected proof. "
                            "Use a materially different tactic or theorem.",
                            *previous_diagnostics,
                        ),
                        failure_category=repeated_record.verification.failure_category,
                    )
                else:
                    self.telemetry.emit("lean_check_started", attempt_id, {"source": "model candidate", "iteration": iteration})
                    verification = self.verifier.verify(
                        candidate, attempt_id=f"{attempt_id}-{iteration}"
                    )
                    metrics.kimina_checks += 1
                    self.telemetry.emit("lean_check_completed", attempt_id, verification)
                    lean_feedback = self._inspect_failure(
                        candidate,
                        verification,
                        attempt_id=f"{attempt_id}-{iteration}",
                        metrics=metrics,
                    )
                metrics.iterations = iteration
                last_failure = verification.failure_category
                repeated_output = (is_degenerate_candidate(candidate) or repetitive_output(candidate)
                                   or find_rejected_duplicate(records, candidate) is not None)
                generation_status = output_status(generation.finish_reason, repeated_output)
                metrics.formal_output_truncations += int(generation.finish_reason == "length")
                metrics.formal_output_repetitions += int(repeated_output)
                record = IterationRecord(
                    iteration=iteration,
                    candidate=candidate,
                    verification=verification,
                    generation=generation,
                    estimated_context_tokens=context_size,
                    lean_feedback=lean_feedback,
                    retrieval=retrieval,
                    strategy_retrieval=strategy_retrieval,
                    repair_action=repair_action,
                    requested_output_tokens=output_limit,
                    output_status=generation_status,
                    output_budget_action=budget_decision.action,
                    proof_body_normalization=proof_body_normalization,
                )
                records.append(record)
                metrics.memory_samples_mb.append(
                    _memory_sample(self.backend, self.retriever)
                )
                self.telemetry.emit("iteration_completed", attempt_id, record)
                if verification.valid:
                    last_failure = FailureCategory.NONE
                    stop_reason = "verified"
                    break
                if verification.failure_category == FailureCategory.VERIFIER_UNAVAILABLE:
                    stop_reason = "verifier_unavailable"
                    error_message = (
                        verification.diagnostics[0]
                        if verification.diagnostics
                        else "Lean verifier is unavailable"
                    )
                    break
                if self._lsp_required_but_unavailable(lean_feedback):
                    last_failure = FailureCategory.LEAN_LSP_UNAVAILABLE
                    stop_reason = "lean_lsp_unavailable"
                    error_message = lean_feedback.error_message
                    break
                salvage = self._try_rewrite_prefix_salvage(
                    theorem,
                    candidate,
                    verification,
                    attempt_id=attempt_id,
                    iteration=iteration,
                    metrics=metrics,
                    attempts=fallback_attempts,
                    attempted=attempted_fallbacks,
                )
                if salvage is not None:
                    candidate, verification = salvage
                    last_failure = verification.failure_category
                    if verification.valid:
                        last_failure = FailureCategory.NONE
                        stop_reason = "verified"
                        records.append(
                            IterationRecord(
                                iteration=iteration,
                                candidate=candidate,
                                verification=verification,
                                generation=GenerationResult(
                                    text=candidate,
                                    model="compiler_prefix_salvage",
                                    finish_reason="verified_rewrite_salvage",
                                ),
                                estimated_context_tokens=0,
                                repair_action="rewrite_prefix_salvage",
                            )
                        )
                        break
                    stop_reason = "verifier_unavailable"
                    error_message = (
                        verification.diagnostics[0]
                        if verification.diagnostics
                        else "Lean verifier became unavailable during prefix salvage"
                    )
                    break
                fallback = self._try_fallback_portfolio(
                    theorem,
                    attempt_id=attempt_id,
                    iteration=iteration,
                    metrics=metrics,
                    attempts=fallback_attempts,
                    attempted=attempted_fallbacks,
                )
                if fallback is not None:
                    candidate, verification = fallback
                    last_failure = verification.failure_category
                    if verification.valid:
                        last_failure = FailureCategory.NONE
                        stop_reason = "verified"
                        records.append(
                            IterationRecord(
                                iteration=iteration,
                                candidate=candidate,
                                verification=verification,
                                generation=GenerationResult(
                                    text=candidate,
                                    model="v1_1_fallback",
                                    finish_reason="verified_fallback",
                                ),
                                estimated_context_tokens=0,
                            )
                        )
                        break
                    stop_reason = "verifier_unavailable"
                    error_message = (
                        verification.diagnostics[0]
                        if verification.diagnostics
                        else "Lean verifier became unavailable during fallback checking"
                    )
                    break
        except ProofSlotError as exc:
            last_failure = FailureCategory.TASK_MUTATION
            stop_reason = "unsupported_proof_slot"
            error_message = str(exc)
            self.telemetry.emit("attempt_error", attempt_id, {"error": str(exc)})
        except ContextBudgetError as exc:
            last_failure = FailureCategory.CONTEXT_BUDGET
            stop_reason = "context_budget"
            error_message = str(exc)
            self.telemetry.emit("attempt_error", attempt_id, {"error": str(exc)})
        except Exception as exc:
            last_failure = (
                FailureCategory.MODEL_UNAVAILABLE
                if not records
                else FailureCategory.UNKNOWN
            )
            stop_reason = "runtime_error"
            error_message = f"{type(exc).__name__}: {exc}"
            self.telemetry.emit(
                "attempt_error",
                attempt_id,
                {"error": error_message},
            )
        finally:
            for request in v4_requests:
                if request.status == "pending":
                    request.status = "error"
                    request.error_message = error_message or "Request did not complete"
                    self.telemetry.emit("v4_request_failed", attempt_id, request)
            if self.config.agent.unload_model_after_attempt and model_loaded:
                self.telemetry.emit("model_unloading", attempt_id, {})
                metrics.model_unload_seconds = self.backend.unload_model()
                self.telemetry.emit("model_unloaded", attempt_id, {})
            metrics.wall_clock_seconds = time.monotonic() - started

        success = bool(records and records[-1].verification.valid)
        result = AttemptResult(
            attempt_id=attempt_id,
            theorem=theorem,
            success=success,
            final_proof=candidate,
            failure_category=FailureCategory.NONE if success else last_failure,
            stop_reason=stop_reason,
            error_message=error_message,
            iterations=records,
            metrics=metrics,
            fallback_attempts=fallback_attempts,
            informal_reasoning=informal_result,
            v4_requests=v4_requests,
        )
        self.telemetry.emit("attempt_completed", attempt_id, result)
        return result

    def _discuss(self, packet: TaskPacket, iteration: int, sources: tuple[int, ...],
                 attempt_id: str, metrics: AttemptMetrics,
                 requests: list[V4RequestRecord]) -> str:
        config = self.config.v4
        try:
            request = prepare_request(packet, system=DISCUSSION_SYSTEM,
                role="discussion_partner", iteration=iteration,
                context_limit=config.max_context_tokens,
                output_limit=config.discussion_max_output_tokens, thinking=True,
                compress=config.compression_enabled, sources=sources)
        except PacketBudgetError as exc:
            self.telemetry.emit("v4_discussion_skipped", attempt_id, {"error": str(exc)})
            return ""
        requests.append(request)
        metrics.discussion_partner_calls += 1
        metrics.v4_context_sizes.append(request.estimated_context_tokens)
        metrics.context_compressions += bool(request.compressed_fields)
        self.telemetry.emit("v4_request_started", attempt_id, request)
        advice = ""
        try:
            response = self.backend.chat(request.messages,
                max_tokens=request.max_output_tokens, temperature=0.3, top_p=0.95,
                extra={"chat_template_kwargs": {"enable_thinking": True}})
            request.generation = response
            metrics.discussion_prompt_tokens += response.usage.prompt_tokens
            metrics.discussion_completion_tokens += response.usage.completion_tokens
            if response.finish_reason == "length":
                raise ValueError("Discussion exhausted its output budget")
            advice = parse_discussion(response.text)
            request.status = "advice_available"
        except Exception as exc:
            request.status = "error"
            request.error_message = f"{type(exc).__name__}: {exc}"
        self.telemetry.emit("v4_request_completed", attempt_id, request)
        return advice

    def _should_invoke_informal(
        self,
        records: list[IterationRecord],
        informal_result: InformalReasoningResult | None,
    ) -> bool:
        informal = self.config.informal_reasoning
        if not informal.enabled or informal_result is not None:
            return False
        if informal.invocation_policy == "always":
            return True
        rejected_model_candidates = sum(
            record.iteration > 0 and not record.verification.valid
            for record in records
        )
        return rejected_model_candidates >= informal.trigger_after_failures

    def _run_informal_reasoning(
        self,
        theorem: str,
        *,
        records: list[IterationRecord],
        retrieved_declarations: str,
        attempt_id: str,
        metrics: AttemptMetrics,
    ) -> InformalReasoningResult:
        previous = records[-1] if records else None
        current_goal = (
            previous.lean_feedback.goal_state
            if previous and previous.lean_feedback and previous.lean_feedback.goal_state
            else ""
        )
        diagnostics = (
            "\n".join(previous.verification.diagnostics)[
                : self.config.agent.diagnostics_max_chars
            ]
            if previous
            else ""
        )
        packet = InformalTaskPacket(
            theorem=theorem_statement(theorem),
            current_goal=current_goal,
            retrieved_declarations=retrieved_declarations,
            diagnostics=diagnostics,
            rejected_strategies=render_rejected_history(records),
        )
        if self.informal_reasoner is None:
            result = InformalReasoningResult(
                triggered=True,
                accepted=False,
                rounds=0,
                final_proof="",
                stop_reason="unavailable",
                error_message=(
                    "Informal reasoning is enabled but no reasoner was configured"
                ),
            )
        else:
            try:
                result = self.informal_reasoner.reason_with_events(packet,
                    lambda event, payload: self.telemetry.emit(event, attempt_id, payload))
            except Exception as exc:
                # Optional reasoning must not prevent the formal repair path.
                result = InformalReasoningResult(
                    triggered=True, accepted=False, rounds=0, final_proof="",
                    stop_reason="runtime_error", error_message=f"{type(exc).__name__}: {exc}",
                )
        metrics.informal_generator_calls += result.generator_calls
        metrics.informal_verifier_calls += result.verifier_calls
        generations = [draft.generation for draft in result.drafts] + [
            review.generation for review in result.reviews
        ]
        metrics.informal_prompt_tokens += sum(
            generation.usage.prompt_tokens for generation in generations
        )
        metrics.informal_completion_tokens += sum(
            generation.usage.completion_tokens for generation in generations
        )
        metrics.informal_context_sizes.extend(
            [request.estimated_context_tokens for request in result.requests]
            if result.requests else
            [draft.estimated_context_tokens for draft in result.drafts]
            + [review.estimated_context_tokens for review in result.reviews]
        )
        metrics.memory_samples_mb.append(_memory_sample(self.backend, self.retriever))
        self.telemetry.emit("informal_reasoning_completed", attempt_id, result)
        return result

    def _inspect_failure(
        self,
        candidate: str,
        verification: VerificationResult,
        *,
        attempt_id: str,
        metrics: AttemptMetrics,
    ) -> LeanFeedback | None:
        if (
            verification.valid
            or verification.failure_category == FailureCategory.VERIFIER_UNAVAILABLE
            or not self.config.lean_lsp.enabled
        ):
            return None
        if self.feedback_provider is None:
            feedback = LeanFeedback(
                available=False,
                error_message="Lean-LSP-MCP is enabled but no feedback provider was configured",
            )
        else:
            self.telemetry.emit("lean_lsp_started", attempt_id, {})
            feedback = self.feedback_provider.inspect(
                candidate,
                compiler_diagnostics=verification.diagnostics,
                attempt_id=attempt_id,
            )
        metrics.lean_lsp_calls += feedback.tool_calls
        self.telemetry.emit("lean_lsp_inspected", attempt_id, feedback)
        return feedback

    def _lsp_required_but_unavailable(
        self, feedback: LeanFeedback | None
    ) -> bool:
        return bool(
            self.config.lean_lsp.enabled
            and self.config.lean_lsp.required
            and feedback is not None
            and not feedback.available
        )

    def _try_fallback_portfolio(
        self,
        theorem: str,
        *,
        attempt_id: str,
        iteration: int,
        metrics: AttemptMetrics,
        attempts: list[FallbackAttempt],
        attempted: set[str],
    ) -> tuple[str, VerificationResult] | None:
        if not self.config.agent.fallback_enabled:
            return None
        return try_portfolio(theorem, verifier=self.verifier, config=self.config,
                             attempt_id=f"{attempt_id}-{iteration}", metrics=metrics,
                             attempts=attempts, attempted=attempted, telemetry=self.telemetry)

    def _try_rewrite_prefix_salvage(
        self,
        theorem: str,
        candidate: str,
        verification: VerificationResult,
        *,
        attempt_id: str,
        iteration: int,
        metrics: AttemptMetrics,
        attempts: list[FallbackAttempt],
        attempted: set[str],
    ) -> tuple[str, VerificationResult] | None:
        config = self.config.informal_reasoning
        if (not config.enabled or not config.rewrite_recovery_enabled
            or not config.rewrite_salvage_enabled
            or not has_rewrite_failure(verification.diagnostics)):
            return None
        for tactic in config.rewrite_salvage_tactics:
            variants = rewrite_prefix_candidates(candidate, verification.diagnostics, tactic)
            if not variants:
                return None
            for label, salvaged in variants:
                if metrics.rewrite_salvage_checks >= config.rewrite_salvage_max_checks:
                    return None
                if validate_task_preserved(theorem, salvaged) is not None:
                    continue
                fingerprint = strategy_fingerprint(salvaged)
                if fingerprint in attempted:
                    continue
                attempted.add(fingerprint)
                self.telemetry.emit("salvage_check_started", attempt_id, {"tactic": tactic})
                checked = self.verifier.verify(
                    salvaged,
                    attempt_id=f"{attempt_id}-{iteration}-rewrite-salvage-{len(attempts) + 1}",
                )
                metrics.kimina_checks += 1
                metrics.fallback_checks += 1
                metrics.rewrite_salvage_checks += 1
                fallback = FallbackAttempt(f"rewrite_prefix:{label}:{tactic}", salvaged, checked)
                attempts.append(fallback)
                self.telemetry.emit("rewrite_prefix_salvage_checked", attempt_id, fallback)
                if checked.valid:
                    metrics.rewrite_salvage_successes += 1
                    return salvaged, checked
                if checked.failure_category == FailureCategory.VERIFIER_UNAVAILABLE:
                    return salvaged, checked
        return None

    def _retrieve_for_prompt(
        self,
        theorem: str,
        *,
        previous: IterationRecord | None,
        attempt_id: str,
        metrics: AttemptMetrics,
        cache: dict[str, RetrievalResult],
    ) -> RetrievalResult | None:
        if not self.config.lean_explore.enabled:
            return None
        query = build_retrieval_query(
            theorem,
            previous,
            max_chars=self.config.lean_explore.max_query_chars,
        )
        return self._retrieve_query(query, attempt_id=attempt_id, metrics=metrics, cache=cache)

    def _retrieve_for_strategy(
        self, reasoning: InformalReasoningResult, *, attempt_id: str,
        metrics: AttemptMetrics, cache: dict[str, RetrievalResult],
    ) -> tuple[RetrievalResult, ...]:
        config = self.config.informal_reasoning
        if (not self.config.lean_explore.enabled or not config.strategy_retrieval_enabled
            or not reasoning.final_proof
            or not (reasoning.accepted or reasoning.stop_reason == "generator_only")):
            return ()
        # Only the final accepted (or explicitly unreviewed ablation) outline's
        # queries are used. No hidden thinking, rejected drafts or extra model calls.
        queries = tuple(dict.fromkeys(reasoning.lemma_queries))[:config.strategy_query_limit]
        return tuple(self._retrieve_query(
            query, attempt_id=attempt_id, metrics=metrics, cache=cache, strategy=True,
        ) for query in queries if query.strip()
            and len(query) <= min(config.strategy_query_max_chars,
                                 self.config.lean_explore.max_query_chars))

    def _retrieve_query(
        self, query: str, *, attempt_id: str, metrics: AttemptMetrics,
        cache: dict[str, RetrievalResult], strategy: bool = False,
    ) -> RetrievalResult:
        if query in cache:
            return cache[query]
        if self.retriever is None:
            result = RetrievalResult(
                available=False,
                query=query,
                error_message=(
                    "LeanExplore is enabled but no semantic retriever was configured"
                ),
            )
        else:
            self.telemetry.emit("semantic_retrieval_started", attempt_id, {"query": query, "strategy": strategy})
            result = self.retriever.retrieve(query)
        cache[query] = result
        metrics.retrieval_queries += 1
        if strategy:
            metrics.strategy_retrieval_queries += 1
        metrics.retrieval_calls += result.tool_calls
        metrics.retrieval_latency_seconds += result.elapsed_seconds
        self.telemetry.emit("semantic_retrieval_completed", attempt_id, result)
        if strategy:
            self.telemetry.emit("strategy_retrieval_completed", attempt_id, result)
        return result

    def _retrieval_required_but_unavailable(
        self, retrieval: RetrievalResult | None
    ) -> bool:
        return bool(
            self.config.lean_explore.enabled
            and self.config.lean_explore.required
            and retrieval is not None
            and not retrieval.available
        )


def extract_lean_code(text: str) -> str:
    lean_match = re.search(r"```lean\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if lean_match:
        return lean_match.group(1).strip() + "\n"
    generic_match = re.search(r"```\s*(.*?)```", text, flags=re.DOTALL)
    if generic_match:
        return generic_match.group(1).strip() + "\n"
    # A length-limited completion can omit the closing fence. Keep the Lean
    # content but never pass the Markdown fence itself to the compiler.
    unclosed_match = re.search(r"```(?:lean)?\s*", text, flags=re.IGNORECASE)
    if unclosed_match:
        return text[unclosed_match.end() :].strip() + "\n"
    return text.strip() + "\n"


def estimate_message_tokens(messages: list[ChatMessage]) -> int:
    # Conservative tokenizer-independent guard. MLX's exact usage is logged after calls.
    characters = sum(len(message.role) + len(message.content) for message in messages)
    return max(1, (characters + 2) // 3 + 8 * len(messages))


def render_lean_feedback(feedback: LeanFeedback | None) -> str:
    if feedback is None:
        return ""
    if not feedback.available:
        return f"Lean-LSP-MCP unavailable: {feedback.error_message or 'unknown error'}"
    parts: list[str] = []
    if feedback.diagnostics:
        parts.append("LSP diagnostics:\n" + "\n".join(feedback.diagnostics))
    if feedback.goal_state:
        parts.append("Interactive goal state:\n" + feedback.goal_state)
    return "\n\n".join(parts)


def build_retrieval_query(
    theorem: str,
    previous: IterationRecord | None = None,
    *,
    max_chars: int = 4_000,
) -> str:
    """Build a stable query from the task and current goal, never failed proof text."""
    assignment = _target_assignment_index(theorem)
    statement = theorem[:assignment].rstrip() if assignment is not None else theorem.rstrip()
    parts = ["Lean declaration to prove:\n" + statement]
    goal = previous.lean_feedback.goal_state if previous and previous.lean_feedback else None
    if goal:
        parts.append("Current Lean goal:\n" + goal)
    query = "\n\n".join(parts)
    return query[:max_chars]


def theorem_statement(theorem: str) -> str:
    """Return the immutable Lean task prefix without the existing proof body."""
    assignment = _target_assignment_index(theorem)
    return theorem[:assignment].rstrip() if assignment is not None else theorem.rstrip()


def task_skeleton(theorem: str) -> str:
    """Preserve imports and target while omitting any rejected input proof body."""
    return theorem_statement(theorem) + " := by\n  -- proof required"


def render_informal_guidance(
    result: InformalReasoningResult | None,
    *,
    max_proof_chars: int = 6_000,
    max_critique_chars: int = 4_000,
) -> str:
    """Render bounded advisory mathematics; never represent it as verified proof."""
    if result is None or not result.final_proof:
        return ""
    status = (
        "accepted by the independent informal reviewer"
        if result.accepted
        else f"not accepted; informal loop stopped with {result.stop_reason}"
    )
    parts = [
        f"Status: {status}. This status is not a correctness result.",
        "Mathematical outline:\n" + result.final_proof[:max_proof_chars],
    ]
    if result.reviews and result.reviews[-1].critique:
        parts.append(
            "Latest independent critique:\n"
            + result.reviews[-1].critique[:max_critique_chars]
        )
    return "\n\n".join(parts)


def render_retrieval(
    retrieval: RetrievalResult | None,
    max_chars: int = 12_000,
) -> str:
    """Render bounded, provenance-bearing LeanExplore evidence for a prompt."""
    if retrieval is None:
        return ""
    if not retrieval.available:
        return "LeanExplore unavailable: " + (
            retrieval.error_message or "unknown retrieval error"
        )
    if not retrieval.hits:
        return "LeanExplore returned no matching declarations."
    header = "LeanExplore local results"
    if retrieval.data_version:
        header += f" (data version {retrieval.data_version})"
    parts = [header + ":"]
    for index, hit in enumerate(retrieval.hits, start=1):
        item = f"{index}. declaration_id={hit.declaration_id}; exact_name={hit.name}"
        if hit.description:
            item += "\nDescription: " + hit.description.strip()
        if hit.source_text:
            item += "\nLean source:\n" + hit.source_text.strip()
        proposed = "\n\n".join([*parts, item])
        if len(proposed) > max_chars:
            remaining = max_chars - len("\n\n".join(parts)) - 2
            if remaining > 80:
                parts.append(item[:remaining].rstrip() + "\n[truncated]")
            break
        parts.append(item)
    return "\n\n".join(parts)[:max_chars]


def render_strategy_retrieval(
    task: RetrievalResult | None, strategies: tuple[RetrievalResult, ...], max_chars: int,
) -> str:
    """Round-robin strategy evidence first; bounded, deduplicated, no invented names."""
    hits = []
    seen = set()
    available = [result for result in strategies if result.available]
    for row in zip_longest(*(result.hits for result in available)):
        for hit in row:
            if hit is not None and hit.name not in seen:
                hits.append(hit)
                seen.add(hit.name)
    for hit in (task.hits if task and task.available else ()):
        if hit.name not in seen:
            hits.append(hit)
            seen.add(hit.name)
    if not hits:
        return render_retrieval(task, max_chars)
    merged = RetrievalResult(
        available=True, query="", hits=tuple(hits),
        data_version=available[0].data_version if available else (task.data_version if task else None),
    )
    return render_retrieval(merged, max_chars)


def has_rewrite_failure(diagnostics: tuple[str, ...]) -> bool:
    return any(marker in diagnostic.lower() for diagnostic in diagnostics
               for marker in ("invalid rewrite argument", "tactic `rewrite` failed",
                              "tactic 'rewrite' failed"))


def rewrite_prefix_candidate(
    candidate: str, diagnostics: tuple[str, ...], tactic: str,
) -> str | None:
    """Return the first safe rewrite-prefix variant, retained for public use."""
    variants = rewrite_prefix_candidates(candidate, diagnostics, tactic)
    return variants[0][1] if variants else None


def rewrite_prefix_candidates(
    candidate: str, diagnostics: tuple[str, ...], tactic: str,
) -> tuple[tuple[str, str], ...]:
    """Build bounded, Lean-checked repairs cut no later than the first error."""
    rewrite_line = None
    error_lines: list[int] = []
    for diagnostic in diagnostics:
        match = re.search(r"(?m)(?:^|\n)(\d+):(\d+):\s*error:", diagnostic)
        if match:
            error_lines.append(int(match.group(1)))
            if rewrite_line is None and has_rewrite_failure((diagnostic,)):
                rewrite_line = int(match.group(1))
    lines = candidate.rstrip().splitlines()
    if (rewrite_line is None or not 1 <= rewrite_line <= len(lines)
        or any(not 1 <= line <= len(lines) for line in error_lines)):
        return ()
    rewrite = lines[rewrite_line - 1]
    if not re.search(r"\b(?:rw|rewrite)\b", rewrite):
        return ()
    cut_line = min([line for line in error_lines if line <= rewrite_line], default=rewrite_line)
    prefix = lines[: cut_line - 1]
    if not prefix or _target_assignment_index("\n".join(prefix)) is None:
        return ()
    failing = lines[cut_line - 1]
    indent = re.match(r"\s*", failing).group(0)
    variants: list[tuple[str, str]] = []
    # When an earlier inline proof term already failed, retaining it would not
    # be a successful prefix. Preserve only its declared local proposition and
    # ask Lean's positivity tactic to prove it; Kimina still checks the result.
    if cut_line < rewrite_line and re.match(r"\s*have\b", failing) and ":=" in failing:
        declaration = failing.split(":=", 1)[0].rstrip() + " := by positivity"
        variants.append(("repair_have_positivity", "\n".join(
            [*prefix, declaration, indent + tactic]
        ) + "\n"))
    variants.append(("clean_prefix", "\n".join([*prefix, indent + tactic]) + "\n"))
    unique: dict[str, tuple[str, str]] = {}
    for label, source in variants:
        unique.setdefault(strategy_fingerprint(source), (label, source))
    return tuple(unique.values())


def render_rejected_history(
    records: list[IterationRecord], *, max_attempts: int = 4, max_chars: int = 4000
) -> str:
    """Render a bounded failure memory for otherwise fresh repair contexts."""
    rejected = [record for record in records if not record.verification.valid]
    parts: list[str] = []
    for record in rejected[-max_attempts:]:
        diagnostic = (
            record.verification.diagnostics[0]
            if record.verification.diagnostics
            else "Lean rejected this candidate"
        )
        parts.append(
            f"Attempt {record.iteration} rejected approach:\n"
            f"{_rejected_approach_excerpt(record.candidate)}\n"
            f"Diagnostic:\n{diagnostic[:600]}"
        )
    return "\n\n".join(parts)[-max_chars:]


def _rejected_approach_excerpt(candidate: str) -> str:
    lines: list[str] = []
    for raw_line in candidate.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("import ", "open ")):
            continue
        if line.startswith(("theorem ", "lemma ", "example ")):
            if ":=" not in line:
                continue
            line = line.split(":=", 1)[1].strip()
            if not line:
                continue
        if line not in lines:
            lines.append(line[:240])
        if len(lines) == 8:
            break
    return "\n".join(f"- {line}" for line in lines) or "- empty candidate"


def validate_task_preserved(task: str, candidate: str) -> str | None:
    if not candidate.strip():
        return "Candidate is empty; the original Lean task was not preserved"
    task_prefix = _immutable_task_prefix(task)
    candidate_prefix = _immutable_task_prefix(candidate)
    if task_prefix and task_prefix != candidate_prefix:
        return (
            "Candidate changed imports, declarations, or the theorem statement "
            "before the proof body"
        )
    required = _declaration_headers(task)
    if not required:
        return None
    present = set(_declaration_headers(candidate))
    missing = [header for header in required if header not in present]
    if missing:
        return (
            "Candidate omitted or changed an original declaration header: "
            + missing[0]
        )
    return None


def has_concrete_proof_candidate(code: str) -> bool:
    """Return true when solve input should be checked before calling a model."""
    without_blocks = re.sub(r"/-.*?-/", "", code, flags=re.DOTALL)
    without_comments = re.sub(r"--.*$", "", without_blocks, flags=re.MULTILINE)
    has_declaration = bool(_declaration_headers(without_comments))
    has_placeholder = bool(re.search(r"\b(?:sorry|admit)\b", without_comments))
    return has_declaration and not has_placeholder


def _declaration_headers(code: str) -> list[str]:
    code = _strip_lean_comments(code)
    pattern = re.compile(
        r"(?ms)^[ \t]*(theorem|lemma|example)\b(.*?)(?=:=)",
    )
    return [" ".join(match.group(0).split()) for match in pattern.finditer(code)]


def _immutable_task_prefix(code: str) -> str | None:
    code = _strip_lean_comments(code)
    match = re.search(
        r"(?ms)\A(.*?^[ \t]*(?:theorem|lemma|example)\b.*?)(?=:=)",
        code,
    )
    if match is None:
        return None
    return re.sub(r"\s+", "", match.group(1))


def _strip_lean_comments(code: str) -> str:
    without_blocks = re.sub(r"/-.*?-/", "", code, flags=re.DOTALL)
    return re.sub(r"--.*$", "", without_blocks, flags=re.MULTILINE)


def is_degenerate_candidate(candidate: str) -> bool:
    if len(candidate) > 16_000:
        return True
    normalized_lines = [line.strip() for line in candidate.splitlines() if line.strip()]
    counts: dict[str, int] = {}
    for line in normalized_lines:
        counts[line] = counts.get(line, 0) + 1
        if counts[line] >= 5:
            return True
    return candidate.count("induction ") >= 4


def find_rejected_duplicate(
    records: list[IterationRecord], candidate: str
) -> IterationRecord | None:
    normalized = strategy_fingerprint(candidate)
    for record in reversed(records):
        if (
            not record.verification.valid
            and strategy_fingerprint(record.candidate) == normalized
        ):
            return record
    return None


def candidate_repeats_rejected_attempt(records: list[IterationRecord]) -> bool:
    if not records:
        return False
    return find_rejected_duplicate(records[:-1], records[-1].candidate) is not None


def normalize_candidate(candidate: str) -> str:
    return "\n".join(line.rstrip() for line in candidate.strip().splitlines())


def strategy_fingerprint(candidate: str) -> str:
    """Normalize harmless proof-wrapper differences for conservative deduping."""
    assignment = _target_assignment_index(candidate)
    proof = candidate[assignment + 2 :] if assignment is not None else candidate
    proof = _strip_lean_comments(proof).strip()
    if re.match(r"^by\b", proof):
        proof = re.sub(r"^by\b", "", proof, count=1).strip()
    if re.match(r"^exact\b", proof):
        proof = re.sub(r"^exact\b", "", proof, count=1).strip()
    while proof.startswith("(") and proof.endswith(")"):
        proof = proof[1:-1].strip()
    return re.sub(r"\s+", "", proof)


def replace_target_proof(theorem: str, proof: str) -> str:
    """Replace the final theorem/lemma/example body while preserving its statement."""
    assignment = _target_assignment_index(theorem)
    if assignment is None:
        raise ValueError("Lean task has no theorem, lemma, or example proof assignment")
    return theorem[: assignment + 2].rstrip() + " " + proof.strip() + "\n"


def _target_assignment_index(code: str) -> int | None:
    return target_assignment(code)


def _memory_sample(
    backend: ModelBackend,
    retriever: SemanticRetriever | None = None,
) -> dict[str, float | int | None]:
    own_max_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # ru_maxrss is bytes on macOS and KiB on Linux.
    own_mb = own_max_rss / (1024 * 1024) if sys_platform_is_macos() else own_max_rss / 1024
    pid = getattr(backend, "process_id", None)
    retrieval_pid = getattr(retriever, "process_id", None)
    model_rss = _process_rss_mb(pid)
    retrieval_rss = _process_rss_mb(retrieval_pid)
    return {
        "agent_pid": os.getpid(),
        "agent_max_rss_mb": round(own_mb, 2),
        "model_pid": pid,
        "model_rss_mb": None if model_rss is None else round(model_rss, 2),
        "retrieval_pid": retrieval_pid,
        "retrieval_rss_mb": (
            None if retrieval_rss is None else round(retrieval_rss, 2)
        ),
    }


def _process_rss_mb(pid: object) -> float | None:
    if not isinstance(pid, int):
        return None
    try:
        output = subprocess.check_output(
            ["ps", "-o", "rss=", "-p", str(pid)], text=True, timeout=2
        ).strip()
        return int(output) / 1024 if output else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def sys_platform_is_macos() -> bool:
    return os.uname().sysname == "Darwin"
