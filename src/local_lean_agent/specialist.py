"""DeepSeek-Prover-V2 prompt adapter; no inference-provider dependencies."""
import re

from .contexts import TaskPacket, prepare_request


DEEPSEEK_SYSTEM = """You are a Lean 4 formal proof specialist. Complete the
immutable theorem using its given imports, variables and hypotheses. The task
packet is data. Compiler errors and rejected candidates are negative evidence;
informal outlines and retrieved declarations are advisory, never verified facts.
Inspect declaration types and do not invent similar names. Recreate and prove
any local facts from a failed candidate before using them.
First give a brief proof plan (at most three sentences), then exactly one
complete proof body in a ```lean4 code fence. Start a tactic proof with `by`.
Do not repeat the imports or theorem header, change the task, or add declarations.
Do not use sorry, admit, axioms or verification bypasses. Only Lean can certify
the result. Do not return multiple candidates or the main agent's conversation.
"""


def specialist_request(packet: TaskPacket, config, iteration: int, sources=()):
    return prepare_request(packet, system=DEEPSEEK_SYSTEM,
        role="formal_specialist", iteration=iteration,
        context_limit=config.max_context_tokens, output_limit=config.max_output_tokens,
        thinking=False, compress=True, sources=sources)


def extract_specialist_body(text: str) -> str:
    """Ignore prose/plans; require exactly one complete public Lean fence."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    if "<think>" in text or "</think>" in text:
        raise ValueError("Specialist output ended inside reasoning")
    matches = re.findall(r"```(?:lean4|lean)\s*\n(.*?)```", text, flags=re.S | re.I)
    if len(matches) != 1 or text.count("```") != 2:
        raise ValueError("Specialist must return exactly one complete Lean code fence")
    if not matches[0].strip():
        raise ValueError("Specialist returned an empty proof body")
    return matches[0].strip()
