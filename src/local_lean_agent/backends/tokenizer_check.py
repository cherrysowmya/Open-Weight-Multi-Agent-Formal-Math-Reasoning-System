"""Fail closed on lossy Lean tokenization before starting an MLX process.

Run in a short-lived subprocess: no model weights or Metal allocations needed.
Use the same AutoTokenizer entry point as MLX-LM, not a substitute tokenizer.
"""
from __future__ import annotations

import argparse
from importlib.metadata import version
import json

LEAN_PROBES = (
    "theorem probe (P Q R : Prop) (h : P → Q → R) : P ∧ Q → R := by\n"
    "  intro hpq\n  exact h hpq.1 hpq.2",
    "∀ (x : ℝ), 0 ≤ x^2 ∧ (x ≠ 0 → ∃ y, y ≥ x)\n"
    "¬P ∨ Q ↔ (P → Q)\n  exact ⟨h₁, h₂⟩ -- λ α ℕ ℤ\n",
)


def validate_tokenizer(tokenizer) -> dict:
    for index, probe in enumerate(LEAN_PROBES):
        decoded = tokenizer.decode(tokenizer.encode(probe, add_special_tokens=False),
                                   skip_special_tokens=False,
                                   clean_up_tokenization_spaces=False)
        if decoded != probe:
            raise ValueError(f"Lossy Lean tokenizer: round-trip probe {index + 1} changed text. "
                             "Reinstall the project's pinned MLX dependencies; do not run proofs.")
    if not tokenizer.chat_template:
        raise ValueError("Tokenizer has no chat template; refusing an implicit fallback")
    messages = [{"role": "system", "content": "Prove the supplied Lean theorem."},
                {"role": "user", "content": LEAN_PROBES[0]}]
    rendered = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    tokens = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True)
    decoded = tokenizer.decode(tokens, skip_special_tokens=False, clean_up_tokenization_spaces=False)
    if rendered != decoded or any(m["content"] not in decoded for m in messages):
        raise ValueError("Chat template/tokenization changed the proof request; refusing inference")
    return {"valid": True, "tokenizer_class": type(tokenizer).__name__,
            "lean_roundtrip_probes": len(LEAN_PROBES), "chat_template_roundtrip": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model")
    args = parser.parse_args()
    try:
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=False)
        result = validate_tokenizer(tokenizer)
        result.update(model=args.model, packages={p: version(p) for p in
                      ("transformers", "tokenizers", "huggingface-hub")})
        print(json.dumps(result))
        return 0
    except Exception as exc:
        print(json.dumps({"valid": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
