"""Conservative, deterministic proof-slot assembly (not a general Lean parser)."""
from dataclasses import dataclass
import re


class ProofSlotError(ValueError):
    pass


def masked_source(source: str) -> str:
    """Mask nested comments and strings, preserving byte-independent offsets/newlines."""
    result = list(source)
    i = 0
    depth = 0
    string = False
    line = False
    while i < len(source):
        pair = source[i:i + 2]
        if line:
            if source[i] == "\n":
                line = False
        elif depth:
            if pair == "/-":
                depth += 1
            elif pair == "-/":
                depth -= 1
                result[i:i + 2] = "  "
                i += 2
                continue
        elif string:
            if source[i] == "\\":
                result[i:i + 2] = " " * len(source[i:i + 2])
                i += 2
                continue
            if source[i] == '"':
                string = False
                result[i] = " "
                i += 1
                continue
        elif pair == "--":
            line = True
        elif pair == "/-":
            depth = 1
        elif source[i] == '"':
            string = True
        else:
            i += 1
            continue
        if source[i] != "\n":
            result[i] = " "
        if pair == "/-" and depth and not string and not line:
            result[i:i + 2] = "  "
            i += 2
        else:
            i += 1
    return "".join(result)


_COMMAND = re.compile(
    r"(?m)^\s*(?:@\[|#|(?:import|theorem|lemma|example|def|abbrev|axiom|constant|"
    r"namespace|section|end|open|variable|variables|set_option|attribute|instance|"
    r"opaque|inductive|structure|class|syntax|macro|elab|initialize|unsafe)\b)"
)


def target_assignment(source: str) -> int | None:
    """Find the final supported declaration's := outside comments/binders."""
    masked = masked_source(source)
    declarations = list(re.finditer(r"(?m)^[ \t]*(?:theorem|lemma|example)\b", masked))
    if not declarations:
        return None
    nesting = 0
    for i in range(declarations[-1].end(), len(masked) - 1):
        char = masked[i]
        if char in "([{":
            nesting += 1
        elif char in ")]}":
            nesting -= 1
        elif masked[i:i + 2] == ":=" and nesting == 0:
            return i
    return None


@dataclass(frozen=True)
class ProofSlot:
    prefix: str
    suffix: str = ""

    @classmethod
    def from_source(cls, source: str) -> "ProofSlot":
        masked = masked_source(source)
        assignment = target_assignment(source)
        if assignment is None:
            raise ProofSlotError("Could not locate an unambiguous target proof assignment")
        # Namespace/section closures are immutable too. Other trailing commands
        # are deliberately unsupported rather than silently deleted.
        end = re.search(r"(?m)^[ \t]*end\b", masked[assignment + 2:])
        boundary = assignment + 2 + end.start() if end else len(source)
        suffix = source[boundary:]
        suffix_code = masked[boundary:]
        if suffix and re.sub(r"(?m)^[ \t]*end(?:[ \t]+[\w'.]+)?[ \t]*$", "", suffix_code).strip():
            raise ProofSlotError("Unsupported commands after the target proof")
        if _COMMAND.search(masked[assignment + 2:boundary]):
            raise ProofSlotError("Unsupported command in or after target proof")
        return cls(source[:assignment + 2], suffix)

    def assemble(self, body: str) -> str:
        body = body.strip()
        if not body:
            raise ValueError("Empty proof body")
        if _COMMAND.search(masked_source(body)):
            raise ValueError("Expected only a proof body, not file-level Lean commands")
        # A body is a complete expression after :=, including `by` for tactics.
        # Indent every line so it cannot escape the declaration's layout scope.
        return self.prefix + "\n" + "\n".join("  " + line for line in body.splitlines()) + "\n" + self.suffix

    def assemble_response(self, response: str) -> tuple[str, str]:
        """Recover known wrappers, never adopt model-provided task material.

        Accept a matching full prefix or just the matching target declaration.
        Token comparison ignores layout only, preserving identifiers, strings,
        comments and operators. Arbitrary preambles/extra declarations fail closed.
        """
        body = response.strip()
        action = "body"
        if target_assignment(body) is not None:
            returned = ProofSlot.from_source(body)
            declarations = list(re.finditer(r"(?m)^[ \t]*(?:theorem|lemma|example)\b",
                                            masked_source(self.prefix)))
            target_prefix = self.prefix[declarations[-1].start():]
            def tokens(text: str) -> list[str]:
                return re.findall(r'"(?:\\.|[^"\\])*"|\w+|[^\s]', text)
            if tokens(returned.prefix) not in (tokens(self.prefix), tokens(target_prefix)):
                raise ValueError("Returned declaration or file prefix differs from the immutable task")
            if returned.suffix and tokens(returned.suffix) != tokens(self.suffix):
                raise ValueError("Returned file suffix differs from the immutable task")
            end = len(body) - len(returned.suffix) if returned.suffix else len(body)
            body = body[len(returned.prefix):end].strip()
            action = "matching_declaration_extracted"
        elif body.startswith(":="):
            body = body[2:].strip()
            action = "assignment_delimiter_removed"
        return self.assemble(body), action


def body_prompt(text: str) -> str:
    """Adapt formal-only prompts; never rewrite task/evidence inside XML packets."""
    pieces = re.split(r"(<[a-z_]+>.*?</[a-z_]+>)", text, flags=re.DOTALL)
    for i, piece in enumerate(pieces):
        if i % 2 and not piece.startswith("<output_budget_recovery>"):
            continue
        pieces[i] = re.sub(
            r"complete\s+(?:replacement\s+)?Lean\s+file|complete\s+file|complete unchanged theorem with a short proof",
            "complete proof body (including `by` for tactics)", piece, flags=re.IGNORECASE)
    return "".join(pieces)


BODY_CONTRACT = """
OUTPUT CONTRACT: Return ONLY the complete expression after the target's `:=`,
inside one lean code fence. For tactics begin with `by`; for a term return the
term itself. Do not return imports, the theorem header, namespace/end commands,
or extra declarations. Python keeps the original task immutable and assembles
the complete file. Repair only this proof body; Lean checks the assembled file.
Do NOT include the `:=` delimiter. Start the code with `by` or a proof term.
Example output shape for a reflexive equality (adapt the proof to YOUR goal):
```lean
by
  rfl
```
Never echo the input declaration. The example is a format illustration, not a
claim that rfl solves the current task.
"""
