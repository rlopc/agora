"""The `agora-permission` CLI: tells the room that an agent is waiting for a permission prompt.

It runs as a `PermissionRequest` hook and never decides: it prints nothing, so the tool's own
prompt stays and the user answers it in the agent's terminal. It only says which agent is waiting
and for what, so the user knows which window to go to.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any

from agora.agents.watch import DEFAULT_URL

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


def notice(participant: str, request: Mapping[str, Any], humans: Sequence[str]) -> str:
    mentions = "".join(f"@{h} " for h in humans)
    return f"{mentions}`{participant}` is waiting for permission in its terminal: `{describe(request)}`"


def _humans(url: str, timeout: float) -> list[str]:
    with urllib.request.urlopen(f"{url}/api/participants", timeout=timeout) as r:
        return [p["id"] for p in json.load(r)["participants"] if p["type"] == "human"]


def _send(url: str, author: str, text: str, timeout: float) -> None:
    body = json.dumps({"author": author, "text": text}).encode()
    request = urllib.request.Request(
        f"{url}/api/messages", data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout):
        pass


def main_permission(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Tells the room that an agent is waiting for permission.")
    parser.add_argument("--participant", required=True)
    parser.add_argument("--url", default=os.environ.get("AGORA_URL", DEFAULT_URL))
    args = parser.parse_args(argv)
    url = args.url.rstrip("/")
    try:
        request = json.load(sys.stdin)
        _send(url, args.participant, notice(args.participant, request, _humans(url, 2)), 2)
    except OSError, ValueError, KeyError, TypeError, AttributeError:
        # Room down or unexpected input: the tool asks as usual.
        pass
