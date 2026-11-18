import json

from agora.agents.permission import LIMIT, answer, describe, from_antigravity, key


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


def test_answer_only_with_a_decision():
    assert json.loads(answer("deny")) == {
        "hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "deny"}}
    }
    assert answer(None) == answer("maybe") == ""


def test_antigravity_answers_with_its_own_decisions():
    # agy's `allow` would hand the call back to its rules, which ask: the room's allow approves it.
    assert json.loads(answer("allow", antigravity=True)) == {"decision": "auto_approve"}
    assert json.loads(answer("deny", antigravity=True))["decision"] == "deny"
    assert answer(None, antigravity=True) == ""


def test_antigravity_request_in_the_shape_of_the_others():
    request = {
        "conversationId": "c1",
        "toolCall": {"name": "run_command", "id": "call_1", "args": {"CommandLine": "ls -la", "Cwd": "/w"}},
    }
    assert from_antigravity(request) == {
        "session_id": "c1",
        "tool_name": "run_command",
        "tool_input": {"command": "ls -la"},
    }
    assert describe(from_antigravity(request)) == "run_command: ls -la"
    assert from_antigravity({}) == {"session_id": None, "tool_name": None, "tool_input": {}}


def test_key_is_the_same_for_the_request_and_the_hooks_after_the_call():
    call = {"session_id": "s1", "tool_name": "Bash", "tool_input": {"command": "make", "description": "Build"}}
    after = {**call, "hook_event_name": "PostToolUse", "tool_use_id": "toolu_1", "tool_response": {}}
    assert key({**call, "hook_event_name": "PermissionRequest"}) == key(after)
    assert key(call) != key({**call, "session_id": "s2"})
    assert key(call) != key({**call, "agent_id": "a1"})
    assert key(call) != key({**call, "tool_input": {"command": "make clean"}})
