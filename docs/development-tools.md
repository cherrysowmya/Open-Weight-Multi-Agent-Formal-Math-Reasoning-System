# Development tool availability

The current public checkout is focused on running the theorem-proving system.
It intentionally excludes `tests/` and the standalone `scripts/test-*.py` and
`scripts/audit-*.py` research tools. These are not runtime dependencies. The
agent's packaged CLI evaluation commands, benchmark inputs, and research reports
remain included.

The published `scripts/` directory contains only:

| Script | Purpose |
| --- | --- |
| `setup-lean-lsp-mcp.sh` | Install the local Lean-LSP-MCP environment |
| `setup-lean-explore.sh` | Install LeanExplore and prepare its local retrieval data |
| `start-kimina.sh` | Start the already-installed local Kimina Lean server |

Their paths are unchanged, so the installation/startup commands still work.
For a basic environment diagnostic, use:

```bash
local-lean-agent --config config/local.toml doctor
```

## Historical research tools

Test and audit commands shown in the research guides require the development
tools, not just a fresh runtime checkout. The last complete development snapshot
before this packaging cleanup is commit
`0f01333224a127a20f680f12288d42e6e5a47e4b`.

To inspect or reproduce that version, clone the repository into a **separate
directory** and check out that commit. Follow its setup instructions. This keeps
the tests and implementation versions matched; old test results do not establish
correctness of future code changes.

This cleanup removes files from the current Git tree, not from Git history.
On the maintainer's existing machine, the development files are retained at their
original paths and ignored by Git, so local test and audit workflows still work.
Ignored local changes are not backed up by pushing this repository.
