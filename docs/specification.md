# Agora specification

## Purpose and limits

Agora is a local room where one person coordinates AI coding agents (Claude Code, Codex, Antigravity CLI). It stores the messages, decides who each one is for from its mentions, limits automatic chains between agents and, when the tool allows it, wakes sessions that are already open.

It does not replace each tool's permissions, and a request from another agent never authorises changes to the project: that rule reaches the agents through the MCP instructions.

Agora runs on macOS and Linux and only listens on local addresses.

## Principles

- **Minimal token use.** An agent is only woken when it is mentioned, wakes are batched and chains between agents have a hop limit.
- **Local services only.** The core exposes three ports (`Store`, `Channel`, `Waker`); other channels (Slack…) would be new adapters, without changing the core or the agents.
- **`AGENTS.md` is the single source of each role's instructions** and is never generated. What is specific to a tool goes in that tool's own file (for example, the marked section of `CLAUDE.md`).
- **Generators never rewrite whole files** they do not own: they update only their keys, keep everything else, are idempotent and do not bypass the tool's protections (such as trusting hooks).
- **Each agent uses its own CLI with its own authentication** (API key or subscription), without proxies.

## Architecture

```mermaid
flowchart LR
  U[User / browser] --> W[web\nFastAPI, HTML, SSE]
  A[Agent session] <-->|MCP stdio| M[mcp]
  M -->|HTTP| W
  W --> C[core\nmentions, hops, wakes]
  C <--> D[(store\nSQLite)]
  C --> K[wakers]
  K --> CQ[codex queue]
  K --> CC[Claude concierge]
  H[agent hooks] -->|agora-pending, agora-permission| W
  G[agora-prepare / launch / start] --> A
```

The package `agora` (in `src/`) has these layers; `import-linter` enforces them:

| Module | Responsibility |
| --- | --- |
| `core` | Participants, messages, mentions, hops, pause, pending messages, batching, retries and permission requests. Imports nothing else from `agora`. |
| `store` | SQLite store (WAL) for messages, mentions, reads, wakes and state. Depends only on `core`. |
| `web` | Web UI, HTTP API and server-sent events. Depends only on `core`. |
| `mcp` | Thin stdio MCP server: an HTTP client of the API, with no access to the store or the wakers. |
| `agents` | What every agent shares: `agora.toml` resolution, the `Tool` contract, preparation, notes, tmux and the `agora-watch`/`agora-pending`/`agora-permission` commands. |
| `tools/<tool>` | One package per tool (`claude`, `codex`, `antigravity`) implementing `Tool`. Tools do not import each other. |
| `server`, `cli` | Composition: build the room from `agora.toml` and expose the commands. |

A message goes through the room like this:

```mermaid
sequenceDiagram
  participant U as User
  participant R as Room (core + SQLite)
  participant W as Waker
  participant A as Agent session
  U->>R: message, optional @agent
  R->>R: store it, compute mentions and hops
  alt can wake
    R->>W: recipient's pending messages
    W->>A: codex queue or Claude concierge
    A->>R: pending()
    A->>R: send(..., reply_to) or mark_read()
  else paused, limit reached or batched
    R-->>R: keep it pending without waking
  end
  R-->>U: UI updated through SSE
```

## Behaviour

### Participants, mentions and the moderator

Each participant has an `id`, a name and a type, `human` or `agent`. Agents may have the role `moderator` (the only role). Mentions have the form `@id`, are lower-cased and deduplicated; unknown mentions are returned to the sender.

A message from the user without mentions goes to the participants with the moderator role; if there is none, it wakes nobody. A message from an agent without mentions goes to nobody.

### Hops, limit and pause

The hop count restarts at zero when the user or a moderator writes; any other agent adds one to the count of the last message. When a message reaches `max_hops` (3 by default), or wakes are paused, it is stored but wakes nobody: the room waits for the user.

