# Agent instructions

Project: local room where the user coordinates agents. `uv` project (package `agora` in `src/`) with Python 3.14 (tools pinned in `mise.toml`: `mise install`); tests with `uv run pytest`; lint, format, import layers, types and the rest of the hooks with `uv run pre-commit run --all-files`. Technical decisions: [docs/decisions.md](docs/decisions.md); a change that takes or changes one updates it.

## Agent room

To coordinate with the user and other agents, use the room (MCP `agora`; server `uv run agora`, web at <http://127.0.0.1:8765>). Its usage rules come with the MCP.
