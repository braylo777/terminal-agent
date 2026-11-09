import os
from pathlib import Path

from terminal_agent.sandbox import Sandbox


def test_command_runs_in_workdir(tmp_path: Path):
    result = Sandbox(tmp_path).run("pwd", timeout_s=5, max_output_chars=1000)
    assert result.exit_code == 0
    assert result.stdout.strip() == str(tmp_path.resolve())


def test_home_is_sandbox(tmp_path: Path):
    result = Sandbox(tmp_path).run("printf %s \"$HOME\"", timeout_s=5, max_output_chars=1000)
    assert result.exit_code == 0
    assert result.stdout == str(tmp_path.resolve())


def test_writes_file_inside_workdir(tmp_path: Path):
    result = Sandbox(tmp_path).run(
        "printf 'hello\\n' > hello.txt", timeout_s=5, max_output_chars=1000
    )
    assert result.exit_code == 0
    assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "hello\n"


def test_timeout_kills_process_group(tmp_path: Path):
    result = Sandbox(tmp_path).run("sleep 30", timeout_s=0.4, max_output_chars=1000)
    assert result.timed_out
    assert result.exit_code == 124
    assert result.duration_s < 5


def test_output_truncated(tmp_path: Path):
    result = Sandbox(tmp_path).run(
        "python3 -c \"print('x' * 5000)\"",
        timeout_s=5,
        max_output_chars=100,
    )
    assert result.exit_code == 0
    assert result.truncated
    assert len(result.stdout) == 100


def test_secret_env_not_copied(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "gh-test-secret")
    result = Sandbox(tmp_path).run(
        "python3 -c \"import os; print(os.environ.get('OPENAI_API_KEY')); print(os.environ.get('GITHUB_TOKEN'))\"",
        timeout_s=5,
        max_output_chars=1000,
    )
    assert result.exit_code == 0
    assert "sk-test-secret" not in result.stdout
    assert "gh-test-secret" not in result.stdout
    assert "None" in result.stdout


def test_bytecode_is_disabled(tmp_path: Path):
    result = Sandbox(tmp_path).run(
        "python3 -c \"import os, sys; print(os.environ['PYTHONDONTWRITEBYTECODE']); print(sys.dont_write_bytecode)\"",
        timeout_s=5,
        max_output_chars=200,
    )
    assert result.exit_code == 0
    assert "1" in result.stdout
    assert "True" in result.stdout


def test_path_is_kept(tmp_path: Path):
    result = Sandbox(tmp_path).run("python3 -c 'print(123)'", timeout_s=5, max_output_chars=100)
    assert result.exit_code == 0
    assert "123" in result.stdout
