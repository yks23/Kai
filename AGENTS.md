# AGENTS.md

## Cursor Cloud specific instructions

**Kai** is a Python CLI tool for automated task orchestration on top of Cursor Agent. It is a pure Python package with a single runtime dependency (`rich`).

### Development environment

- Python 3.9+ required; venv at `.venv/`
- Install: `source .venv/bin/activate && pip install -e .`
- Entry points: `kai` and `secretary` (both identical)

### Running the CLI

- Set `SECRETARY_WORKSPACE=/workspace` (or run `kai base .`) before using `kai` commands; otherwise it defaults to CWD.
- `kai --help` shows all available commands.
- `kai monitor --text` shows system status as a text snapshot (non-blocking, no TUI).
- `kai monitor` launches a TUI dashboard (requires TTY).

### Linting

No linter config is committed; use `ruff check .` and `pyright secretary/` for static analysis. Pre-existing warnings exist in the codebase.

### Testing

No automated test suite exists in the repository. Manual verification: `kai --help`, `kai skills`, `kai workers`, `kai monitor --text`.

### Building

`python -m build` produces sdist and wheel under `dist/`.

### Key caveat

Kai's task execution depends on the Cursor `agent` binary being available in `PATH`. The CLI itself (help, monitor, hire, skills, etc.) works without it, but `kai start` / `kai task` cannot process tasks without the `agent` command.
