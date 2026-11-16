# Design debate

A moderator and two Claude Code agents (proponent and critic) debate a design question you ask in the room. Each role's instructions are in its folder's `AGENTS.md`.

Claude Code also loads the `CLAUDE.md` files of parent folders, so copy this folder outside the agora repository first and run the commands from agora's checkout:

```sh
cp -r examples/design-debate ~/debates/design-debate
uv run agora-start --config ~/debates/design-debate/agora.toml
tmux attach -t agora
```

Then write the question in the room (<http://127.0.0.1:8765>); without a mention it goes to the moderator.
