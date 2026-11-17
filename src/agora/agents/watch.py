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
from collections.abc import Sequence
from datetime import datetime

from agora.core import Message, Participant, format_line

DEFAULT_URL = "http://127.0.0.1:8765"


class WatchWaker:
    def wake(self, participant: Participant, pending: Sequence[Message]) -> None:
        return None


def fetch_pending(
    url: str, participant: str, since: int = 0, wait: float = 0, timeout: float = 5, watching: bool = False
) -> list[Message]:
    params = {"participant": participant, "since": since, "wait": wait}
    if watching:
        params["watching"] = "true"
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
    return parser.parse_args(argv)


def _utf8_output() -> None:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")


def fetch_last_wake(url: str, participant: str, timeout: float = 5) -> str | None:
    with urllib.request.urlopen(f"{url.rstrip('/')}/api/state", timeout=timeout) as r:
        return json.load(r)["participants"][participant]["last_wake"]


def main_watch(argv: Sequence[str] | None = None) -> None:
    """Prints the pending messages of `--participant` each time the room decides to wake it.

    This way it respects the hop limit, the pause and the grouping of wakes: a message that does not
    wake anyone is not printed until the next wake. On start it shows what is pending.
    """
    args = _arguments("Watches the room and writes one line per message when the room wakes you.", argv)
    _utf8_output()
    since = 0
    printed: set[int] = set()
    seen: str | None = None
    first = True
    down = False
    while True:
        try:
            new = fetch_pending(args.url, args.participant, since, 0 if first else args.wait, watching=True)
            wake = fetch_last_wake(args.url, args.participant)
            if new and wake == seen and not first:
                # The wake is recorded right after the message is saved: allow a margin.
                time.sleep(0.3)
                wake = fetch_last_wake(args.url, args.participant)
            to_print = fetch_pending(args.url, args.participant) if first or wake != seen else []
        except (OSError, ValueError, KeyError) as exc:
            if not down:
                print(f"[agora] unavailable at {args.url}: {exc}", flush=True)
                down = True
            time.sleep(args.retry)
            continue
        down = False
        first = False
        seen = wake
        since = max([since, *(m.id for m in new)])
        for message in to_print:
            if message.id not in printed:
                print(format_line(message), flush=True)
                printed.add(message.id)


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
        pending = fetch_pending(args.url, args.participant, timeout=0.5)
    except OSError, ValueError:
        pending = []
    lines = []
    if pending:
        lines.append(
            f"[agora] {len(pending)} pending message(s) for @{args.participant} (handle them with the `agora` MCP):"
        )
        lines.extend(format_line(m) for m in pending)
    if args.json_event:
        output = (
            {"hookSpecificOutput": {"hookEventName": args.json_event, "additionalContext": "\n".join(lines)}}
            if lines
            else {}
        )
        print(json.dumps(output, ensure_ascii=False))
    elif args.inject_steps:
        output = {"injectSteps": [{"systemMessage": {"systemMessage": "\n".join(lines)}}]} if lines else {}
        print(json.dumps(output, ensure_ascii=False))
    elif lines:
        print("\n".join(lines))
