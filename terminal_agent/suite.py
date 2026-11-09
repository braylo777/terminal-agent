"""Built-in tasks, independent scorers, and a scripted mock provider.

The scripts are the mock. They do not reason. Each scorer looks at the
working directory after the run and does not read the script. A pass means
the loop, the sandbox, and the checker agreed on that script.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from terminal_agent.agent import DenyAll, run_agent
from terminal_agent.limits import Limits
from terminal_agent.log import ActionLog
from terminal_agent.policy import CommandPolicy
from terminal_agent.pricing import ILLUSTRATIVE_PRICES
from terminal_agent.providers import ScriptedProvider
from terminal_agent.sandbox import Sandbox

SUITE_LIMITS = Limits(
    max_steps=16,
    max_commands=12,
    max_usd=0.50,
    command_timeout_s=30.0,
    max_output_chars=8_000,
    max_completion_tokens=512,
    max_parse_failures=2,
    max_repeats=3,
)

DISCLAIMER = (
    "Scripted mock provider. Not a language model. Not a Terminal-Bench score. "
    "Prices are illustrative (USD per 1M tokens: in 0.25, out 1.25), not a vendor price list."
)

_MATH_TEST = "from mathx import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
_PYTEST = (
    "python3 -m pytest -q -p no:cacheprovider --noconftest --rootdir=. "
    "-c pytest.ini test_mathx.py"
)


def py(code: str) -> str:
    if "\n" in code or '"' in code:
        raise ValueError("python -c snippet must be one line without double quotes")
    return f'python3 -c "{code}"'


@dataclass(frozen=True)
class Task:
    id: str
    instruction: str
    setup: object
    score: object
    script: tuple


def builtin_tasks() -> list[Task]:
    return [
        Task(
            id="write_hello",
            instruction=(
                "Create hello.txt in the working directory. "
                "Its exact contents must be the five characters hello followed by a newline."
            ),
            setup=_noop,
            score=_score_hello,
            script=(
                {"action": "plan", "plan": "Write hello.txt with printf."},
                {
                    "action": "run",
                    "command": "printf 'hello\\n' > hello.txt",
                    "reason": "create the file",
                },
                {"action": "finish", "summary": "wrote hello.txt"},
            ),
        ),
        Task(
            id="fix_failing_test",
            instruction=(
                "test_mathx.py fails. mathx.add(2, 3) must return 5. "
                "Change mathx.py only. Do not modify test_mathx.py or pytest.ini. "
                "python3 -m pytest -q -p no:cacheprovider --noconftest --rootdir=. "
                "-c pytest.ini test_mathx.py must exit 0."
            ),
            setup=_setup_failing_test,
            score=_score_failing_test,
            script=(
                {"action": "plan", "plan": "Run the test, read the failure, fix add, rerun."},
                {"action": "run", "command": _PYTEST, "reason": "see the failure"},
                {"action": "run", "command": "cat mathx.py", "reason": "read the source"},
                {
                    "action": "run",
                    "command": py(
                        "from pathlib import Path; "
                        "Path('mathx.py').write_text('def add(a, b):' + chr(10) + "
                        "'    return a + b' + chr(10))"
                    ),
                    "reason": "return the sum",
                },
                {"action": "run", "command": _PYTEST, "reason": "confirm the test passes"},
                {"action": "finish", "summary": "add returns a + b and the test passes"},
            ),
        ),
        Task(
            id="setup_project",
            instruction=(
                "Create a small Python project. requirements.txt must be exactly the line "
                "pytest and a newline. README.md must be exactly the line '# greet' and a "
                "newline. src/greet.py must define greet(name) and return 'hi ' followed by "
                "the name, so greet('ada') == 'hi ada'."
            ),
            setup=_noop,
            score=_score_project,
            script=(
                {"action": "plan", "plan": "Create requirements.txt, README.md, and src/greet.py."},
                {"action": "run", "command": "mkdir -p src", "reason": "package directory"},
                {
                    "action": "run",
                    "command": "printf 'pytest\\n' > requirements.txt",
                    "reason": "pin the dev dependency name",
                },
                {
                    "action": "run",
                    "command": "printf '# greet\\n' > README.md",
                    "reason": "write the readme heading",
                },
                {
                    "action": "run",
                    "command": py(
                        "from pathlib import Path; "
                        "Path('src/greet.py').write_text('def greet(name):' + chr(10) + "
                        "'    return ' + chr(34) + 'hi ' + chr(34) + ' + name' + chr(10))"
                    ),
                    "reason": "define greet",
                },
                {
                    "action": "run",
                    "command": py(
                        "import sys; sys.path.insert(0, 'src'); from greet import greet; "
                        "assert greet('ada') == 'hi ada'"
                    ),
                    "reason": "check greet",
                },
                {"action": "finish", "summary": "project files are in place"},
            ),
        ),
        Task(
            id="parse_logs",
            instruction=(
                "app.log has one event per line. Write errors.txt containing only the lines "
                "that start with 'ERROR ' (the level and a space), in the same order, each "
                "ending with a newline. Do not include other lines."
            ),
            setup=_setup_logs,
            score=_score_logs,
            script=(
                {"action": "plan", "plan": "Keep lines that start with ERROR and a space."},
                {
                    "action": "run",
                    "command": "grep '^ERROR ' app.log > errors.txt",
                    "reason": "filter error lines",
                },
                {"action": "finish", "summary": "wrote errors.txt"},
            ),
        ),
        Task(
            id="csv_sum",
            instruction=(
                "sales.csv has a header item,amount. Sum the amount column and write the "
                "total as a number to total.txt, followed by a newline."
            ),
            setup=_setup_csv,
            score=_score_csv,
            script=(
                {"action": "plan", "plan": "Sum the amount column with the csv module."},
                {
                    "action": "run",
                    "command": py(
                        "import csv; from pathlib import Path; "
                        "rows=list(csv.DictReader(open('sales.csv'))); "
                        "total=sum(float(r['amount']) for r in rows); "
                        "Path('total.txt').write_text(str(total)+'\\n')"
                    ),
                    "reason": "sum amounts",
                },
                {"action": "finish", "summary": "wrote the total"},
            ),
        ),
        Task(
            id="fix_json",
            instruction=(
                "data.json is not valid JSON. Write fixed.json with the same keys and values "
                "(name is ada, n is the number 1) as valid JSON. Leave data.json in place and still invalid."
            ),
            setup=_setup_json,
            score=_score_json,
            script=(
                {"action": "plan", "plan": "Write a valid JSON object to fixed.json."},
                {
                    "action": "run",
                    "command": py(
                        "import json; from pathlib import Path; "
                        "Path('fixed.json').write_text(json.dumps({'name': 'ada', 'n': 1})+'\\n')"
                    ),
                    "reason": "emit valid json",
                },
                {"action": "finish", "summary": "wrote fixed.json"},
            ),
        ),
        Task(
            id="extract_ips",
            instruction=(
                "Read access.log and write ips.txt. Each line is one unique dotted-quad "
                "(four decimal groups separated by dots). Sort the lines lexicographically. "
                "End every line with a newline, including the last."
            ),
            setup=_setup_ips,
            score=_score_ips,
            script=(
                {"action": "plan", "plan": "Collect unique dotted quads and sort them."},
                {
                    "action": "run",
                    "command": py(
                        "import re; from pathlib import Path; "
                        "text=Path('access.log').read_text(); "
                        "ips=sorted(set(re.findall(r'\\b(?:\\d{1,3}\\.){3}\\d{1,3}\\b', text))); "
                        "Path('ips.txt').write_text(''.join(i+'\\n' for i in ips))"
                    ),
                    "reason": "extract and sort",
                },
                {"action": "finish", "summary": "wrote ips.txt"},
            ),
        ),
        Task(
            id="failing_import",
            instruction=(
                "python3 main.py fails because util is missing. Add util.py so main.py can "
                "stay unchanged and `python3 main.py` prints OK! and exits 0. "
                "shout(text) must return the uppercased text plus an exclamation mark."
            ),
            setup=_setup_import,
            score=_score_import,
            script=(
                {"action": "plan", "plan": "Run main.py, then add the missing module and rerun."},
                {"action": "run", "command": "python3 main.py", "reason": "see the import error"},
                {
                    "action": "run",
                    "command": py(
                        "from pathlib import Path; "
                        "Path('util.py').write_text('def shout(s):' + chr(10) + "
                        "'    return s.upper() + ' + chr(34) + '!' + chr(34) + chr(10))"
                    ),
                    "reason": "define shout",
                },
                {"action": "run", "command": "python3 main.py", "reason": "confirm it prints OK!"},
                {"action": "finish", "summary": "main.py imports util and prints OK!"},
            ),
        ),
        Task(
            id="count_py_files",
            instruction=(
                "Count regular files whose name ends with .py anywhere under the working "
                "directory. Write only that integer and a newline to count.txt."
            ),
            setup=_setup_py_files,
            score=_score_py_files,
            script=(
                {"action": "plan", "plan": "Walk the tree and count .py files."},
                {
                    "action": "run",
                    "command": py(
                        "from pathlib import Path; "
                        "n=sum(1 for p in Path('.').rglob('*.py') if p.is_file()); "
                        "Path('count.txt').write_text(str(n)+'\\n')"
                    ),
                    "reason": "count python files",
                },
                {"action": "finish", "summary": "wrote the count"},
            ),
        ),
        Task(
            id="copy_baks",
            instruction=(
                "For every file whose name ends with .bak, anywhere under the working "
                "directory, copy its bytes to the same path with the .bak suffix removed. "
                "Leave the .bak files in place. Do not change other files."
            ),
            setup=_setup_baks,
            score=_score_baks,
            script=(
                {"action": "plan", "plan": "Copy each .bak file to the path without that suffix."},
                {
                    "action": "run",
                    "command": py(
                        "from pathlib import Path; "
                        "[p.with_suffix('').write_bytes(p.read_bytes()) "
                        "for p in Path('.').rglob('*.bak') if p.is_file()]"
                    ),
                    "reason": "copy backups",
                },
                {"action": "finish", "summary": "copied the bak files"},
            ),
        ),
        Task(
            id="makefile_hello",
            instruction=(
                "Write a Makefile whose hello target prints hello-from-make. "
                "`make hello` must exit 0."
            ),
            setup=_noop,
            score=_score_make,
            script=(
                {"action": "plan", "plan": "Write a Makefile and run make hello."},
                {
                    "action": "run",
                    "command": py(
                        "from pathlib import Path; "
                        "Path('Makefile').write_text('hello:' + chr(10) + chr(9) + "
                        "'printf hello-from-make' + chr(10))"
                    ),
                    "reason": "write the makefile",
                },
                {"action": "run", "command": "make hello", "reason": "run the target"},
                {"action": "finish", "summary": "make hello printed hello-from-make"},
            ),
        ),
        Task(
            id="git_commit",
            instruction=(
                "Initialize a git repository here, create notes.txt whose exact contents are "
                "'ship it' and a newline, and commit that file with the message 'add notes'. "
                "Set the commit identity with the command itself. Do not push."
            ),
            setup=_noop,
            score=_score_git,
            script=(
                {"action": "plan", "plan": "Init a repo, write notes.txt, commit it locally."},
                {"action": "run", "command": "git init", "reason": "create the repository"},
                {
                    "action": "run",
                    "command": "printf 'ship it\\n' > notes.txt",
                    "reason": "write the note",
                },
                {"action": "run", "command": "git add notes.txt", "reason": "stage the note"},
                {
                    "action": "run",
                    "command": (
                        "git -c user.email=agent@example.com -c user.name=terminal-agent "
                        "-c commit.gpgsign=false commit -m 'add notes'"
                    ),
                    "reason": "commit without signing",
                },
                {"action": "finish", "summary": "committed notes.txt"},
            ),
        ),
        Task(
            id="word_freq",
            instruction=(
                "words.txt has one word per line. Write top.txt with the single most frequent "
                "word, lowercased, and a newline. If several words tie, choose the "
                "lexicographically smallest."
            ),
            setup=_setup_words,
            score=_score_words,
            script=(
                {"action": "plan", "plan": "Count words and break ties alphabetically."},
                {
                    "action": "run",
                    "command": py(
                        "from pathlib import Path; from collections import Counter; "
                        "words=Path('words.txt').read_text().split(); "
                        "counts=Counter(w.lower() for w in words); "
                        "best=sorted(counts, key=lambda w: (-counts[w], w))[0]; "
                        "Path('top.txt').write_text(best+'\\n')"
                    ),
                    "reason": "pick the top word",
                },
                {"action": "finish", "summary": "wrote top.txt"},
            ),
        ),
    ]


def run_suite() -> dict:
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    tasks_out = []
    for task in builtin_tasks():
        tasks_out.append(_run_task(task))
    passed = sum(1 for row in tasks_out if row["passed"])
    total = len(tasks_out)
    return {
        "label": "MOCK",
        "provider": "scripted-mock",
        "price_basis": "illustrative",
        "prices_usd_per_million": dict(ILLUSTRATIVE_PRICES["mock"]),
        "disclaimer": DISCLAIMER,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "started_at": started_at,
        "elapsed_s": round(time.perf_counter() - started, 4),
        "passed": passed,
        "failed": total - passed,
        "total": total,
        "limits": SUITE_LIMITS.as_dict(),
        "tasks": tasks_out,
    }


def check_against(report: dict, path: Path) -> list[str]:
    """Return human-readable mismatches. Empty list means pass/fail agrees."""
    committed = json.loads(path.read_text(encoding="utf-8"))
    problems = []
    if committed.get("label") != "MOCK":
        problems.append("committed results are not labeled MOCK")
    if committed.get("provider") != "scripted-mock":
        problems.append("committed provider is not scripted-mock")
    fresh = {row["id"]: row["passed"] for row in report["tasks"]}
    old = {row["id"]: row["passed"] for row in committed.get("tasks", [])}
    if fresh != old:
        problems.append(f"pass/fail changed: fresh={fresh} committed={old}")
    if committed.get("passed") != committed.get("total"):
        problems.append("committed file records a failure")
    if report["passed"] != report["total"]:
        problems.append("fresh run has a failure")
    return problems


def render_table(report: dict) -> str:
    lines = [
        f"label: {report['label']}  provider: {report['provider']}  "
        f"{report['passed']}/{report['total']} passed  "
        f"{report['elapsed_s']}s  python {report['python']}",
        report["disclaimer"],
        "",
        f"{'task':<20} {'result':<6} {'steps':>5} {'cmds':>5} {'exit codes':<16} {'usd':>12} {'sec':>8}",
    ]
    for row in report["tasks"]:
        codes = " ".join(str(item["exit_code"]) for item in row["command_trace"]) or "-"
        result = "pass" if row["passed"] else "FAIL"
        lines.append(
            f"{row['id']:<20} {result:<6} {row['steps']:>5} {row['commands']:>5} "
            f"{codes:<16} {row['spent_usd']:>12.8f} {row['elapsed_s']:>8.4f}"
        )
        if not row["passed"]:
            lines.append(f"  {row['detail']}")
    return "\n".join(lines)


def _run_task(task: Task) -> dict:
    started = time.perf_counter()
    with TemporaryDirectory(prefix=f"terminal-agent-{task.id}-") as tmp:
        root = Path(tmp)
        task.setup(root)
        sandbox = Sandbox(root)
        provider = ScriptedProvider(list(task.script))
        result = run_agent(
            task.instruction,
            sandbox=sandbox,
            provider=provider,
            policy=CommandPolicy(root),
            limits=SUITE_LIMITS,
            confirmer=DenyAll(),
            action_log=ActionLog(),
            price_basis="illustrative",
        )
        try:
            ok, detail = task.score(root)
        except Exception as exc:  # a scorer bug should fail the task, not the process
            ok, detail = False, f"scorer raised {type(exc).__name__}: {exc}"
    trace = [
        {"command": event["command"], "exit_code": event["exit_code"]}
        for event in result.events
        if event.get("event") == "command"
    ]
    return {
        "id": task.id,
        "passed": bool(ok) and result.reason == "finish",
        "detail": detail if ok and result.reason == "finish" else f"{detail}; stop={result.reason}",
        "steps": result.steps,
        "commands": result.commands,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "spent_usd": result.spent_usd,
        "elapsed_s": round(time.perf_counter() - started, 4),
        "stop_reason": result.reason,
        "status": result.status,
        "command_trace": trace,
    }


def _noop(_root: Path) -> None:
    return None


def _score_hello(root: Path):
    path = root / "hello.txt"
    if not path.is_file():
        return False, "hello.txt is missing"
    text = path.read_text(encoding="utf-8")
    if text != "hello\n":
        return False, f"hello.txt is {text!r}"
    return True, "hello.txt is exactly hello and a newline"


def _setup_failing_test(root: Path) -> None:
    (root / "mathx.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (root / "test_mathx.py").write_text(_MATH_TEST, encoding="utf-8")
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")


def _score_failing_test(root: Path):
    current = (root / "test_mathx.py").read_text(encoding="utf-8")
    if current != _MATH_TEST:
        return False, "test_mathx.py was modified"
    if (root / "pytest.ini").read_text(encoding="utf-8") != "[pytest]\n":
        return False, "pytest.ini was modified"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--noconftest",
            "--rootdir=.",
            "-c",
            "pytest.ini",
            "test_mathx.py",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        env=_score_env(root),
    )
    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr)[-400:]
        return False, f"pytest still failing: {tail}"
    return True, "pytest passes and the test file is unchanged"


def _score_project(root: Path):
    req = root / "requirements.txt"
    readme = root / "README.md"
    greet = root / "src" / "greet.py"
    if not req.is_file() or req.read_text(encoding="utf-8") != "pytest\n":
        return False, f"requirements.txt is {req.read_text(encoding='utf-8')!r}" if req.is_file() else "requirements.txt missing"
    if not readme.is_file() or readme.read_text(encoding="utf-8") != "# greet\n":
        return False, "README.md is not exactly '# greet' plus a newline"
    if not greet.is_file():
        return False, "src/greet.py is missing"
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, 'src'); from greet import greet; "
            "assert greet('ada') == 'hi ada'",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        env=_score_env(root),
    )
    if proc.returncode != 0:
        return False, f"greet() failed: {(proc.stdout + proc.stderr)[-300:]}"
    return True, "requirements, readme, and greet('ada') match"


def _setup_logs(root: Path) -> None:
    (root / "app.log").write_text(
        "INFO start\nERROR disk full\nERRORS no\nWARN low memory\nERROR timeout on db\nINFO done\n",
        encoding="utf-8",
    )


def _score_logs(root: Path):
    expected = "ERROR disk full\nERROR timeout on db\n"
    path = root / "errors.txt"
    if not path.is_file():
        return False, "errors.txt is missing"
    got = path.read_text(encoding="utf-8")
    if got != expected:
        return False, f"errors.txt is {got!r}"
    return True, "errors.txt has the two ERROR lines"


def _setup_csv(root: Path) -> None:
    (root / "sales.csv").write_text("item,amount\na,10\nb,2.5\nc,7.5\n", encoding="utf-8")


def _score_csv(root: Path):
    path = root / "total.txt"
    if not path.is_file():
        return False, "total.txt is missing"
    try:
        value = float(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return False, f"total.txt is not a number: {path.read_text(encoding='utf-8')!r}"
    if abs(value - 20.0) > 1e-9:
        return False, f"total is {value}, expected 20"
    return True, "amount column sums to 20"


def _setup_json(root: Path) -> None:
    (root / "data.json").write_text('{ "name": "ada", "n": 1, }\n', encoding="utf-8")


def _score_json(root: Path):
    raw = (root / "data.json").read_text(encoding="utf-8")
    try:
        json.loads(raw)
        return False, "data.json was rewritten into valid JSON"
    except json.JSONDecodeError:
        pass
    path = root / "fixed.json"
    if not path.is_file():
        return False, "fixed.json is missing"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"fixed.json is still invalid: {exc.msg}"
    if data != {"name": "ada", "n": 1}:
        return False, f"fixed.json is {data!r}"
    return True, "fixed.json is valid and data.json was left broken"


def _setup_ips(root: Path) -> None:
    (root / "access.log").write_text(
        "GET / 10.0.0.2\n"
        "err 10.0.0.2 again\n"
        "from 192.168.1.10 connected\n"
        "version 1.0\n"
        "no ip here\n"
        "and 8.8.8.8\n",
        encoding="utf-8",
    )


def _score_ips(root: Path):
    expected = "10.0.0.2\n192.168.1.10\n8.8.8.8\n"
    path = root / "ips.txt"
    if not path.is_file():
        return False, "ips.txt is missing"
    got = path.read_text(encoding="utf-8")
    if got != expected:
        return False, f"ips.txt is {got!r}"
    return True, "three unique addresses, sorted lexicographically"


def _setup_import(root: Path) -> None:
    (root / "main.py").write_text(
        "from util import shout\nprint(shout('ok'))\n",
        encoding="utf-8",
    )


def _score_import(root: Path):
    main = (root / "main.py").read_text(encoding="utf-8")
    if main != "from util import shout\nprint(shout('ok'))\n":
        return False, "main.py was modified"
    proc = subprocess.run(
        [sys.executable, "main.py"],
        cwd=root,
        capture_output=True,
        text=True,
        env=_score_env(root),
    )
    if proc.returncode != 0:
        return False, f"main.py exit {proc.returncode}: {proc.stderr[-200:]}"
    if proc.stdout != "OK!\n":
        return False, f"stdout is {proc.stdout!r}"
    return True, "main.py printed OK! and was not edited"


def _setup_py_files(root: Path) -> None:
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "b.txt").write_text("nope\n", encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "c.py").write_text("y = 2\n", encoding="utf-8")
    (root / "sub" / "d.md").write_text("# d\n", encoding="utf-8")
    (root / "sub" / "nested").mkdir()
    (root / "sub" / "nested" / "e.py").write_text("z = 3\n", encoding="utf-8")


def _score_py_files(root: Path):
    expected = sum(1 for path in root.rglob("*.py") if path.is_file())
    path = root / "count.txt"
    if not path.is_file():
        return False, "count.txt is missing"
    try:
        got = int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return False, f"count.txt is {path.read_text(encoding='utf-8')!r}"
    if got != expected:
        return False, f"count is {got}, tree has {expected} .py files"
    return True, f"count.txt is {expected}"


def _setup_baks(root: Path) -> None:
    (root / "a.txt.bak").write_text("A\n", encoding="utf-8")
    (root / "b.txt.bak").write_text("B\n", encoding="utf-8")
    (root / "keep.txt").write_text("K\n", encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "c.txt.bak").write_text("C\n", encoding="utf-8")


def _score_baks(root: Path):
    checks = {
        "a.txt": "A\n",
        "b.txt": "B\n",
        "a.txt.bak": "A\n",
        "b.txt.bak": "B\n",
        "keep.txt": "K\n",
        "sub/c.txt": "C\n",
        "sub/c.txt.bak": "C\n",
    }
    for rel, expected in checks.items():
        path = root / rel
        if not path.is_file():
            return False, f"{rel} is missing"
        got = path.read_text(encoding="utf-8")
        if got != expected:
            return False, f"{rel} is {got!r}"
    return True, "bak files were copied and originals kept"


def _score_make(root: Path):
    proc = subprocess.run(
        ["make", "hello"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return False, f"make exit {proc.returncode}: {proc.stderr[-200:]}"
    if "hello-from-make" not in proc.stdout:
        return False, f"stdout is {proc.stdout!r}"
    return True, "make hello printed hello-from-make"


def _score_git(root: Path):
    if not (root / ".git").is_dir():
        return False, "no .git directory"
    notes = root / "notes.txt"
    if not notes.is_file() or notes.read_text(encoding="utf-8") != "ship it\n":
        return False, "notes.txt is not exactly 'ship it' plus a newline"
    env = _score_env(root)
    log = subprocess.run(
        ["git", "log", "-1", "--format=%s"],
        cwd=root,
        capture_output=True,
        text=True,
        env=env,
    )
    if log.returncode != 0:
        return False, f"git log failed: {log.stderr[-200:]}"
    if log.stdout.strip() != "add notes":
        return False, f"commit subject is {log.stdout!r}"
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        capture_output=True,
        text=True,
        env=env,
    )
    if status.stdout.strip():
        return False, f"working tree not clean: {status.stdout!r}"
    return True, "notes.txt committed with message 'add notes'"


def _setup_words(root: Path) -> None:
    (root / "words.txt").write_text("red\nblue\nred\nblue\ngreen\n", encoding="utf-8")


def _score_words(root: Path):
    path = root / "top.txt"
    if not path.is_file():
        return False, "top.txt is missing"
    got = path.read_text(encoding="utf-8")
    if got != "blue\n":
        return False, f"top.txt is {got!r}, expected 'blue\\n' (tie, lex smaller)"
    return True, "top word is blue"


def _score_env(root: Path) -> dict[str, str]:
    env = Sandbox(root).env()
    # Scorers run outside the agent, but git and python should not pick up
    # the caller's home config. Reuse the sandbox env.
    return env

