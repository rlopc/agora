"""`agora-prepare`, `agora-launch` and `agora-start`: the agents' commands, with the registered tools."""

from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from agora.agents.config import ConfigError
from agora.agents.environment import environment_for, room_running, wait_for_room
from agora.agents.prepare import agents, load, pending_changes, prepare
from agora.agents.terminal import open_window
from agora.tools import TOOLS


def _config(description: str, argv: Sequence[str] | None, *extra: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--config", type=Path, default=Path("agora.toml"))
    for name in extra:
        parser.add_argument(name)
    args = parser.parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    args.config = args.config.resolve()
    return args


def main_prepare(argv: Sequence[str] | None = None) -> None:
    args = _config("Generates the configuration of each room agent.", argv)
    path = args.config
    config = load(path)
    try:
        result = prepare(config, path.parent, TOOLS)
    except ConfigError as exc:
        raise SystemExit(f"invalid agora.toml: {exc}") from None
    if not result:
        print("Nothing to prepare: agora.toml declares no agents.")
        return
    for p in agents(config, path.parent, TOOLS):
        changed = result[p.id]
        status = f"{len(changed)} files updated" if changed else "up to date"
        print(f"- {p.id} ({p.tool}, {p.model}): {status}")
        for notice in TOOLS[p.tool].notices(p, changed):
            print(f"  {notice}")
    url = environment_for(config, path.parent).url
    if room_running(url):
        print(f"The room at {url} keeps the configuration it started with: restart it so it uses this one.")


def main_launch(argv: Sequence[str] | None = None) -> None:
    """Opens an agent with its tool, its model and its folder."""
    args = _config("Opens a room agent with its tool and its model.", argv, "id")
    path = args.config
    config = load(path)
    base = path.parent
    try:
        by_id = {p.id: p for p in agents(config, base, TOOLS)}
    except ConfigError as exc:
        raise SystemExit(f"invalid agora.toml: {exc}") from None
    p = by_id.get(args.id.lower())
    if p is None:
        raise SystemExit(f"unknown agent: {args.id}. Agents: {', '.join(sorted(by_id)) or '(none)'}")
    if not p.folder.is_dir():
        raise SystemExit(f"the folder of {p.id} does not exist: {p.folder}")
    tool = TOOLS[p.tool]
    environment = environment_for(config, base)
    if pending_changes(p, environment, TOOLS):
        changed = tool.generate(p, environment)
        print(f"Configuration of {p.id} updated ({len(changed)} files).")
    launch = tool.launch(p, os.environ)
    executable = shutil.which(launch.argv[0])
    if executable is None:
        raise SystemExit(f'Cannot find the command "{launch.argv[0]}". Install it and try again.')
    print(f"Starting {p.tool} ({p.id}) with {p.model} in {p.folder}")
    raise SystemExit(subprocess.run([executable, *launch.argv[1:]], cwd=launch.cwd, env=launch.env).returncode)


def main_start(argv: Sequence[str] | None = None) -> None:
    """Prepares the agents, opens the room and opens each agent in its own tmux window."""
    args = _config("Prepares and starts the room and all its agents.", argv)
    path = args.config
    config = load(path)
    base = path.parent
    environment = environment_for(config, base)
    if room_running(environment.url):
        # An open room keeps the configuration it started with: the agents would run with another one.
        raise SystemExit(f"A room is already running at {environment.url}: stop it before starting the debate.")
    try:
        prepare(config, base, TOOLS)
        found = agents(config, base, TOOLS)
    except ConfigError as exc:
        raise SystemExit(f"invalid agora.toml: {exc}") from None
    uv = ["uv", "run", "--project", str(environment.project)]
    open_window("agora", [*uv, "agora", "--config", str(path)], base)
    if not wait_for_room(environment.url):
        raise SystemExit(
            f"The room is not answering at {environment.url}: check its window (tmux attach -t agora) before opening the agents."
        )
    for p in found:
        open_window(p.id, [*uv, "agora-launch", "--config", str(path), p.id], p.folder)
    print(f"Room at {environment.url}; agents: {', '.join(p.id for p in found)}. Open them with: tmux attach -t agora")
