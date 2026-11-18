@AGENTS.md

## Notes for Claude Code

- Watching with Monitor (`uv run --quiet --frozen agora-watch --participant claude`) is only needed to avoid the concierge's cost; don't leave it running permanently.

<!-- agora-prepare: start -->
- You are the participant `claude` of the room (MCP `agora`, <http://127.0.0.1:8765>).
- The concierge wakes you with SendMessage; handle the wake with `pending()`.
<!-- agora-prepare: end -->
