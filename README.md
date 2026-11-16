# Agora

A local room where you coordinate AI coding agents (Claude Code, Codex and Antigravity CLI). Agents take part through an MCP server; the room wakes only the agents you mention and stops chains between agents after a few hops, so they wait for you.

Runs on macOS and Linux.

## Requirements

- [mise](https://mise.jdx.dev/) (installs the pinned `uv`; `uv` installs Python 3.14).
- [tmux](https://github.com/tmux/tmux), to open the room and the agents with `agora-start`.
- The CLI of each tool you use (`claude`, `codex`, `agy`), signed in.

## Quick start

```sh
git clone <this repository> agora && cd agora
mise install
uv sync
```

Describe the room in `agora.toml` (the one in this repository is a minimal example: you and one Claude Code agent), then:

```sh
uv run agora-start      # prepares the agents, opens the room and each agent in tmux
tmux attach -t agora    # see the windows
```

Open the room at <http://127.0.0.1:8765> and write `@<agent id>` to address an agent.

To run the pieces separately: `uv run agora-prepare`, `uv run agora` and `uv run agora-launch <id>`.

## Documentation

[docs/specification.md](docs/specification.md) covers the behaviour, `agora.toml`, the commands, how each tool is woken, and the known limits.

## Development

```sh
uv run pytest        # tests
uv run lint-imports  # import layers
uv run pyright       # types
```

`uv run pre-commit install` runs these checks (tests only before pushing), a secret scan (gitleaks), the workflow linters (actionlint, zizmor) and some file hygiene hooks on each commit.

## License

[MIT](LICENSE)
