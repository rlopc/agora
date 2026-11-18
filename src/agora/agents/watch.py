"""Claude's waker and the `agora-watch` and `agora-pending` CLIs.

Claude cannot receive messages from outside its session: it is Claude who runs `agora-watch`
with its monitoring tool, and each printed line wakes it. That is why the server's waker
does nothing: it is enough for the message to remain pending.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from agora.core import Message, Participant, format_line

DEFAULT_URL = "http://127.0.0.1:8765"
# Seconds per long-poll of agy's Stop hook, which checks between polls that agy is still waiting.
STOP_HOOK_POLL = 5.0
STOP_HOOK_REASON = "[agora] The room woke you: its pending messages follow."


class WatchWaker:
    def wake(self, participant: Participant, pending: Sequence[Message]) -> None:
        return None


def fetch_pending(
    url: str,
    participant: str,
    since: int = 0,
    wait: float = 0,
    timeout: float = 5,
    watching: bool = False,
    activity: bool = False,
) -> list[Message]:
    params = {"participant": participant, "since": since, "wait": wait}
    if watching:
        params["watching"] = "true"
    if activity:
        params["activity"] = "true"
    query = urllib.parse.urlencode(params)
    with urllib.request.urlopen(f"{url.rstrip('/')}/api/pending?{query}", timeout=timeout + wait) as r:
        data = json.load(r)
    return [
        Message(
            d["id"],
            d["author"],
            d["text"],
            tuple(d["mentions"]),
            d["reply_to"],
            d["hops"],
            datetime.fromisoformat(d["created"]),
            d["turns"],
        )
        for d in data
    ]


def _arguments(description: str, argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--participant", required=True, help="participant id (e.g. claude)")
    parser.add_argument("--url", default=os.environ.get("AGORA_URL", DEFAULT_URL))
    parser.add_argument("--wait", type=float, default=25.0, help="long-poll seconds per request")
    parser.add_argument("--retry", type=float, default=5.0, help="seconds between retries while disconnected")
    parser.add_argument(
        "--stop-hook",
        action="store_true",
        help="for agy's Stop hook: wait for the next wake and print the JSON that makes agy continue",
    )
    return parser.parse_args(argv)


def _utf8_output() -> None:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")


def fetch_wake_state(url: str, participant: str, timeout: float = 5) -> dict[str, Any]:
    with urllib.request.urlopen(f"{url.rstrip('/')}/api/state", timeout=timeout) as r:
        return json.load(r)["participants"][participant]


def fetch_last_wake(url: str, participant: str, timeout: float = 5) -> str | None:
    return fetch_wake_state(url, participant, timeout)["last_wake"]


def _poll(
    url: str, participant: str, since: int, wait: float, seen: str | None, first: bool
) -> tuple[list[Message], int, str | None]:
    """One long-poll: what to print (if the room woke the participant), the new `since` and the last wake."""
    new = fetch_pending(url, participant, since, wait, watching=True)
    wake = fetch_last_wake(url, participant)
    if new and wake == seen and not first:
        # The wake is recorded right after the message is saved: allow a margin.
        time.sleep(0.3)
        wake = fetch_last_wake(url, participant)
    woken = fetch_pending(url, participant) if first or wake != seen else []
    return woken, max([since, *(m.id for m in new)]), wake


def main_watch(argv: Sequence[str] | None = None) -> None:
    """Prints the pending messages of `--participant` each time the room decides to wake it.

    This way it respects the hop limit, the pause and the grouping of wakes: a message that does not
    wake anyone is not printed until the next wake. On start it shows what is pending.
    """
    args = _arguments("Watches the room and writes one line per message when the room wakes you.", argv)
    _utf8_output()
    if args.stop_hook:
        _stop_hook(args.url, args.participant, args.wait)
        return
    since = 0
    printed: set[int] = set()
    seen: str | None = None
    first = True
    down = False
    while True:
        try:
            to_print, since, wake = _poll(args.url, args.participant, since, 0 if first else args.wait, seen, first)
        except (OSError, ValueError, KeyError) as exc:
            if not down:
                print(f"[agora] unavailable at {args.url}: {exc}", flush=True)
                down = True
            time.sleep(args.retry)
            continue
        down = False
        first = False
        seen = wake
        for message in to_print:
            if message.id not in printed:
                print(format_line(message), flush=True)
                printed.add(message.id)


def _woken_since_activity(state: Mapping[str, Any]) -> bool:
    """Whether the last wake came after the agent's last activity (it has not seen those messages)."""
    wake, activity = state["last_wake"], state["last_activity"]
    return wake is not None and (activity is None or datetime.fromisoformat(wake) > datetime.fromisoformat(activity))


def _stop_hook(url: str, participant: str, wait: float) -> None:
    """agy's Stop hook: waits, at no token cost, until the room wakes the agent, and makes agy continue.

    Messages it already saw and left pending do not wake it, or agy would loop. It gives up (and agy
    stops) if the room is down or agy kills the process it started (`uv run`), as Esc does.
    """
    parent = os.getppid()
    try:
        state = fetch_wake_state(url, participant)
        seen = state["last_wake"]
        woken = fetch_pending(url, participant) if _woken_since_activity(state) else []
        since, poll_wait = 0, 0.0
        while not woken:
            if os.getppid() != parent:
                return
            woken, since, seen = _poll(url, participant, since, poll_wait, seen, False)
            # Short polls, to notice soon that the parent died.
            poll_wait = min(wait, STOP_HOOK_POLL)
    except OSError, ValueError, KeyError:
        print("{}")
        return
    # Not the messages: the PreInvocation hook delivers them before the model call that follows.
    print(json.dumps({"decision": "continue", "reason": STOP_HOOK_REASON}))


def _notice(participant: str, pending: Sequence[Message]) -> str:
    # Without this, an agent that is not woken through the MCP (Antigravity) may answer in its own chat.
    header = (
        f"[agora] {len(pending)} pending message(s) for @{participant}. Answer in the room with the `send` "
        "tool of the `agora` MCP (set `reply_to`), not in this chat; use `mark_read` for what needs no reply:"
    )
    return "\n".join([header, *(format_line(m) for m in pending)])


def main_pending(argv: Sequence[str] | None = None) -> None:
    """For hooks: prints the pending messages or nothing (also if the room is down)."""
    parser = argparse.ArgumentParser(description="Shows the room's pending messages.")
    parser.add_argument("--participant", required=True)
    parser.add_argument("--url", default=os.environ.get("AGORA_URL", DEFAULT_URL))
    parser.add_argument(
        "--json-event",
        metavar="EVENT",
        help="hook JSON output (e.g. UserPromptSubmit), for clients that do not accept text, like Codex",
    )
    parser.add_argument("--inject-steps", action="store_true", help="hook JSON output for Antigravity (`injectSteps`)")
    args = parser.parse_args(argv)
    _utf8_output()
    try:
        # agy sees them before each model call: that counts as activity, so its Stop hook does not wake it for them.
        pending = fetch_pending(args.url, args.participant, timeout=0.5, activity=args.inject_steps)
    except OSError, ValueError:
        pending = []
    text = _notice(args.participant, pending) if pending else ""
    if args.json_event:
        output = {"hookSpecificOutput": {"hookEventName": args.json_event, "additionalContext": text}} if text else {}
        print(json.dumps(output, ensure_ascii=False))
    elif args.inject_steps:
        output = {"injectSteps": [{"systemMessage": {"systemMessage": text}}]} if text else {}
        print(json.dumps(output, ensure_ascii=False))
    elif text:
        print(text)
