# AGENTS.md

## Cursor Cloud specific instructions

**Study** is a Python CLI tool for automated task orchestration on top of Cursor Agent. It is a pure Python package with a single runtime dependency (`rich`).

### Development environment

- Python 3.9+ required; venv at `.venv/`
- Install: `source .venv/bin/activate && pip install -e .`
- Entry points: `study` and `secretary` (both identical)

### Running the CLI

- Set `SECRETARY_WORKSPACE=/workspace` (or run `study base .`) before using `study` commands; otherwise it defaults to CWD.
- `study --help` shows all available commands.
- `study monitor --text` shows system status as a text snapshot (non-blocking, no TUI).
- `study monitor` launches a TUI dashboard (requires TTY).

### Linting

No linter config is committed; use `ruff check .` and `pyright secretary/` for static analysis. Pre-existing warnings exist in the codebase.

### Testing

No automated test suite exists in the repository. Manual verification: `study --help`, `study skills`, `study workers`, `study monitor --text`.

### Building

`python -m build` produces sdist and wheel under `dist/`.

### Key caveat

Study's task execution depends on the Cursor `agent` binary being available in `PATH`. The CLI itself (help, monitor, hire, skills, etc.) works without it, but `study start` / `study task` cannot process tasks without the `agent` command.
