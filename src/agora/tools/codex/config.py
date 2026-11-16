"""Configuration of a Codex agent: its CODEX_HOME and the hooks of its folder."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomlkit

from agora.agents.config import ResolvedAgent
from agora.agents.environment import Environment, mcp_server, pending_command
from agora.agents.merge import at_path, put, table, update_json, update_toml
from agora.agents.notes import note
from agora.paths import same_path

SUBAGENT = "subagent"
ROOM_TOOLS = ("pending", "send", "mark_read", "history")
EVENTS = ("SessionStart", "UserPromptSubmit")


def _project_key(projects, folder: Path) -> str:
    return next((key for key in projects if same_path(key, str(folder))), str(folder))


@dataclass(frozen=True)
class CodexOptions:
    home: Path | None  # CODEX_HOME of the agent: `codex_home`, or `homes`/<id> from [tools.codex]


def resolve_options(data: Mapping[str, Any], defaults: Mapping[str, Any], base: Path) -> CodexOptions:
    if "codex_home" in data:
        return CodexOptions(Path(data["codex_home"]))
    homes = defaults.get("homes")
    return CodexOptions(Path(homes) / str(data["id"]).lower() if homes else None)


def own_note(p: ResolvedAgent, environment: Environment) -> list[str]:
    detail = ", ".join(x for x in (p.subagents.model, f"effort {p.subagents.effort}" if p.subagents.effort else None) if x)
    return [
        "- The room wakes you with `codex queue`; handle the wake with `pending()`.",
        f'- Launch your subagents with `agent_type = "{SUBAGENT}"`' + (f" ({detail})." if detail else "."),
    ]


def generate_codex(p: ResolvedAgent, environment: Environment, write: bool = True) -> list[Path]:
    home = p.tool_options.home
    if home is None:
        raise ValueError(f"{p.id}: `homes` is missing in [tools.codex]")
    config = home / "config.toml"
    subagent = home / "agents" / f"{SUBAGENT}.toml"
    hooks = p.folder / ".codex" / "hooks.json"

    def change_config(doc: tomlkit.TOMLDocument) -> None:
        put(doc, "model", p.model)
        if p.effort:
            put(doc, "model_reasoning_effort", p.effort)
        put(doc, "sandbox_mode", "workspace-write")
        # Nobody watches the console during the debate: the sandbox sets the limits and Codex does not ask.
        put(doc, "approval_policy", "never")
        put(doc, "check_for_update_on_startup", False)
        # Each agent logs in with its own CODEX_HOME; the OS credential store keeps the token out of plain files.
        put(doc, "cli_auth_credentials_store", "auto")
        put(doc, "developer_instructions", note(p, environment, own_note(p, environment)))
        if p.write_dirs:
            put(table(doc, "sandbox_workspace_write"), "writable_roots", [str(r) for r in p.write_dirs])
        projects = table(doc, "projects")
        put(table(projects, _project_key(projects, p.folder)), "trust_level", "trusted")
        room = at_path(doc, ("mcp_servers", "agora"))
        for key, value in mcp_server(p.id, environment).items():
            put(room, key, value)
        for tool in ROOM_TOOLS:
            put(at_path(room, ("tools", tool)), "approval_mode", "approve")
        agent = at_path(doc, ("agents", SUBAGENT))
        put(agent, "description", "Researches a subproject and updates its record")
        put(agent, "config_file", str(subagent))

    def change_subagent(doc: tomlkit.TOMLDocument) -> None:
        if p.subagents.model:
            put(doc, "model", p.subagents.model)
        if p.subagents.effort:
            put(doc, "model_reasoning_effort", p.subagents.effort)

    def change_hooks(data: dict) -> None:
        events = data.setdefault("hooks", {})
        for event in EVENTS:
            own = {"type": "command", "command": pending_command(p.id, environment, event), "timeout": 10}
            groups = [
                {**g, "hooks": [h for h in g.get("hooks", []) if "agora-pending" not in h.get("command", "")]}
                for g in events.get(event, [])
            ]
            events[event] = [g for g in groups if g["hooks"]] + [{"hooks": [own]}]

    changed = []
    if update_toml(config, change_config, write):
        changed.append(config)
    if update_toml(subagent, change_subagent, write):
        changed.append(subagent)
    if update_json(hooks, change_hooks, write):
        changed.append(hooks)
    return changed
