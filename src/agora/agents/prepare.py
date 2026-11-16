"""Generates the configuration of each agent declared in agora.toml, with its tool."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from agora.agents.config import ConfigError, ResolvedAgent, resolve
from agora.agents.environment import Environment, environment_for
from agora.agents.tool import Tool


def pending_changes(p: ResolvedAgent, environment: Environment, tools: Mapping[str, Tool]) -> list[Path]:
    """Files that would change if prepared now: empty if the configuration is up to date."""
    return tools[p.tool].generate(p, environment, write=False)


def agents(config: Mapping[str, Any], base: Path, tools: Mapping[str, Tool]) -> list[ResolvedAgent]:
    return [p for p in resolve(config, base, tools) if isinstance(p, ResolvedAgent)]


def prepare(
    config: Mapping[str, Any], base: Path, tools: Mapping[str, Tool], write: bool = True
) -> dict[str, list[Path]]:
    environment = environment_for(config, base)
    found = agents(config, base, tools)
    check_folders(found, tools)
    return {p.id: tools[p.tool].generate(p, environment, write) for p in found}


def check_folders(found: Sequence[ResolvedAgent], tools: Mapping[str, Tool]) -> None:
    """Before writing anything: each agent in its own folder, which exists, and none inside another's if its tool forbids it.

    Claude Code loads the `CLAUDE.md` files of parent folders: an agent with Claude inside another's folder
    would also read that other agent's instructions (even if it uses Codex today, if a `CLAUDE.md` was left).
    """
    for p in found:
        if not p.folder.is_dir():
            raise ConfigError(f"the folder of {p.id} does not exist: {p.folder}")
    for i, a in enumerate(found):
        for b in found[i + 1 :]:
            ra, rb = a.folder.resolve(), b.folder.resolve()
            if ra == rb:
                raise ConfigError(f"{a.id} and {b.id} share a folder ({a.folder}): each agent needs its own")
            for inner, outer, ri, ro in ((a, b, ra, rb), (b, a, rb, ra)):
                if tools[inner.tool].loads_parent_instructions and ri.is_relative_to(ro):
                    raise ConfigError(
                        f"the folder of {inner.id} ({inner.folder}) is inside that of {outer.id} ({outer.folder}): "
                        f"with {inner.tool}, {inner.id} would also load the instructions of {outer.id}. Use sibling folders."
                    )


def load(path: Path) -> dict[str, Any]:
    with path.open("rb") as f:
        return tomllib.load(f)
