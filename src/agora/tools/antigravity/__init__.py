"""Antigravity CLI (`agy`) as an agent tool: configuration in its folder and no wake (the hooks deliver)."""

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
    # agy has no way to receive a message from outside: typing into its terminal could answer an approval dialog.
    default_waker = None
    own_keys = frozenset()
    loads_parent_instructions = True  # AGENTS.md and rules of parent folders, up to the repository root
    first_run = "trust the folder"

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
        raise NotImplementedError("Antigravity has no waker")

    def notices(self, p: ResolvedAgent, changed: Sequence[Path]) -> list[str]:
        if changed and p.waker is None:
            return ["The room cannot wake it: it sees the messages when it next calls the model."]
        return []

    def startup_warning(self, p: ResolvedAgent, waker: Waker) -> str | None:
        return None
