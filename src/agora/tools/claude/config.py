"""Configuration of a Claude Code agent in its folder: `.mcp.json`, `.claude/settings.json` and `CLAUDE.md`."""

from __future__ import annotations

from pathlib import Path

from agora.agents.config import ResolvedAgent
from agora.agents.environment import Environment, hook_command, mcp_server, pending_command
from agora.agents.merge import update_json
from agora.agents.notes import note
from agora.core import PERMISSION_WAIT

START = "<!-- agora-prepare: start -->"
END = "<!-- agora-prepare: end -->"
EVENTS = ("SessionStart", "UserPromptSubmit")
# Inside the project, paths relative to the session so the files can be versioned.
# In .mcp.json the variable does not exist when it is expanded: the default value is needed.
PROJECT_MCP = "${CLAUDE_PROJECT_DIR:-.}"
PROJECT_HOOK = '"${CLAUDE_PROJECT_DIR}"'


def update_section(path: Path, content: str, write: bool) -> bool:
    """Replaces the marked section of `path` (creates it if missing); the rest of the text is left untouched."""
    section = f"{START}\n{content}\n{END}"
    try:
        before = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        before = ""
    if START in before and END in before:
        head, rest = before.split(START, 1)
        after = head + section + rest.split(END, 1)[1]
    elif before:
        after = before.rstrip("\n") + "\n\n" + section + "\n"
    else:
        after = f"@AGENTS.md\n\n{section}\n"
    if after == before:
        return False
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(after, encoding="utf-8")
    return True


def own_note(p: ResolvedAgent, environment: Environment) -> list[str]:
    lines = ["- The concierge wakes you with SendMessage; handle the wake with `pending()`."]
    if p.subagents.model:
        lines.append(f"- Launch your subagents with `model: {p.subagents.model}`.")
    return lines


# Read-only commands the agent can run without asking (nobody is watching the console).
READ_ONLY = [
    "Bash(ls *)",
    "Bash(cat *)",
    "Bash(head *)",
    "Bash(wc *)",
    "Bash(rg *)",
    "Bash(git log *)",
    "Bash(git show *)",
    "Bash(git status *)",
]


def generate_claude(p: ResolvedAgent, environment: Environment, write: bool = True) -> list[Path]:
    mcp = p.folder / ".mcp.json"
    settings = p.folder / ".claude" / "settings.json"
    claude_md = p.folder / "CLAUDE.md"
    internal = p.folder.resolve().is_relative_to(environment.project.resolve())

    def change_mcp(data: dict) -> None:
        data.setdefault("mcpServers", {})["agora"] = mcp_server(p.id, environment, PROJECT_MCP if internal else None)

    def change_settings(data: dict) -> None:
        events = data.setdefault("hooks", {})
        project = PROJECT_HOOK if internal else None
        pending = {"type": "command", "command": pending_command(p.id, environment, project=project), "timeout": 10}
        permission = {
            "type": "command",
            "command": hook_command("agora-permission", p.id, environment, project),
            # It waits for the user to answer in the room; the terminal prompt stays open meanwhile.
            "timeout": PERMISSION_WAIT,
        }
        own = {event: ("agora-pending", pending) for event in EVENTS}
        own["PermissionRequest"] = ("agora-permission", permission)
        # Allowing in the terminal does not stop the PermissionRequest hook: these tell the room the call ran.
        done = {"type": "command", "command": f"{permission['command']} --done", "timeout": 10}
        for event in ("PostToolUse", "PostToolUseFailure"):
            own[event] = ("agora-permission", done)
        for event, (name, hook) in own.items():
            groups = [
                {**g, "hooks": [h for h in g.get("hooks", []) if name not in h.get("command", "")]}
                for g in events.get(event, [])
            ]
            events[event] = [g for g in groups if g["hooks"]] + [{"hooks": [hook]}]
        permissions = data.setdefault("permissions", {})
        allowed = permissions.setdefault("allow", [])
        allowed.extend(r for r in ["mcp__agora", *READ_ONLY] if r not in allowed)
        extra = [str(r) for r in (*p.read_dirs, *p.write_dirs)]
        if extra:
            directories = permissions.setdefault("additionalDirectories", [])
            directories.extend(r for r in extra if r not in directories)

    changed = []
    if update_json(mcp, change_mcp, write):
        changed.append(mcp)
    if update_json(settings, change_settings, write):
        changed.append(settings)
    if update_section(claude_md, note(p, environment, own_note(p, environment)), write):
        changed.append(claude_md)
    return changed
