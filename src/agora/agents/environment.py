"""Data shared by all the agents of a room: the room's URL and project."""

from __future__ import annotations

import time
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# src/agora/agents/environment.py → project root.
PROJECT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Environment:
    url: str
    project: Path


def environment_for(config: Mapping[str, Any], base: Path) -> Environment:
    room = config.get("room", {})
    host = room.get("host", "127.0.0.1")
    port = int(room.get("port", 8765))
    return Environment(
        url=f"http://{host}:{port}",
        project=Path(room.get("project", PROJECT)),
    )


def room_running(url: str) -> bool:
    """Is a room already answering at `url`? (quick check, no waiting)."""
    try:
        with urllib.request.urlopen(f"{url}/api/state", timeout=1):
            return True
    except OSError:
        return False


def wait_for_room(url: str, timeout: float = 15) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/api/state", timeout=2):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def hook_command(name: str, p_id: str, environment: Environment, project: str | None = None) -> str:
    """Hook command that runs the room's CLI `name` for the agent `p_id`."""
    project = project or environment.project.as_posix()
    return f"uv run --quiet --frozen --project {project} {name} --participant {p_id} --url {environment.url}"


def pending_command(p_id: str, environment: Environment, event: str | None = None, project: str | None = None) -> str:
    """Hook command that shows the agent its pending messages."""
    command = hook_command("agora-pending", p_id, environment, project)
    return f"{command} --json-event {event}" if event else command


def mcp_server(p_id: str, environment: Environment, project: str | None = None) -> dict[str, Any]:
    return {
        "command": "uv",
        "args": ["run", "--quiet", "--frozen", "--project", project or environment.project.as_posix(), "agora-mcp"],
        "env": {"AGORA_PARTICIPANT": p_id, "AGORA_URL": environment.url},
    }