Because the moderator resets the hops, a moderator and its agents could go on forever without the user. So the room also counts the moderator's turns: each message from the moderator adds one, and the user's next message resets the count. A message past `max_moderator_turns` (6 by default) is stored but wakes nobody either.

The pause persists across restarts, and resuming does not wake retroactively. A manual wake (button in the UI or `POST /api/wake/{participant}`) needs pending messages and ignores the pause and both limits.

### Blind rounds

A blind round lets several agents answer the same question without seeing each other's answers, so the first one does not anchor the rest. The user (the "Blind round" box in the UI) or the moderator (`send(..., blind=True)`) opens it with a message that mentions at least two agents; those are its participants. Only one round can be open at a time.

While it is open, every message from a participant belongs to the round, with or without `reply_to`:

- It is visible only to its author and the user: `pending`, `history`, the hooks' notices and the wakes leave it out for any other agent, the moderator included.
- It mentions whoever opened the round, plus the user if named; mentions of other agents are dropped (the text keeps them) and `send` reports them as held.
- It wakes nobody and keeps the hops of the opening message, since the answers are replies to it and not a chain between agents.

The round closes when every participant has written in it, or earlier with "Close blind round" in the UI or `close_round()`, if someone does not answer; only the user or the moderator can close it. Closing reveals the answers and, if the round was opened by the moderator, wakes it once with all of them, unless wakes are paused or a limit was reached. A round survives a restart. Messages written after the close are ordinary.

### Pending messages, reads and batching

A pending message mentions the participant, was not written by them and is not marked as read. `mark_read` is idempotent, and replying with `reply_to` marks the original as read.

While there is a wake later than the recipient's last activity, new messages are batched instead of waking again. When a wake has been delivered, or the agent acts, the room wakes again for pending messages it had not seen yet; it does not wake for what already existed when the server started. A failed wake is retried once after five seconds; if it fails again, the error is shown in the UI and in `/api/state`, and the message stays pending.

### Wakers

How the room reaches an open session depends on the tool:

