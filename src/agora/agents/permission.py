"""The `agora-permission` CLI: lets the user answer an agent's permission request from the room.

It runs as a `PermissionRequest` hook. It posts the request to the room and waits on that same
connection; meanwhile the tool's own prompt stays open in the agent's terminal. If the user
answers in the room first, it prints the decision for the tool. If they deny it in the terminal,
Claude Code kills the hook and the room sees the connection close; if they allow it, the hook
keeps waiting, so with `--done`, as a `PostToolUse` and `PostToolUseFailure` hook, it tells the
room that the call ran. If the room is down or does not decide, it prints nothing and the tool
asks as usual.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any

from agora.agents.watch import DEFAULT_URL
from agora.core import PERMISSION_WAIT

LIMIT = 300


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
    """The same tool call in the session, as both hooks see it: they share no call id."""
    call = [request.get("session_id"), request.get("tool_name"), request.get("tool_input")]
    return hashlib.sha256(json.dumps(call, sort_keys=True).encode()).hexdigest()


def answer(decision: str | None) -> str:
    """What the hook prints: the tool's JSON for `allow` or `deny`, nothing otherwise."""
    if decision not in ("allow", "deny"):
        return ""
    return json.dumps(
        {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": decision}}}
    )


def _post(url: str, data: Mapping[str, Any], timeout: float) -> Any:
    request = urllib.request.Request(
        url, data=json.dumps(data).encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout) as r:
        return json.load(r)


def main_permission(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Lets the user answer an agent's permission request from the room.")
    parser.add_argument("--participant", required=True)
    parser.add_argument("--url", default=os.environ.get("AGORA_URL", DEFAULT_URL))
    parser.add_argument("--done", action="store_true", help="the tool call ran: close its request, if pending")
    args = parser.parse_args(argv)
    url = args.url.rstrip("/")
    output = ""
    try:
        request = json.load(sys.stdin)
        if args.done:
            _post(f"{url}/api/permissions/done", {"participant": args.participant, "key": key(request)}, 2)
        else:
            data = {"participant": args.participant, "summary": describe(request), "key": key(request)}
            # A little longer than the room waits, so the room is the one that gives up.
            output = answer(_post(f"{url}/api/permissions", data, PERMISSION_WAIT + 10)["decision"])
    except OSError, ValueError, KeyError, TypeError, AttributeError:
        # Room down, stopped or unexpected input: the tool asks as usual.
        return
    if output:
        print(output)
