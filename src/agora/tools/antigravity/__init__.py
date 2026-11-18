"""Antigravity CLI (`agy`) as an agent tool: configuration in its folder; its Stop hook waits for the room's wakes."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from agora.agents.config import ResolvedAgent
from agora.agents.environment import Environment
from agora.agents.tool import Launch, WakeContext, model_flag
from agora.core import Waker
from agora.tools.antigravity.config import generate_antigravity, own_note

# Only for the session: the user's settings.json is left untouched.
SETTINGS = {"permissions": {"allow": ["mcp(agora/*)"]}}


class Antigravity:
    name = "antigravity"
    # agy cannot receive a message from outside (typing into its terminal could answer an approval dialog):
    # its Stop hook runs `agora-watch --stop-hook`, which makes it continue when the room wakes it.
    default_waker = "watch"
    own_keys = frozenset()
    loads_parent_instructions = True  # AGENTS.md and rules of parent folders, up to the repository root
    # Its Stop hook only runs after a turn, and a first message given at launch (`-i`) starts a conversation
    # without the MCP tools: agy adds them only on a message typed once they are loaded.
    first_run = (
        "trust the folder; on every launch, once `/mcp` lists the `agora` tools, write «Answer the room.» in its "
        "window: from then on the room wakes it"
    )

    def resolve(self, data: Mapping[str, Any], defaults: Mapping[str, Any], base: Path) -> None:
        return None

    def generate(self, p: ResolvedAgent, environment: Environment, write: bool = True) -> list[Path]:
        return generate_antigravity(p, environment, write)

    def note(self, p: ResolvedAgent, environment: Environment) -> list[str]:
        return own_note(p, environment)

    def launch(self, p: ResolvedAgent, base_env: Mapping[str, str]) -> Launch:
        argv = ["agy", *model_flag(p), "--mode", "accept-edits", "--settings", json.dumps(SETTINGS)]
        if p.effort:
            argv += ["--effort", p.effort]
        for directory in (*p.read_dirs, *p.write_dirs):
            argv += ["--add-dir", str(directory)]
        return Launch(argv, p.folder, dict(base_env))

    def create_waker(self, p: ResolvedAgent, context: WakeContext) -> Waker:
        raise NotImplementedError("Antigravity is woken by its Stop hook (waker `watch`)")

    def notices(self, p: ResolvedAgent, changed: Sequence[Path]) -> list[str]:
        if changed:
            return [
                "Between turns it waits for the room: what you type in its window is queued until Esc stops the "
                "wait, and after Esc the room does not wake it until its next turn ends."
            ]
        return []

    def startup_warning(self, p: ResolvedAgent, waker: Waker) -> str | None:
        return None
