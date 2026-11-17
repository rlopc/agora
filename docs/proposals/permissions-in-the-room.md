# Proposal: answer permission requests from the room

Status: phase 1 (notice) implemented; phase 2 (deciding from the room) not implemented.

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

Sources: [Claude Code hooks](https://code.claude.com/docs/en/hooks), [Codex hooks](https://developers.openai.com/codex/hooks), [Antigravity hooks](https://antigravity.google/docs/hooks).

## Phases

1. **Notice (implemented).** A `PermissionRequest` hook in Claude Code runs `agora-permission --participant ID`, which posts to the room, mentioning the user, which agent is waiting and for what (`Bash: <command>`, the file, or the input), and prints nothing. The terminal prompt stays and the user answers it there. This solves finding the right window at a fraction of the cost: it reuses `POST /api/messages` and the hook generator, needs no token (it grants nothing an agent could not already do: send a message) and leaves nothing pending, since it is a message, not a request. Codex and Antigravity are unchanged. Limit: the notice is an agent message, so it counts as a hop.
2. **Deciding from the room (not implemented).** The design below. Worth it only if, with phase 1 in use, going to the terminal is still a bottleneck. It builds on phase 1's hook and command, and still needs the checks listed under "Still to verify".

## Design (phase 2)

1. **Hook.** `agora-prepare` adds a `PermissionRequest` hook (Claude Code and Codex) that runs a new command, `agora-permission --participant ID`. It reads the request from stdin and posts it to the room.
2. **Room.** A new `permission` record (agent, tool, input, description, created, decision, decided). The UI shows pending requests at the top, with «Allow» and «Deny»; a server-sent event announces each one.
3. **Waiting.** Claude Code shows its usual prompt in the terminal while the hook runs, so the room and the terminal race and the first answer wins. `agora-permission` long-polls the room until there is a decision, and the hook keeps the default timeout (600 seconds): a short deadline is not needed. With a decision it prints the tool's JSON.
4. **Room down.** If the room is not reachable, the command prints nothing at once: the tool asks as today.
5. **Answered elsewhere.** When the user answers in the terminal or the hook times out, the tool kills `agora-permission`, and the request would stay pending in the room. The command must mark it as answered in the terminal before it exits, or the room must expire it.
6. **Codex.** Today `approval_policy = "never"`, so Codex never asks and the hook would never fire. Using this feature means switching to an approval policy that asks (`on-request`), which changes Codex from "fail outside the sandbox" to "ask the room".
7. **Antigravity.** Not in the first version: its `PreToolUse` hook fires on every call, so the room would have to ask for all of them or copy `agy`'s permission rules.

## Security

The API has no authentication, so an agent could approve its own request with an HTTP call to localhost. Deciding must therefore require a secret that agents do not see:

- The room generates a token when it starts and prints it with the URL in its own terminal (`http://127.0.0.1:8765/?token=…`). The UI keeps it in a cookie and sends it on each decision; decisions without it are rejected.
- The token is not written to any file in the agents' folders, nor served in a page an agent could fetch without it.

This stops an agent from approving by mistake or through a prompt injection. It does not stop a process that runs as the same user and goes looking for the token (for example, by reading the room's tmux window); that limit should be documented.

## Verified

Claude Code 2.1.311, `default` permission mode, a `Bash` call, with a probe hook that logs its input and waits (2026-11-17):

| Case | Result |
| --- | --- |
| Hook waiting | The terminal shows the prompt at the same time; it does not wait for the hook. |
| Hook exits 0 with no output | The normal prompt stays; the user answers it. |
| Hook prints `allow` while the prompt is open | The hook wins: the prompt closes and the call runs ("Allowed by PermissionRequest hook"). |
| User answers in the terminal first | The hook process is killed before it finishes. |
| Hook times out | The hook process is killed, the prompt stays open and a later decision is ignored. |

The hook's input includes `session_id`, `cwd`, `permission_mode`, `tool_name`, `tool_input` (for `Bash`, `command` and `description`) and `permission_suggestions`.

Still to verify:

- Claude Code: which signal kills the hook, so `agora-permission` can mark the request before it exits.
- Claude Code: `deny` with a `message`, and tools other than `Bash` (only documented).
- Codex: that `PermissionRequest` fires with the agent's approval policy, how the sandbox and approval settings change once it is no longer `never`, whether its prompt also races with the hook, and what it does when the hook times out.

## Cost

A new command, an API resource with storage, a UI panel, the token, and changes to the Claude Code and Codex generators, with their tests. One more hook per agent, and a decision about Codex's approval policy.
