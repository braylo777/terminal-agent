from pathlib import Path

from terminal_agent.agent import AllowAll, DenyAll, run_agent
from terminal_agent.limits import Limits
from terminal_agent.log import ActionLog
from terminal_agent.policy import CommandPolicy
from terminal_agent.pricing import cost_usd
from terminal_agent.providers import Completion, ScriptedProvider
from terminal_agent.sandbox import Sandbox


def _run(tmp_path: Path, items, **kwargs):
    sandbox = Sandbox(tmp_path)
    provider = ScriptedProvider(items)
    log = ActionLog(tmp_path / "actions.jsonl")
    result = run_agent(
        kwargs.pop("task", "do the thing"),
        sandbox=sandbox,
        provider=provider,
        policy=CommandPolicy(tmp_path),
        action_log=log,
        confirmer=kwargs.pop("confirmer", DenyAll()),
        limits=kwargs.pop("limits", Limits()),
        **kwargs,
    )
    return result, provider, log


def test_self_debug_sees_the_traceback_and_fixes_the_file(tmp_path: Path):
    (tmp_path / "main.py").write_text("print(1/0)\n", encoding="utf-8")
    items = [
        {"action": "plan", "plan": "run it, then replace it"},
        {"action": "run", "command": "python3 main.py", "reason": "see the error"},
        {
            "action": "run",
            "command": "printf 'print(1 + 1)\\n' > main.py",
            "reason": "fix the crash",
        },
        {"action": "run", "command": "python3 main.py", "reason": "rerun"},
        {"action": "finish", "summary": "prints 2"},
    ]
    result, provider, log = _run(tmp_path, items)
    assert result.status == "finished"
    assert result.reason == "finish"
    assert (tmp_path / "main.py").read_text(encoding="utf-8") == "print(1 + 1)\n"
    # The call that produced the fix saw the first failure.
    fix_call = provider.calls[2]
    blob = "\n".join(message["content"] for message in fix_call)
    assert "ZeroDivisionError" in blob
    codes = [event["exit_code"] for event in log.events if event["event"] == "command"]
    assert codes == [1, 0, 0]


def test_max_steps_stops_before_another_call(tmp_path: Path):
    items = [{"action": "plan", "plan": "again"}] * 5
    result, provider, _log = _run(tmp_path, items, limits=Limits(max_steps=2))
    assert result.reason == "max_steps"
    assert result.steps == 2
    assert len(provider.calls) == 2


def test_budget_preflight_blocks_the_first_call(tmp_path: Path):
    items = [{"action": "finish", "summary": "nope"}]
    result, provider, _log = _run(
        tmp_path,
        items,
        limits=Limits(max_usd=0.0001, max_completion_tokens=1_000_000),
    )
    assert result.reason == "budget_usd"
    assert result.steps == 0
    assert provider.calls == []


def test_budget_stops_after_an_expensive_call(tmp_path: Path):
    class Costly(ScriptedProvider):
        def complete(self, messages, max_tokens=None):
            completion = super().complete(messages, max_tokens=max_tokens)
            return Completion(
                text=completion.text,
                prompt_tokens=10,
                completion_tokens=2_000_000,
                model="mock",
            )

    sandbox = Sandbox(tmp_path)
    provider = Costly([{"action": "plan", "plan": "one"}, {"action": "plan", "plan": "two"}])
    result = run_agent(
        "task",
        sandbox=sandbox,
        provider=provider,
        policy=CommandPolicy(tmp_path),
        limits=Limits(max_usd=1.0, max_completion_tokens=16),
        confirmer=DenyAll(),
    )
    assert result.reason == "budget_usd"
    assert len(provider.calls) == 1
    assert result.spent_usd == round(cost_usd("mock", 10, 2_000_000), 8)


def test_denied_command_is_not_executed(tmp_path: Path):
    marker = tmp_path / "keep.txt"
    marker.write_text("safe\n", encoding="utf-8")
    # /tmp is outside the sandbox, so this is a path denial. If the policy
    # failed open, only this marker's sibling outside the sandbox would change;
    # the assertion is that the command never ran.
    outside = tmp_path.parent / f"terminal-agent-marker-{tmp_path.name}"
    outside.write_text("outside\n", encoding="utf-8")
    try:
        items = [
            {"action": "run", "command": f"rm {outside}", "reason": "should be blocked"},
            {"action": "finish", "summary": "stopped asking"},
        ]
        result, provider, log = _run(tmp_path, items)
        assert outside.read_text(encoding="utf-8") == "outside\n"
        assert marker.read_text(encoding="utf-8") == "safe\n"
        assert result.status == "finished"
        assert any(event["event"] == "policy" and event["decision"] == "deny" for event in log.events)
        assert not any(event["event"] == "command" for event in log.events)
        blob = "\n".join(message["content"] for message in provider.calls[1])
        assert "Blocked by policy" in blob
    finally:
        outside.unlink(missing_ok=True)


