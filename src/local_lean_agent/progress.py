"""Concise event-driven terminal presentation; never exposes raw model reasoning."""
import sys
import time


class TerminalProgress:
    def __init__(self, stream=None):
        self.stream = stream if stream is not None else sys.stderr
        self.started = time.monotonic()

    def __call__(self, record):
        event, data = record["event"], record["payload"]
        message = None
        if event == "attempt_started":
            message = "Orchestrator | Starting theorem attempt; Lean is the final authority."
        elif event == "model_loading":
            message = f"MLX | Loading {data['model']} (cold starts can take a while)."
        elif event == "model_loaded":
            message = "MLX | Server ready; first inference may still warm up the model."
        elif event == "model_unloading":
            message = "MLX | Unloading the model and releasing memory."
        elif event == "model_unloaded":
            message = "MLX | Model unloaded."
        elif event == "semantic_retrieval_started":
            message = "LeanExplore | Searching local declarations" + (" for the informal strategy." if data.get("strategy") else ".")
        elif event == "semantic_retrieval_completed":
            message = (f"LeanExplore | Retrieved {len(data.get('hits', []))} declarations."
                       if data.get("available") else "LeanExplore | Retrieval unavailable; see trace.")
        elif event == "formal_output_budget_selected":
            role = "Fresh formal context" if data["role"] == "fresh_subproblem" else "Main agent"
            message = (f"{role} (Qwen) | Round {data['iteration']}: generating/repairing a Lean proof; "
                       f"output budget {data['requested_output_tokens']} tokens.")
        elif event == "informal_role_started":
            role = "Informal Generator" if data["role"] == "generator" else "Informal Verifier"
            action = "Developing a mathematical plan" if data["role"] == "generator" else "Independently critiquing the proposed plan"
            message = f"{role} (Qwen) | {action}; fresh context, refinement {data['refinement_round']}."
        elif event == "informal_role_completed":
            role = "Informal Generator" if data["role"] == "generator" else "Informal Verifier"
            status = data.get("verdict") or data.get("finish_reason") or "returned"
            message = f"{role} | Response: {status}. Informal output is not a Lean proof."
        elif event == "informal_reasoning_completed":
            message = f"Orchestrator | Informal loop ended: {data['stop_reason']}; returning to formal proof generation."
        elif event == "v4_request_started" and data["role"] == "discussion_partner":
            message = "Discussion Partner (Qwen) | Considering another strategy in a fresh context."
        elif event == "v4_request_completed" and data["role"] == "discussion_partner":
            message = "Discussion Partner | Returned advisory strategy; not verified mathematics."
        elif event == "lean_check_started":
            message = f"Kimina / Lean | Checking {data['source']}."
        elif event == "lean_check_completed":
            message = "Kimina / Lean | " + ("ACCEPTED." if data["valid"] else f"REJECTED ({data['failure_category']}).")
        elif event == "lean_lsp_started":
            message = "Lean-LSP-MCP | Inspecting compiler diagnostics and current goals."
        elif event == "lean_lsp_inspected":
            message = "Lean-LSP-MCP | Feedback received." if data.get("available") else "Lean-LSP-MCP | Feedback unavailable; see trace."
        elif event in {"portfolio_check_started", "salvage_check_started"}:
            role = "Tactic portfolio" if event == "portfolio_check_started" else "Prefix repair"
            message = f"{role} (not an LLM) | Lean is testing: {data['tactic']}"
        elif event in {"fallback_candidate_checked", "rewrite_prefix_salvage_checked"}:
            message = f"Lean automation | {data['tactic']}: " + ("ACCEPTED." if data["verification"]["valid"] else "rejected.")
        elif event == "proof_body_response_normalized" and data["action"] != "body":
            message = f"Proof assembler | {data['action']}; original task retained."
        elif event == "iteration_completed":
            message = (f"Orchestrator | Round {data['iteration']} "
                       + ("verified." if data["verification"]["valid"] else "not verified; evaluating recovery options."))
        elif event == "attempt_error":
            message = "Orchestrator | Runtime/budget error; details saved in trace."
        elif event == "attempt_completed":
            message = f"Orchestrator | Finished: {'SUCCESS' if data['success'] else 'FAILURE'} ({data['stop_reason']})."
        if message:
            # Stage labels are controlled strings; remove terminal control bytes
            # from the few configuration/status values interpolated above.
            message = "".join(c if c.isprintable() else " " for c in message)
            print(f"[{time.monotonic() - self.started:7.1f}s] {message}", file=self.stream, flush=True)


def demo_summary(result, *, trace, result_file, proof_file):
    m = result.metrics
    return {
        "success": result.success, "failure_category": result.failure_category.value,
        "stop_reason": result.stop_reason, "rounds": result.rounds,
        "wall_clock_seconds": round(m.wall_clock_seconds, 2),
        "model_calls": {"formal": m.model_calls, "informal_generator": m.informal_generator_calls,
                        "informal_verifier": m.informal_verifier_calls, "discussion": m.discussion_partner_calls,
                        "fresh_formal_contexts": m.fresh_subproblem_calls},
        "tokens": {"prompt": m.prompt_tokens + m.informal_prompt_tokens + m.discussion_prompt_tokens,
                   "generated": m.completion_tokens + m.informal_completion_tokens + m.discussion_completion_tokens},
        "kimina_checks": m.kimina_checks, "lean_lsp_calls": m.lean_lsp_calls,
        "retrieval_calls": m.retrieval_calls, "portfolio_checks": m.portfolio_checks,
        "portfolio_successes": m.portfolio_successes,
        "model_load_seconds": round(m.model_load_seconds, 2),
        "model_unload_seconds": round(m.model_unload_seconds, 2),
        "trace_file": str(trace.resolve()), "full_result_file": str(result_file.resolve()),
        "verified_proof_file": str(proof_file.resolve()) if proof_file else None,
        "error_message": result.error_message,
    }
