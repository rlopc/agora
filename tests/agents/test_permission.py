from agora.agents.permission import LIMIT, describe, notice


def test_describes_command_file_or_whole_input():
    assert (
        describe({"tool_name": "Bash", "tool_input": {"command": "ls\n  -la", "description": "List"}}) == "Bash: ls -la"
    )
    assert describe({"tool_name": "Write", "tool_input": {"file_path": "a.py", "content": "x"}}) == "Write: a.py"
    assert describe({"tool_name": "mcp__x__y", "tool_input": {"n": 1}}) == 'mcp__x__y: {"n": 1}'
    assert describe({}) == "?: {}"


def test_truncates_long_input():
    text = describe({"tool_name": "Bash", "tool_input": {"command": "x" * 1000}})
    assert len(text) == len("Bash: ") + LIMIT and text.endswith("…")


def test_notice_mentions_the_humans():
    request = {"tool_name": "Bash", "tool_input": {"command": "make"}}
    assert (
        notice("claude", request, ["user"]) == "@user `claude` is waiting for permission in its terminal: `Bash: make`"
    )
    assert notice("claude", request, []).startswith("`claude` is waiting")