def test_confirm_deny_skips_rm(tmp_path: Path):
    (tmp_path / "keep.txt").write_text("safe\n", encoding="utf-8")
    items = [
        {"action": "run", "command": "rm keep.txt", "reason": "delete"},
        {"action": "finish", "summary": "left it"},
    ]
    result, _provider, log = _run(tmp_path, items, confirmer=DenyAll())
    assert (tmp_path / "keep.txt").is_file()
    assert result.reason == "finish"
    assert any(event["event"] == "confirm" and event["approved"] is False for event in log.events)


def test_confirm_allow_runs_rm(tmp_path: Path):
    (tmp_path / "keep.txt").write_text("safe\n", encoding="utf-8")
    items = [
        {"action": "run", "command": "rm keep.txt", "reason": "delete"},
        {"action": "finish", "summary": "removed"},
    ]
    result, _provider, _log = _run(tmp_path, items, confirmer=AllowAll())
    assert result.reason == "finish"
    assert not (tmp_path / "keep.txt").exists()


def test_action_log_is_jsonl_and_records_the_loop(tmp_path: Path):
    items = [
        {"action": "plan", "plan": "write the file"},
        {"action": "run", "command": "printf 'hi\\n' > out.txt", "reason": "write"},
        {"action": "finish", "summary": "done"},
    ]
    result, _provider, log = _run(tmp_path, items)
    assert result.summary == "done"
    names = [event["event"] for event in log.events]
    assert names[0] == "run_start"
    assert "plan" in names
    assert "command" in names
    assert names[-1] == "stop"
    lines = (tmp_path / "actions.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(log.events)
    assert '"event": "command"' in "\n".join(lines)


def test_parse_retry_then_finish(tmp_path: Path):
    items = [
        "sorry, no json",
        {"action": "finish", "summary": "recovered"},
    ]
    result, provider, log = _run(tmp_path, items, limits=Limits(max_parse_failures=2))
    assert result.reason == "finish"
    assert result.summary == "recovered"
    assert len(provider.calls) == 2
    assert any(event["event"] == "parse_error" for event in log.events)


def test_parse_failures_stop_the_run(tmp_path: Path):
    items = ["nope", "still nope", {"action": "finish", "summary": "too late"}]
    result, provider, _log = _run(tmp_path, items, limits=Limits(max_parse_failures=2))
    assert result.reason == "max_parse_failures"
    assert len(provider.calls) == 2


def test_repeat_failing_command_stops(tmp_path: Path):
    items = [{"action": "run", "command": "false", "reason": "again"}] * 5
    result, provider, _log = _run(tmp_path, items, limits=Limits(max_repeats=3))
    assert result.reason == "repeat_command"
    assert result.commands == 3
    assert len(provider.calls) == 3


def test_max_commands_does_not_run_the_extra_one(tmp_path: Path):
    items = [{"action": "run", "command": "true", "reason": "ok"}] * 4
    result, _provider, log = _run(tmp_path, items, limits=Limits(max_commands=2))
    assert result.reason == "max_commands"
    assert result.commands == 2
    assert sum(1 for event in log.events if event["event"] == "command") == 2


def test_markdown_fenced_json_finishes(tmp_path: Path):
    items = ['```json\n{"action":"finish","summary":"ok"}\n```']
    result, _provider, _log = _run(tmp_path, items)
    assert result.status == "finished"
    assert result.summary == "ok"


def test_provider_error_stops_without_retry(tmp_path: Path):
    class Boom:
        model = "mock"

        def __init__(self):
            self.calls = 0

        def complete(self, messages, max_tokens=None):
            self.calls += 1
            from terminal_agent.providers import ProviderError

            raise ProviderError("connection refused")

    provider = Boom()
    result = run_agent(
        "task",
        sandbox=Sandbox(tmp_path),
        provider=provider,
        policy=CommandPolicy(tmp_path),
        limits=Limits(),
        confirmer=DenyAll(),
    )
    assert result.reason == "provider_error"
    assert provider.calls == 1
