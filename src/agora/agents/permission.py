"""The `agora-permission` CLI: lets the user answer an agent's permission request from the room.

It runs as a `PermissionRequest` hook, or with `--antigravity` as agy's `PreToolUse` hook for its
commands. It posts the request to the room and waits on that same connection. Claude Code keeps
its own prompt open in the agent's terminal meanwhile; Codex and agy show theirs only once the
hook ends without a decision. If the user answers in the room first, it
prints the decision for the tool. If they deny it in Claude Code's terminal, or interrupt Codex,
the tool kills the hook and the room sees the connection close; if they allow it in Claude Code's
terminal, the hook keeps waiting, so with `--done`, as a `PostToolUse` and `PostToolUseFailure`
hook, it tells the room that the call ran. If the room is down or does not decide, it prints
nothing and the tool asks as usual.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any

from agora.agents.watch import DEFAULT_URL
from agora.core import PERMISSION_WAIT

LIMIT = 300
# Seconds between checks that the hook's parent is still alive while it waits.
CHECK = 1


def describe(request: Mapping[str, Any]) -> str:
    """`Tool: what it wants to do`, in one line of at most `LIMIT` characters."""
    tool = request.get("tool_name") or "?"
    data = request.get("tool_input") or {}
    detail = next((data[k] for k in ("command", "file_path", "url") if isinstance(data.get(k), str)), None)
    if detail is None:
        detail = json.dumps(data, ensure_ascii=False)
    detail = " ".join(detail.split())
    if len(detail) > LIMIT:
        detail = detail[: LIMIT - 1] + "…"
    return f"{tool}: {detail}"


def key(request: Mapping[str, Any]) -> str:
    """The same tool call in the session, as both hooks see it: they share no call id.

    A subagent's calls carry the session's id, so `agent_id` (absent for the main agent) tells them apart.
    """
    call = [request.get("session_id"), request.get("agent_id"), request.get("tool_name"), request.get("tool_input")]
    return hashlib.sha256(json.dumps(call, sort_keys=True).encode()).hexdigest()


def from_antigravity(request: Mapping[str, Any]) -> dict[str, Any]:
    """agy's `PreToolUse` input in the shape of the other tools' `PermissionRequest`."""
    call = request.get("toolCall") or {}
    data = call.get("args") or {}
    command = data.get("CommandLine")
    return {
        "session_id": request.get("conversationId"),
        "tool_name": call.get("name"),
        "tool_input": {"command": command} if isinstance(command, str) else data,
    }


def answer(decision: str | None, antigravity: bool = False) -> str:
    """What the hook prints: the tool's JSON for `allow` or `deny`, nothing otherwise."""
    if decision not in ("allow", "deny"):
        return ""
    if antigravity:
        # agy's `allow` only hands the call to its own rules, which would ask: `auto_approve` approves it.
        if decision == "allow":
            return json.dumps({"decision": "auto_approve"})
        return json.dumps({"decision": "deny", "reason": "Denied by the user in the room."})
    return json.dumps(
        {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": decision}}}
    )


def _post(url: str, data: Mapping[str, Any], timeout: float) -> Any:
    request = urllib.request.Request(
        url, data=json.dumps(data).encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout) as r:
        return json.load(r)


def _wait(url: str, data: Mapping[str, Any], timeout: float) -> Any:
    """`_post`, giving up if the hook's parent dies: agy kills only the process it started (`uv run`).

    Then the process exits and the connection closes, so the room marks the request answered elsewhere.
    """
    parent = os.getppid()
    result: list[Any] = []
    errors: list[BaseException] = []

    def call() -> None:
        try:
            result.append(_post(url, data, timeout))
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    while thread.is_alive():
        thread.join(CHECK)
        if os.getppid() != parent:
            raise OSError("the hook's parent stopped")
    if errors:
        raise errors[0]
    return result[0]


def main_permission(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Lets the user answer an agent's permission request from the room.")
    parser.add_argument("--participant", required=True)
    parser.add_argument("--url", default=os.environ.get("AGORA_URL", DEFAULT_URL))
    parser.add_argument("--done", action="store_true", help="the tool call ran: close its request, if pending")
    parser.add_argument("--antigravity", action="store_true", help="read and answer agy's PreToolUse hook")
    args = parser.parse_args(argv)
    url = args.url.rstrip("/")
    output = ""
    try:
        request = json.load(sys.stdin)
        if args.antigravity:
            request = from_antigravity(request)
        if args.done:
            _post(f"{url}/api/permissions/done", {"participant": args.participant, "key": key(request)}, 2)
        else:
            data = {"participant": args.participant, "summary": describe(request), "key": key(request)}
            # A little longer than the room waits, so the room is the one that gives up.
            output = answer(_wait(f"{url}/api/permissions", data, PERMISSION_WAIT + 10)["decision"], args.antigravity)
    except OSError, ValueError, KeyError, TypeError, AttributeError:
        # Room down, stopped or unexpected input: the tool asks as usual.
        return
    if output:
        print(output)
