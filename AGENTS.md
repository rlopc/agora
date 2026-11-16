# Agent instructions

Project: local room where the user coordinates agents. `uv` project (package `agora` in `src/`) with Python 3.14 (tools pinned in `mise.toml`: `mise install`); tests with `uv run pytest`, import layers with `uv run lint-imports` and types with `uv run pyright`.

## Agent room

To coordinate with the user and other agents, use the room (MCP `agora`; server `uv run agora`, web at <http://127.0.0.1:8765>). Its usage rules come with the MCP.
