"""Participant resolution: which tool, model and folder each agent uses.

What `[[participants]]` declares wins; anything still unset is taken from `[tools.<tool>]`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agora.core import Participant, ParticipantType

if TYPE_CHECKING:
    from agora.agents.tool import Tool

# Keys this layer interprets; they do not go into `Participant.options`.
_OWN = {"id", "name", "type", "role", "waker", "tool", "folder", "model", "effort", "subagents", "read_dirs", "write_dirs"}


class ConfigError(ValueError):
    """`agora.toml` is not valid."""


@dataclass(frozen=True)
class Subagents:
    model: str | None = None
    effort: str | None = None


@dataclass(frozen=True, kw_only=True)
class ResolvedParticipant:
    id: str
    name: str
    type: ParticipantType
    role: str | None = None
    waker: str | None = None
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class ResolvedAgent(ResolvedParticipant):
    tool: str
    folder: Path
    model: str | None = None  # None: the tool's default
    effort: str | None = None
    subagents: Subagents = field(default_factory=Subagents)
    read_dirs: tuple[Path, ...] = ()
    write_dirs: tuple[Path, ...] = ()
    tool_options: Any = None  # what `Tool.resolve` returns (e.g. `CodexOptions`)


def _list(values) -> str:
    return ", ".join(sorted(values))


def resolve(config: Mapping[str, Any], base: Path, tools: Mapping[str, Tool]) -> list[ResolvedParticipant]:
    defaults = config.get("tools", {})
    return [_resolve_one(dict(data), defaults, base, tools) for data in config.get("participants", [])]


def _resolve_one(data: dict, defaults: Mapping[str, Any], base: Path, tools: Mapping[str, Tool]) -> ResolvedParticipant:
    id = str(data["id"]).lower()
    type = data["type"]
    if type == "human":
        return ResolvedParticipant(id=id, name=data["name"], type=type, role=data.get("role"))
    if type != "agent":
        raise ConfigError(f"unknown type for {id}: {type}. Valid: agent, human")
    tool = data.get("tool")
    if not isinstance(tool, str) or tool not in tools:
        problem = f"unknown tool for {id}: {tool}" if tool else f"{id} does not declare `tool`"
        raise ConfigError(f"{problem}. Valid: {_list(tools)}")
    default = defaults.get(tool, {})
    sub_default = default.get("subagents", {})
    sub = dict(data.get("subagents", {}))
    folder = Path(data.get("folder", "."))
    return ResolvedAgent(
        id=id,
        name=data["name"],
        type=type,
        role=data.get("role"),
        tool=tool,
        folder=folder if folder.is_absolute() else base / folder,
        model=data.get("model", default.get("model")),
        effort=data.get("effort", default.get("effort")),
        subagents=Subagents(sub.get("model", sub_default.get("model")), sub.get("effort", sub_default.get("effort"))),
        read_dirs=_paths(data.get("read_dirs", ()), base),
        write_dirs=_paths(data.get("write_dirs", ()), base),
        tool_options=tools[tool].resolve(data, default, base),
        waker=data.get("waker", tools[tool].default_waker),
        options={k: v for k, v in data.items() if k not in _OWN | tools[tool].own_keys},
    )


def _paths(values, base: Path) -> tuple[Path, ...]:
    """Absolute paths; relative ones are relative to the folder of agora.toml."""
    return tuple(Path(v) if Path(v).is_absolute() else base / v for v in values)


def to_participant(p: ResolvedParticipant) -> Participant:
    return Participant(id=p.id, name=p.name, type=p.type, waker=p.waker, options=dict(p.options), role=p.role)
