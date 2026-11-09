import json
from pathlib import Path

import pytest

from terminal_agent.policy import CommandPolicy
from terminal_agent.suite import builtin_tasks, run_suite

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def suite_report():
    return run_suite()


def test_at_least_ten_tasks():
    assert len(builtin_tasks()) >= 10


def test_task_ids_are_unique():
    ids = [task.id for task in builtin_tasks()]
    assert len(ids) == len(set(ids))


def test_every_script_command_is_allowed(tmp_path: Path):
    policy = CommandPolicy(tmp_path)
    for task in builtin_tasks():
        for step in task.script:
            if step["action"] != "run":
                continue
            decision = policy.decide(step["command"])
            assert decision.action == "allow", f"{task.id}: {step['command']!r} -> {decision}"


def test_suite_passes_on_the_mock(suite_report):
    failed = [row for row in suite_report["tasks"] if not row["passed"]]
    assert not failed, failed
    assert suite_report["label"] == "MOCK"
    assert suite_report["provider"] == "scripted-mock"
    assert "Not a language model" in suite_report["disclaimer"]
    assert "Not a Terminal-Bench score" in suite_report["disclaimer"]
    assert suite_report["price_basis"] == "illustrative"
    assert suite_report["passed"] == suite_report["total"] == len(builtin_tasks())


def test_fix_failing_test_trace_fails_then_passes(suite_report):
    row = next(item for item in suite_report["tasks"] if item["id"] == "fix_failing_test")
    pytest_codes = [
        item["exit_code"]
        for item in row["command_trace"]
        if "pytest" in item["command"]
    ]
    assert pytest_codes == [1, 0]


def test_failing_import_trace_fails_then_passes(suite_report):
    row = next(item for item in suite_report["tasks"] if item["id"] == "failing_import")
    codes = [item["exit_code"] for item in row["command_trace"]]
    assert codes[0] == 1
    assert codes[-1] == 0


def test_committed_results_match_fresh_pass_fail(suite_report):
    committed = json.loads((ROOT / "results" / "suite.json").read_text(encoding="utf-8"))
    fresh = {row["id"]: row["passed"] for row in suite_report["tasks"]}
    old = {row["id"]: row["passed"] for row in committed["tasks"]}
    assert fresh == old
    assert committed["label"] == "MOCK"
    assert committed["passed"] == committed["total"]
    assert "scripted mock" in committed["disclaimer"].lower()


def test_readme_records_the_measured_pass_count(suite_report):
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    sentinel = f"{suite_report['passed']}/{suite_report['total']} passed"
    assert sentinel in readme
    assert "MOCK" in readme
    assert "scripted" in readme.lower()
    assert "https://github.com/harbor-framework/terminal-bench" in readme
    assert "leaderboard" in readme.lower()