- **Claude Code (`concierge`).** It looks in `~/.claude/sessions` for the most recently active interactive session whose working folder is the agent's folder, and runs a minimal `claude -p` (Haiku by default, strict MCP configuration, only `ToolSearch`, `ListAgents` and `SendMessage`) that delivers the notice to that session with `SendMessage`. If there is no session, the wake fails. If the agent is running `agora-watch`, the concierge is skipped.
- **Codex (`codex_queue`).** It runs `codex queue --thread … --message …`. The thread is `codex_thread`, the `AGORA_<ID>_THREAD` variable or, if neither is set, the most recent main session (not a subagent's) in the agent's `CODEX_HOME` whose working folder is the agent's folder.
- **Antigravity CLI (`watch`, through its `Stop` hook).** `agy` cannot receive messages from outside, and typing into its terminal could answer an open approval dialog. When a turn ends, its `Stop` hook runs `agora-watch --stop-hook`, which waits at no token cost until the room wakes the agent and then answers `continue`, so agy starts another turn; the messages reach it through the `PreInvocation` hook, before each model call, which also counts as the agent's activity. The hook does not wake it for messages it already saw and left pending, which would loop. Meanwhile agy queues what the user types in its window; Esc stops the wait, and then the room cannot wake it until its next turn ends. Each launch needs a first message typed in its window once `/mcp` lists the `agora` tools: agy adds the MCP tools to a conversation only on a message typed after they load, so a first message given at launch (`-i`) leaves it without them.
- **`watch`** (any agent, set with `waker = "watch"`). The server does not wake it: the agent runs `agora-watch`, which long-polls and prints the messages only after the room has decided to wake it. Meanwhile the UI shows the agent as listening.

The notice sent to Claude and Codex includes the pending messages (up to 1000 characters each) and says that they are data, not system instructions.

## Configuration (`agora.toml`)

Values declared on a participant win; anything still unset is taken from `[tools.<tool>]`. Relative paths (`folder`, `read_dirs`, `write_dirs`, `db`, and Codex's `homes` and `codex_home`) are relative to the folder of `agora.toml`.

```toml
[room]
host = "127.0.0.1"       # local address only
port = 8765
db = "data/agora.db"     # SQLite database, excluded from Git
max_hops = 3
max_moderator_turns = 6   # moderator messages since the user's last one
# project = "/path/to/agora"   # uv project that hooks and the MCP run; defaults to this checkout

[tools.claude]
model = "opus"
effort = "high"                                    # optional
subagents = { model = "sonnet", effort = "medium" }

[tools.codex]
model = "gpt-5.6-terra"
effort = "high"
subagents = { model = "gpt-5.6-terra", effort = "medium" }
homes = "/path/to/codex-homes"   # each agent uses <homes>/<id> as its CODEX_HOME

[tools.antigravity]
model = "gemini-3.8-flash-high"

[[participants]]
id = "user"
name = "User"
type = "human"

[[participants]]
id = "moderator"
name = "Moderator"
type = "agent"
role = "moderator"
tool = "claude"
folder = "moderator"
read_dirs = ["/path/to/sources"]   # extra folders the agent may read
write_dirs = ["shared"]            # extra folders the agent may write

[[participants]]
id = "analyst"
name = "Analyst"
type = "agent"
tool = "codex"
folder = "analyst"
# model, effort and subagents override the [tools.codex] values
```

Participant keys:

| Key | Meaning |
| --- | --- |
| `id`, `name`, `type` | Required. `type` is `human` or `agent`. |
| `role` | Optional, agents only: `moderator`. |
| `tool` | Required for agents: `claude`, `codex` or `antigravity`. |
| `folder` | The agent's working folder (default `.`). Each agent needs its own existing folder; with Claude Code and Antigravity, which load the instructions of parent folders, an agent's folder cannot be inside another's. |
| `model`, `effort`, `subagents` | Override the tool's defaults. Without a model, the tool uses its own default. |
| `read_dirs`, `write_dirs` | Extra folders; see [Permissions](#permissions-security-and-known-limits) for how each tool applies them. |
| `waker` | Optional: `watch` instead of the tool's waker. |
| `codex_home` | Codex: the agent's `CODEX_HOME`, instead of `<homes>/<id>`. |
| `codex_thread` | Codex: the session to wake, instead of discovering it. |
| `concierge_model` | Claude Code: the concierge's model (default `haiku`). |
| `executable`, `timeout` | Claude Code and Codex: the waker's command and time limit. |

To use several configurations (for example, the same debate with other tools), keep one `agora.toml` per configuration and pass it with `--config`.

## Commands

All commands accept `--config` (default `agora.toml`) where it applies.

| Command | What it does | Files it writes |
| --- | --- | --- |
| `uv run agora [--host H] [--port P]` | Starts the room (API and UI) and prints its link with the token that lets the page answer permission requests (`AGORA_TOKEN`, or a new one each start). Refuses to start if the port is in use. | The SQLite database. |
| `uv run agora-prepare` | Checks the folders and generates each agent's integration. Warns if a running room still uses the previous configuration. | See below. |
| `uv run agora-launch ID` | Regenerates the agent's configuration if it is out of date and opens its tool in its folder, with its model. | Same as `agora-prepare`, for that agent. |
| `uv run agora-start` | Refuses an already running room, prepares, opens the room and then each agent in its own window of the `agora` tmux session (`tmux attach -t agora`), prints the room's link with its token and lists what each tool asks the first time in a folder. | Same as `agora-prepare`. |
| `uv run agora-archive` | Refuses a running room, copies its database to `archive/<name>-YYYYMMDD-HHMMSS.db` next to it (`data/archive/` by default) and deletes it, so the room starts empty next time. To read an archive, open it with `sqlite3` or start a room whose `db` points to it. | The archived copy. |
| `uv run agora-mcp --participant ID [--url URL]` | Serves the MCP over stdio; also reads `AGORA_PARTICIPANT` and `AGORA_URL`. Tools run it; you do not. | None. |
| `uv run agora-watch --participant ID` | Long-polls and prints one line per message when the room wakes the participant. With `--stop-hook`, for agy's `Stop` hook, it waits for the next wake and prints `{"decision": "continue", …}` (or `{}` if the room is down). | None. |
| `uv run agora-pending --participant ID` | For hooks: prints the pending messages, or nothing (also if the room is down). `--json-event EVENT` and `--inject-steps` print the JSON that Codex/Claude and Antigravity hooks expect. | None. |
| `uv run agora-permission --participant ID` | For `PermissionRequest` hooks: reads the request from stdin, shows it in the room and waits for the user to allow or deny it there. Meanwhile Claude Code's own prompt stays open in the terminal; Codex and agy show theirs only if the room does not decide. With `--antigravity` it reads agy's `PreToolUse` input and prints agy's decisions (`auto_approve` or `deny`). It prints the decision if the room answers first, and nothing if the terminal does, the wait ends or the room is down. With `--done`, for `PostToolUse` and `PostToolUseFailure` hooks, it tells the room that the call ran, so a request allowed in the terminal stops waiting. | None. |

What `agora-prepare` generates for each tool:

| Tool | Files | Notes |
| --- | --- | --- |
| Claude Code | `<folder>/.mcp.json`, `<folder>/.claude/settings.json` and the section of `<folder>/CLAUDE.md` between `<!-- agora-prepare: start -->` and `<!-- agora-prepare: end -->` | Hooks on `SessionStart` and `UserPromptSubmit`, on `PermissionRequest` (600-second timeout) so the permission request can be answered from the room, and on `PostToolUse` and `PostToolUseFailure` to close it when it was allowed in the terminal. Inside the room's project, paths use `${CLAUDE_PROJECT_DIR}` so the files can be versioned. Claude Code asks to approve `.mcp.json` when the session opens. |
| Codex | `CODEX_HOME/config.toml`, `CODEX_HOME/agents/subagent.toml` and `<folder>/.codex/hooks.json` | Hooks on `SessionStart` and `UserPromptSubmit`, and on `PermissionRequest` (600-second timeout) so the permission request can be answered from the room. The agent's note goes in `developer_instructions`. Codex asks to trust the hooks again whenever `hooks.json` changes. |
| Antigravity CLI | `<folder>/.agents/mcp_config.json`, `<folder>/.agents/hooks.json` and `<folder>/.agents/rules/agora.md` | Hooks on `SessionStart` and `PreInvocation`, on `PreToolUse` for `run_command` (630-second timeout, longer than the room waits: on timeout agy fails the call) so its commands can be answered from the room, and on `Stop` (one-week timeout: on timeout agy stops waiting) so the room can wake it. The rule is always applied (`trigger: always_on`). `agy` asks to trust the folder the first time. |

On first launch each tool asks in its window before the session exists: Claude Code to trust the folder and to use the `agora` MCP server (in both, the default option is the one that declines), Codex to trust the hooks, Antigravity to trust the folder (and, on every launch, a first message: see [Wakers](#wakers)). Until they are answered, the room cannot wake that agent.

`agora-launch` opens each tool like this:

- Claude Code: `claude --model M --permission-mode acceptEdits`, without the `CLAUDE*` variables of the session that runs the command.
- Codex: `codex --model M "<first message>"` with `CODEX_HOME` set. The first message makes Codex save the session, so the room can find it.
- Antigravity CLI: `agy --model M --mode accept-edits --settings '{"permissions":{"allow":["mcp(agora/*)"]}}'`, plus `--effort` and one `--add-dir` per extra folder. The permission applies only to that session; the user's `settings.json` is not changed.

## Authentication

Each agent signs in with its own tool:

- **Claude Code:** the user's account, shared by all Claude agents.
- **Codex:** one `CODEX_HOME` per agent, so each agent signs in once with `CODEX_HOME=<home> codex login`; `agora-prepare` reminds you when it writes the configuration. The configuration sets `cli_auth_credentials_store = "auto"`: the credentials go to the operating system's credential store (the Keychain on macOS) when there is one, or to `CODEX_HOME/auth.json` otherwise. On macOS each `CODEX_HOME` gets its own Keychain entry, so one agent's login does not replace another's (checked with Codex 0.169).
- **Antigravity CLI:** the user's Google account, shared by all Antigravity agents (there is no equivalent of `CODEX_HOME`).

## Web API and MCP

The UI is served at `/`. It shows the thread, lets you reply to a message, pause and resume wakes, see who is listening and wake an agent manually after a failed wake. After you allow notifications (button shown until the browser has asked), a message that mentions you or a permission request raises a browser notification while the page is hidden or out of focus; clicking it brings the page to front.

Above the thread, the UI lists the agents' pending permission requests, with «Allow» and «Deny», and the last 20 answered, with where they were answered. Claude Code's prompt stays open in the agent's terminal meanwhile: whichever answers first wins. Codex and agy wait for the room and show their prompt in the terminal only when the room stops waiting (after 600 s) or is down. They live in memory only and are not messages, so they count as no hop. The buttons appear only on a page opened with the link the room prints when it starts (`/?token=…`), which leaves the token in an `HttpOnly` cookie and redirects to `/`; after a restart, open the new link.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/participants` | Participants. |
| `GET /api/messages`, `GET /api/history` | Messages; with `participant`, only those that participant may see (blind rounds). |
| `POST /api/messages` | Send a message (as the user, unless `author` is given); `blind` opens a blind round. |
| `POST /api/round/close` | Close the open blind round (as the user, unless `participant` is given). |
| `GET /api/pending` | A participant's pending messages; `wait` long-polls (up to 30 s per request) and `watching` marks the participant as listening meanwhile. |
| `POST /api/read` | Mark messages as read. |
| `GET /api/state` | Pause, hops, moderator turns, the open blind round (who answered and who is awaited), and each participant's pending count, last wake, last activity and error. |
| `POST /api/pause` | Pause or resume wakes. |
| `POST /api/wake/{participant}` | Manual wake. |
| `POST /api/permissions` | For the hook: shows a permission request (`participant`, `summary`) and waits on the same connection, up to 600 s, for `{"decision": "allow" \| "deny" \| null}`. If the connection closes first, the request counts as answered in the terminal. `key` identifies the tool call (session, tool and input). |
| `POST /api/permissions/done` | For the hooks after a tool call (`participant`, `key`): closes that call's pending request as answered in the terminal. |
| `GET /api/permissions` | Pending and recently answered requests, and `can_decide`: whether this browser holds the token. |
| `POST /api/permissions/{id}` | Allow or deny (`allow`); needs the token cookie (403 without it, 409 if already answered). |
| `GET /api/events` | Server-sent events (`message`, `wake`, `state`, `round` when a blind round closes, `permission` when a request is made or answered). |

The MCP server `agora` offers these tools, identifying the agent with `AGORA_PARTICIPANT`:

- `pending()`: the unread messages that mention the agent; it also records activity.
- `send(text, reply_to?, blind=false)`: posts a message; a reply marks the original as read; `blind` opens a blind round.
- `mark_read(ids)`: marks messages as handled without replying.
- `history(since?, limit=20)`: the latest messages the agent may see, or those after `since`.
- `close_round()`: closes the open blind round (user or moderator only).

A session that was already open must be restarted to load the MCP.

## Permissions, security and known limits

- The API only accepts the hosts `127.0.0.1`, `localhost` and the configured host, as protection against DNS rebinding.
- A request with an `Origin` header is accepted only from the room's own page (`http://<host>:<port>` for those hosts), so another web page open in the browser cannot post or wake agents. Requests without `Origin` (agents, commands) are not rejected for it; any local process can still use the API, except to answer permission requests.
- Answering a permission request needs the token the room prints when it starts, so an agent cannot approve its own request by calling the API, by mistake or through a prompt injection. The token is written to no file and served in no page. It does not stop a process of the same user that goes looking for it: in the room's tmux window, in the room's command line or environment (`agora-start` passes it with `env`), or in the browser's cookies.
- If the user answers in the terminal just as the room answers, the hook may be killed before reading the room's decision: the UI then says it was answered in the room, though the terminal decided.
- **Claude Code** is allowed the `agora` MCP tools and a fixed list of read-only commands (`ls`, `cat`, `head`, `wc`, `rg`, `git log/show/status`). `read_dirs` and `write_dirs` both become `additionalDirectories`, which does not distinguish reading from writing. Agora adds no `deny` rules, but keeps any already in `settings.json`. Other permission prompts can be answered in the agent's terminal or from the room (see [Web API and MCP](#web-api-and-mcp)); the room offers only allow and deny, not «always allow».
- **Codex** runs with `sandbox_mode = "workspace-write"` and `approval_policy = "on-request"`: to leave the sandbox (writing outside the folder, using the network) Codex asks, and the request appears in the room. Until it is answered there, or the room stops waiting, the agent waits. Interrupting Codex in its terminal closes the request. `write_dirs` become `writable_roots`; `read_dirs` add nothing to Codex's configuration.
- **Antigravity CLI** runs in `accept-edits` mode: edits are accepted and every command asks for approval, unless a rule allows it. Its commands (`run_command`) are asked in the room first, and in its terminal only if the room does not decide. The room's hook runs before agy's rules, so it also asks for commands that a rule of the user's or an «always allow» would let through. Other tools that ask (MCP servers other than the room, the browser) ask only in its terminal. `read_dirs` and `write_dirs` are added with `--add-dir`.
- A blind round hides the answers from agents that use the room as intended, not from one that looks for them: the database file (inside an agent's folder if `db` is), `GET /api/history` without `participant` and `/api/events` show everything. Keep `db` outside the agents' folders.
- Reading limits are not enforced by Agora: reinforce them with instructions, scope and human review.
- A wake is not a guarantee: the session, the executable or the connection to the room may be missing. Failed wakes stay visible in the UI and can be retried manually.

## Tests and checks

From the project root, with the tools from `mise.toml` (`mise install`):

```sh
uv run pytest        # tests
uv run lint-imports  # import layers
uv run pyright       # types (src/)
uv run ruff check    # lint (ruff format formats)
```

`uv run pre-commit run --all-files` runs every commit hook; CI runs them too, with a secret scan of the whole history and the tests. The other workflows in `.github/workflows/` run CodeQL, OpenSSF Scorecard, the pull request title check, a weekly external link check, the reminder to update `mise.toml` and the releases.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| Start from scratch | Stop the room and run `agora-archive`: it keeps the messages in `data/archive/`. |
| `room not available` in the MCP or a hook | Start the room (`uv run agora`, or `agora-start`). |
| The port is already in use | Another room is running at that address: stop it or choose another port. |
| An agent does not wake | Check the pause, `max_hops`, `max_moderator_turns`, batching and the error in the UI; see [Wakers](#wakers) for what each tool needs. |
| Claude Code does not wake | Answer the first-run dialogs in its window (trust the folder, use the `agora` MCP), or open an interactive session in the agent's exact folder; do not open a second Claude Code session there. Then wake it again from the UI. |
| Codex does not wake | Check its `CODEX_HOME` and folder, and that a main session exists (`agora-launch` creates one). |
| Antigravity does not react | After a launch, or after Esc, write «Answer the room.» in its window once `/mcp` lists the `agora` tools (a bare «Continue.» makes it explore the folder). If it looks for the room's tools with commands, that conversation lacks them: Esc and write again. |
| The room ignores a configuration change | Run `agora-prepare` and restart the room: it reads `agora.toml` when it starts. |
| Codex's window does not show a woken turn | Expected: a turn started by `codex queue` shows up when you return to the conversation view; the answer is already in the room. |
| Codex asks to trust the hooks again | Expected after `agora-prepare` changed `<folder>/.codex/hooks.json`. |
