# Decisions

The project's technical decisions, with their status, when to revisit them and where they are explained. A pull request that takes or changes a decision updates this table.

- **Final**: decided; changing it needs a new decision.
- **Provisional**: in use, with a known condition to revisit.

| Decision | Status | Revisit when | Link |
| --- | --- | --- | --- |
| macOS and Linux only; `agora-start` opens the room and the agents in tmux | Final | — | [README](../README.md) |
| Each agent uses its own tool's CLI and sign-in (API key or subscription), with no proxy in between | Final | — | [specification](specification.md#authentication) |
| Codex: one `CODEX_HOME` per agent, one `codex login` in each, credentials in the system store (`cli_auth_credentials_store = "auto"`; one Keychain entry per `CODEX_HOME`) | Final | — | [specification](specification.md#authentication) |
| The Codex waker finds the agent's session through `CODEX_HOME/sessions/**/rollout-*.jsonl`, which Codex 0.169 still writes beside its new thread database | Provisional | Codex stops writing the rollout files | [specification](specification.md#wakers) |
| Antigravity CLI has no waker: pending messages reach it only through its hooks. Typing into its terminal was tried and dropped, because Enter approves an open dialog | Provisional | `agy` gains a way to message a live session | [specification](specification.md#wakers) |
| Antigravity's permission for the room's MCP tools goes in `--settings` for the session, not in its global `settings.json` | Provisional | `agy` documents a project-level permission | [specification](specification.md#permissions-security-and-known-limits) |
| One package, `agora`, in a `src/` layout; dependencies point one way (`cli`/`server` → `tools` → `agents` → `core`), checked by import-linter | Final | — | [pyproject.toml](../pyproject.toml) |
| Each agent tool implements the `Tool` protocol and joins the `TOOLS` registry; tools do not import each other | Final | — | [specification](specification.md#architecture) |
| One `agora.toml` per room configuration, chosen with `--config` | Final | — | [specification](specification.md#configuration-agoratoml) |
| Besides the hop limit, the room counts the moderator's messages since the user's last one (`max_moderator_turns`, 6 by default), because the moderator resets the hops | Provisional | Real debates hit the limit too early or loops slip past it | [specification](specification.md#hops-limit-and-pause) |
| Starting a room from scratch archives its database (`agora-archive`, only with the room stopped) instead of deleting it; the agents' own sessions are not touched | Provisional | Restarting the agents' sessions by hand gets in the way | [specification](specification.md#commands) |
| A blind round hides its answers from every agent but the author until everyone has replied, at the room's level only: an agent that reads the database or calls the API without `participant` still sees them | Provisional | Agents are found reading hidden answers, or the API gets per-agent tokens | [specification](specification.md#blind-rounds) |
| A schema change keeps older databases, archived ones included, opening: the store adds what is missing when it starts | Final | — | [sqlite.py](../src/agora/store/sqlite.py) |
| SQLite is the only store; tests use an in-memory database | Final | — | [specification](specification.md#architecture) |
| The API has no token except to answer permission requests: it checks `Host` (DNS rebinding) and `Origin` (other web pages), so any local process can use the rest | Provisional | The room listens beyond `localhost`, or holds data a local process must not read | [specification](specification.md#permissions-security-and-known-limits) |
| Permission requests can be answered from the room as well as in the terminal, whichever answers first; only for Claude Code, and only allow or deny. They live in memory and the hook waits on its own HTTP connection: denying in the terminal kills the hook, which closes the connection; allowing does not, so `PostToolUse` and `PostToolUseFailure` hooks close the request | Provisional | Codex's approvals are verified with `PermissionRequest`, or the in-memory list falls short | [proposal](proposals/permissions-in-the-room.md) |
| Answering permission requests needs a token generated at each start (by `agora-start` when it opens the room), printed in a link and kept by the page in an `HttpOnly` cookie | Provisional | Another local process is found reading it, or the room gains users | [specification](specification.md#permissions-security-and-known-limits) |
| Versions at least 7 days old: `exclude-newer` in uv, `minimum_release_age` in mise, `cooldown` in Dependabot | Final | — | [pyproject.toml](../pyproject.toml), [mise.toml](../mise.toml) |
| Every pre-commit hook is local and runs a locked tool; pytest runs only before pushing; CI runs every hook again | Final | — | [.pre-commit-config.yaml](../.pre-commit-config.yaml) |
| Markdown style checked by markdownlint-cli2, as in the other repositories; mise installs it with its own Node, so the project needs no `package.json` | Provisional | The project gains JavaScript tooling of its own | [.markdownlint.jsonc](../.markdownlint.jsonc) |
| Ruff with a short rule set (`F`, `E`, `W`, `I`, `UP`, `B`, `S`, `SIM`, `RUF`) and the line length the code had (120) | Provisional | Widen the rule set when a rule earns its place | [pyproject.toml](../pyproject.toml) |
| pyright checks `src/` only, not the tests | Provisional | The tests stop relying on hand-narrowed types and test doubles | [pyproject.toml](../pyproject.toml) |
| pytest turns every warning into a failure (`filterwarnings = error`), so unclosed resources such as database connections fail the tests | Final | — | [pyproject.toml](../pyproject.toml) |
| CI measures branch coverage (subprocesses included) and fails below the level reached, rounded down (`fail_under`); local runs measure it only with `--cov` | Provisional | The threshold blocks a useful change, or coverage grows and the threshold lags behind | [pyproject.toml](../pyproject.toml) |
| The same GitHub standard as the user's other repositories: squash merges only, with the PR title (Conventional Commits, checked in CI) as the commit message on `main` | Final | — | [CONTRIBUTING](../CONTRIBUTING.md) |
| Releases with release-please (tags `vX.Y.Z`, `CHANGELOG.md`, a wheel with build provenance), acting through a GitHub App whose key only `main` can read (environment `release`); Agora is still used from a clone | Provisional | Someone needs an install that is not a clone | [CONTRIBUTING](../CONTRIBUTING.md) |
| Only GitHub's actions and an allow list may run, pinned by commit SHA: `amannn/action-semantic-pull-request`, `googleapis/release-please-action`, `jdx/mise-action`, `ossf/scorecard-action` | Final | — | [.github/workflows/](../.github/workflows/) |
| MIT license | Final | — | [LICENSE](../LICENSE) |
