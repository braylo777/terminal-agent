import pytest

from terminal_agent.parsing import ParseError, parse_action


def test_parse_plan():
    action = parse_action('{"action":"plan","plan":"look around"}')
    assert action == {"action": "plan", "plan": "look around"}


def test_parse_run_strips_command():
    action = parse_action('{"action": "run", "command": "  ls  ", "reason": "list"}')
    assert action["command"] == "ls"
    assert action["reason"] == "list"


def test_parse_finish_missing_summary():
    action = parse_action('{"action":"finish"}')
    assert action["summary"] == ""


def test_parse_markdown_fence():
    text = '```json\n{"action":"finish","summary":"ok"}\n```'
    assert parse_action(text)["summary"] == "ok"


def test_parse_json_with_preamble():
    text = 'Here you go: {"action":"finish","summary":"done"}'
    assert parse_action(text)["action"] == "finish"


def test_parse_rejects_unknown_action():
    with pytest.raises(ParseError):
        parse_action('{"action":"delete"}')


def test_parse_rejects_empty_command():
    with pytest.raises(ParseError):
        parse_action('{"action":"run","command":"  "}')


def test_parse_rejects_prose():
    with pytest.raises(ParseError):
        parse_action("I cannot help with that.")
