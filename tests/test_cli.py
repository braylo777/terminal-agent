import json
import subprocess
import sys
from pathlib import Path

import pytest

from terminal_agent.cli import main

ROOT = Path(__file__).resolve().parents[1]


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["--help"])
    assert caught.value.code == 0
    assert "suite" in capsys.readouterr().out


def test_decide_exit_codes(tmp_path: Path, capsys):
    assert main(["decide", "--workdir", str(tmp_path), "ls"]) == 0
    assert main(["decide", "--workdir", str(tmp_path), "rm keep.txt"]) == 1
    assert main(["decide", "--workdir", str(tmp_path), "rm -rf /"]) == 2
    text = capsys.readouterr().out
    assert "deny" in text
    assert "confirm" in text
    assert "allow" in text


def test_run_mock_script_writes_hello(tmp_path: Path, capsys):
    workdir = tmp_path / "work"
    code = main(
        [
            "run",
            "Create hello.txt",
            "--provider",
            "mock",
            "--script",
            str(ROOT / "examples" / "write_hello.json"),
            "--workdir",
            str(workdir),
            "--confirm",
            "deny",
            "--json",
        ]
    )
    assert code == 0
    assert (workdir / "hello.txt").read_text(encoding="utf-8") == "hello\n"
    payload = json.loads(capsys.readouterr().out)
    assert payload["reason"] == "finish"
    assert payload["price_basis"] == "illustrative"
    assert "does not score" in payload["note"]
    log = (workdir / "actions.jsonl").read_text(encoding="utf-8")
    assert '"event": "command"' in log


def test_mock_without_script_exits():
    with pytest.raises(SystemExit) as caught:
        main(["run", "do something", "--provider", "mock", "--confirm", "deny"])
    assert "not a model" in str(caught.value).lower()


def test_openai_without_key_exits(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "run",
                "do something",
                "--provider",
                "openai",
                "--workdir",
                str(tmp_path),
            ]
        )
    assert "OPENAI_API_KEY" in str(caught.value)


def test_module_help():
    proc = subprocess.run(
        [sys.executable, "-m", "terminal_agent", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "suite" in proc.stdout
    assert "decide" in proc.stdout


def test_harbor_sketch_is_labeled_unrun():
    text = (ROOT / "examples" / "harbor_agent.py").read_text(encoding="utf-8")
    assert "has not been executed" in text
    assert "https://github.com/harbor-framework/terminal-bench" in text
    compile(text, "examples/harbor_agent.py", "exec")
