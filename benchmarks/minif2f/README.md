# MiniF2F (V7)

`data/manifest.json` pins the Lean 4 dataset revision and per-statement hashes.
`data/valid` and `data/test` contain 244 unproved statements each.
`data/LICENSE` retains the upstream license. Do not add reference solutions to
agent prompts or replace failed statements with easier ones.

Use `local-lean-agent minif2f-prepare` to fetch/verify the pinned dataset, and
`minif2f-validate` / `minif2f-run` to validate/evaluate it. See
[the V7 guide](../../docs/v7-minif2f.md) for commands, provenance, budget semantics,
resumption, scoring and experimental limitations.
