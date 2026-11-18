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

The first time, each agent's tool asks a few questions in its window before the session starts (`agora-start` lists them when it finishes): answer them before writing in the room, or the room cannot wake that agent. Claude Code asks to trust the folder (choose «Yes, I trust this folder») and whether to use the `agora` MCP server (choose «Use this MCP server»); in both, the option selected by default is the other one. In tmux, `Ctrl-b` and the window number (`Ctrl-b w` for a list) switch windows.

Open the room with the link `agora-start` prints (<http://127.0.0.1:8765/?token=…>; the token lets the page answer the agents' permission requests) and write `@<agent id>` to address an agent.

To run the pieces separately: `uv run agora-prepare`, `uv run agora` and `uv run agora-launch <id>`.

## Documentation

[docs/specification.md](docs/specification.md) covers the behaviour, `agora.toml`, the commands, how each tool is woken, and the known limits.

[docs/decisions.md](docs/decisions.md) lists the technical decisions and when to revisit them.

## Development

```sh
uv run pytest        # tests
uv run lint-imports  # import layers
uv run pyright       # types
uv run ruff check    # lint
```

`uv run pre-commit install` runs these checks (tests only before pushing), Ruff's formatter, a secret scan (gitleaks), the workflow linters (actionlint, zizmor), a local link check (lychee) and some file hygiene hooks on each commit. CI runs them all again, and also CodeQL, OpenSSF Scorecard, a pull request title check and a weekly external link check. Releases and the changelog are made by release-please ([CONTRIBUTING.md](CONTRIBUTING.md)).

Security reports: [SECURITY.md](SECURITY.md). Contributing: [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
