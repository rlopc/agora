# Decisions

The project's technical decisions, with their status, when to revisit them and where they are explained. A pull request that takes or changes a decision updates this table.

- **Final**: decided; changing it needs a new decision.
- **Provisional**: in use, with a known condition to revisit.

| Decision | Status | Revisit when | Link |
| --- | --- | --- | --- |
| macOS and Linux only; `agora-start` opens the room and the agents in tmux | Final | — | [README](../README.md) |
| Each agent uses its own tool's CLI and sign-in (API key or subscription), with no proxy in between | Final | — | [specification](specification.md#authentication) |
| Codex: one `CODEX_HOME` per agent, one `codex login` in each, credentials in the system store (`cli_auth_credentials_store = "auto"`) | Provisional | Once tested with a real `codex` | [specification](specification.md#authentication) |
| Antigravity CLI has no waker: pending messages reach it only through its hooks. Typing into its terminal was tried and dropped, because Enter approves an open dialog | Provisional | `agy` gains a way to message a live session | [specification](specification.md#wakers) |
| Antigravity's permission for the room's MCP tools goes in `--settings` for the session, not in its global `settings.json` | Provisional | `agy` documents a project-level permission | [specification](specification.md#permissions-security-and-known-limits) |
| One package, `agora`, in a `src/` layout; dependencies point one way (`cli`/`server` → `tools` → `agents` → `core`), checked by import-linter | Final | — | [pyproject.toml](../pyproject.toml) |
| Each agent tool implements the `Tool` protocol and joins the `TOOLS` registry; tools do not import each other | Final | — | [specification](specification.md#architecture) |
| One `agora.toml` per room configuration, chosen with `--config` | Final | — | [specification](specification.md#configuration-agoratoml) |
| SQLite is the only store; tests use an in-memory database | Final | — | [specification](specification.md#architecture) |
| The API has no token: it checks `Host` (DNS rebinding) and `Origin` (other web pages), so any local process can use it | Provisional | The room listens beyond `localhost`, or holds data a local process must not read | [specification](specification.md#permissions-security-and-known-limits) |
| Versions at least 7 days old: `exclude-newer` in uv, `minimum_release_age` in mise, `cooldown` in Dependabot | Final | — | [pyproject.toml](../pyproject.toml), [mise.toml](../mise.toml) |
| Every pre-commit hook is local and runs a locked tool; pytest runs only before pushing; CI runs every hook again | Final | — | [.pre-commit-config.yaml](../.pre-commit-config.yaml) |
| Ruff with a short rule set (`F`, `E`, `W`, `I`, `UP`, `B`, `S`, `SIM`, `RUF`) and the line length the code had (120) | Provisional | Widen the rule set when a rule earns its place | [pyproject.toml](../pyproject.toml) |
| pyright checks `src/` only, not the tests | Provisional | The tests stop relying on hand-narrowed types and test doubles | [pyproject.toml](../pyproject.toml) |
| No releases yet: Agora is used from a clone of `main` | Provisional | Someone needs a versioned install | [README](../README.md) |
| MIT license | Final | — | [LICENSE](../LICENSE) |
