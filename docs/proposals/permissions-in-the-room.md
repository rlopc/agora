# Proposal: answer permission requests from the room

Status: phase 2 (deciding from the room) implemented for Claude Code, Codex and Antigravity's commands; it replaced phase 1 (notice).

## Problem

When an agent needs permission for a tool call, its tool asks in the agent's terminal. With several agents in tmux windows, the user has to find the right window; meanwhile the agent waits and the debate stalls.

## Goal

Show each permission request in the room's UI, with the agent, the tool and what it wants to run, and let the user allow or deny it there. If the user does not answer in time, the tool's own prompt takes over, as today.

Out of scope: the first-run dialogs (trusting the folder, enabling the MCP server). They are not tool permissions and no hook sees them.

## What each tool offers

| Tool | Hook | Fires | Answer |
| --- | --- | --- | --- |
| Claude Code | `PermissionRequest` | Only when it is about to ask for permission (or would auto-deny a call that cannot prompt). | `{"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}}`; `deny` takes an optional `message`. |
| Codex | `PermissionRequest` | Only when it is about to ask for approval (shell escalation, network approval); not for commands that need none. | Same shape. No decision: the normal approval prompt. `updatedInput`, `updatedPermissions` and `interrupt` are rejected. |
| Antigravity CLI | `PreToolUse` | Before every tool call, not only those that would ask. | `decision`: `allow` (hands the call to agy's rules), `auto_approve`, `ask`, `force_ask`, `deny` with a `reason`, `deny_unless_prior_grant`. |

Both hooks receive `tool_name` and `tool_input` (Codex adds a human-readable `tool_input.description`). Command hooks may run for up to 600 seconds by default in Claude Code and Codex.

Sources: [Claude Code hooks](https://code.claude.com/docs/en/hooks), [Codex hooks](https://learn.chatgpt.com/docs/hooks), [Antigravity hooks](https://antigravity.google/docs/hooks).

## Phases

1. **Notice (implemented, then replaced by phase 2).** A `PermissionRequest` hook in Claude Code runs `agora-permission --participant ID`, which posts to the room, mentioning the user, which agent is waiting and for what (`Bash: <command>`, the file, or the input), and prints nothing. The terminal prompt stays and the user answers it there. This solves finding the right window at a fraction of the cost: it reuses `POST /api/messages` and the hook generator, needs no token (it grants nothing an agent could not already do: send a message) and leaves nothing pending, since it is a message, not a request. Codex and Antigravity are unchanged. Limit: the notice is an agent message, so it counts as a hop.
2. **Deciding from the room (implemented for Claude Code).** The design below. The user wanted to watch and answer everything from the room while keeping the terminals.

## Design (phase 2)

1. **Hook.** The `PermissionRequest` hook that `agora-prepare` writes for Claude Code runs `agora-permission --participant ID`, now with a 600-second timeout. It reads the request from stdin and posts a one-line summary (`Bash: <command>`, the file, or the input) to `POST /api/permissions`.
2. **Room.** The request lives in memory (`agora.core.permissions`): agent, summary, created, status (`pending`, `allowed`, `denied`, `terminal`) and when it was answered. Pending ones are kept, plus the last 20 answered. A restart leaves nothing stale. Each change is published as a `permission` event; the UI shows them above the thread, with «Allow» and «Deny». They are not messages, so they count as no hop, and they raise a browser notification like a mention.
3. **Waiting.** Claude Code shows its prompt while the hook runs, so the room and the terminal race and the first answer wins. The room answers on the same connection the hook opened, after up to 600 seconds; with a decision the hook prints the tool's JSON.
4. **Room down or stopping.** If the room is not reachable, or stops while the hook waits, the hook prints nothing: the tool asks as today.
5. **Answered elsewhere.** When the user denies in the terminal or the hook times out, Claude Code kills the hook. Whatever the signal (even `SIGKILL`), the connection closes; the room checks for it while it waits and marks the request `terminal`. When the user allows in the terminal, Claude Code does not stop the hook: the call runs and the hook would keep waiting until its timeout. So `PostToolUse` and `PostToolUseFailure` hooks run `agora-permission --done`, and the room closes that call's pending request and answers its hook with no decision. The hooks share no call id, so the call is matched by session, tool and input (hashed). One race remains: if the user answers in the terminal just as the room answers, the room shows its own decision although the terminal's won.
6. **Codex.** `approval_policy` goes from `never` to `on-request`, so Codex asks to leave the sandbox instead of failing, and `hooks.json` gets the same `PermissionRequest` hook. Codex does not race: it waits for the hook and shows its prompt only if the hook ends without a decision (the room stops waiting, the room is down, or the hook times out). So a request is never pending when the call runs and Codex needs no hook after the call. Interrupting Codex kills the hook, and the room marks the request `terminal`. If nobody answers in the room, the agent then waits on its terminal prompt, as Claude Code does.
7. **Antigravity: only its commands.** Its `PreToolUse` hook fires on every call, but agora launches agy in `accept-edits` mode, where every command (`run_command`) asks unless a rule allows it. So a `PreToolUse` hook limited to `run_command` asks the room for what agy would ask in its terminal, without copying agy's rules: allow prints `auto_approve`, deny prints `deny` with a reason, and no decision prints nothing, which hands the call to agy's rules and its prompt. Like Codex it does not race, so it needs no hook after the call. Its timeout must outlast the room's wait (630 s), since on timeout agy fails the call. When the user interrupts agy, it kills only the process it started (`uv run`), not `agora-permission`, so the hook stops waiting as soon as its parent dies. Limits: the hook runs before agy's rules, so the room also asks for commands that a rule of the user's or an «always allow» would let through; and other tools that ask (other MCP servers, the browser) are not covered. Rules for read-only commands, as Claude Code has, can be added later if the room gets too many requests.
8. **Out of scope.** Only allow and deny: no `deny` message (not verified) and no «always allow» (`permission_suggestions`, which would need `updatedPermissions`).

## Security

The rest of the API has no authentication, so an agent could approve its own request with an HTTP call to localhost. Deciding therefore requires a secret that agents do not see:

- The room takes a token from `AGORA_TOKEN` or generates one when it starts, and prints the link `http://127.0.0.1:8765/?token=…` in its own terminal. `agora-start` generates it itself, passes it to the room's tmux window with `env` and prints the link, since the user does not see that window. Opening the link sets an `HttpOnly`, `SameSite=Strict` cookie (one per port) and redirects to `/`, so the token is not left in the address bar or in any script. Decisions without it get 403.
- The token is not written to any file, nor served in any page.

This stops an agent from approving by mistake or through a prompt injection. It does not stop a process that runs as the same user and goes looking for the token (in the room's tmux window, its command line or environment, or the browser's cookies); the specification documents that limit.

## Verified

Claude Code 2.1.311, `default` permission mode, a `Bash` call, with a probe hook that logs its input and waits (2026-11-17):

| Case | Result |
| --- | --- |
| Hook waiting | The terminal shows the prompt at the same time; it does not wait for the hook. |
| Hook exits 0 with no output | The normal prompt stays; the user answers it. |
| Hook prints `allow` while the prompt is open | The hook wins: the prompt closes and the call runs ("Allowed by PermissionRequest hook"). |
| User answers in the terminal first | Only with «No» is the hook process killed (verified again on 2026-11-18; see below). |
| Hook times out | The hook process is killed, the prompt stays open and a later decision is ignored. |

The hook's input includes `session_id`, `cwd`, `permission_mode`, `tool_name`, `tool_input` (for `Bash`, `command` and `description`) and `permission_suggestions`.

Claude Code 2.1.311, `default` permission mode, the room's own hooks, Haiku (2026-11-18):

| Case | Result |
| --- | --- |
| «Allow» in the room, `Bash` and `Write` | The call runs. |
| «Deny» in the room, without `message` | The call does not run; the agent is told a hook denied it and does not retry. |
| «No» in the terminal | The hook is killed; the room marks the request `terminal`. |
| «Yes» in the terminal | The hook gets no signal and keeps waiting (up to its timeout, or until the session ends). `PostToolUse` fires about a second later, with the same `session_id`, `tool_name` and `tool_input`; `PostToolUseFailure` if the call fails. |
| A decision from the room after «Yes» in the terminal | Ignored: the call ran once. |
| The session ends | The hook dies with it. |
| A subagent asks for permission | Its `PermissionRequest` and `PostToolUse` carry the session's `session_id`, plus `agent_id` and `agent_type`, which the main agent's lack; the room's key includes `agent_id`. |

The `PermissionRequest` input has no `tool_use_id`; the `PostToolUse` input has.

Codex 0.170.0, `on-request` approval policy, `workspace-write` sandbox, a `Bash` call that needs the network, with a probe hook (2026-11-18):

| Case | Result |
| --- | --- |
| The call needs to leave the sandbox | `PermissionRequest` fires, after `PreToolUse`. Its input has `session_id`, `turn_id`, `tool_name` (`Bash`) and `tool_input` with `command` and Codex's reason for asking (`description`); no `tool_use_id`. |
| Hook waiting | The terminal shows «Running hook», not the prompt: there is no race. |
| Hook prints `allow` | The call runs without a prompt. |
| Hook prints `deny`, with or without `message` | The call does not run («Blocked by hook»); the agent gets the message, or «PermissionRequest hook denied approval», and does not retry. No `PostToolUse`. |
| Hook exits 0 with no output, or times out | The normal prompt appears; «Yes» runs the call and `PostToolUse` fires. |
| The user interrupts Codex (Esc) while the hook waits | The hook process is killed. |

Antigravity CLI 1.5.11, `accept-edits` mode, `run_command`, with a probe hook (2026-11-18):

| Case | Result |
| --- | --- |
| Any command, also `ls -la`, with no allow rule | agy asks. `PreToolUse` input: `conversationId`, `cwd`, `toolCall.name`, `toolCall.id` and `toolCall.args` (`CommandLine`, `toolSummary`, …). No hook trust is asked. |
| Hook prints `auto_approve` | The command runs without a prompt. |
| Hook prints `deny` with a `reason` | The command does not run; the agent gets «tool call denied by pre-tool hook:» followed by the reason. |
| Hook waiting (75 s, timeout 700) | The terminal shows «Running command...», not the prompt: there is no race. The timeout is honoured beyond the 30 s default. |
| Hook exits with no output | agy applies its rules: the prompt appears, or the command runs if a rule (`command(ls)` in `--settings`) allows it. |
| Hook times out | The call fails; no prompt. |
| The user interrupts agy (Esc) while the hook waits | The hook process is killed. |
| The same, with the room's hook (`uv run … agora-permission`) | Only `uv run` is killed: `agora-permission` was left waiting, which is why it watches its parent. |

Still to verify:

- Claude Code: `deny` with a `message` (only documented).

## Cost

A new command behaviour, an in-memory resource in `core` with four endpoints, a UI panel, the token (in the room and in `agora-start`), a longer hook timeout and two more hooks in the Claude Code generator (one process of about 60 ms after every tool call), with their tests. Codex adds its approval policy and one more hook in its generator; Antigravity, one more hook and the translation of its input and output in `agora-permission`.
