# Proposal: answer permission requests from the room

Status: phase 2 (deciding from the room) implemented for Claude Code; it replaced phase 1 (notice). Codex and Antigravity are not covered.

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
| Antigravity CLI | `PreToolUse` | Before every tool call, not only those that would ask. | `decision`: `allow`, `ask` or `deny`. |

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
6. **Codex (not covered).** Today `approval_policy = "never"`, so Codex never asks and the hook would never fire. Using this feature means switching to an approval policy that asks (`on-request`), which changes Codex from "fail outside the sandbox" to "ask the room", and the points under "Still to verify".
7. **Antigravity (not covered).** Its `PreToolUse` hook fires on every call, so the room would have to ask for all of them or copy `agy`'s permission rules.
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

Still to verify:

- Claude Code: `deny` with a `message` (only documented).
- Codex: that `PermissionRequest` fires with the agent's approval policy, how the sandbox and approval settings change once it is no longer `never`, whether its prompt also races with the hook, and what it does when the hook times out.

## Cost

A new command behaviour, an in-memory resource in `core` with four endpoints, a UI panel, the token (in the room and in `agora-start`), a longer hook timeout and two more hooks in the Claude Code generator (one process of about 60 ms after every tool call), with their tests. Codex would add a decision about its approval policy.
