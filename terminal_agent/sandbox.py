"""Run a shell command with cwd, HOME, and TMPDIR inside one directory.

The timeout kills the child process group. A command that starts its own
session can outlive that kill; the policy denies setsid and nohup for that
reason. This is not a container, seccomp profile, or network namespace.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

_KEEP = ("PATH", "LANG", "LC_CTYPE", "TERM", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PYTHONUSERBASE")


@dataclass
class CommandResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    truncated: bool
    timed_out: bool


class Sandbox:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def prepare(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / ".tmp").mkdir(exist_ok=True)

    def env(self) -> dict[str, str]:
        """A small allowlist. Credential-looking parent variables are not copied."""
        source = os.environ
        env: dict[str, str] = {}
        for key in _KEEP:
            if key in source and source[key]:
                env[key] = source[key]
        env["PATH"] = source.get("PATH", "/usr/local/bin:/usr/bin:/bin")
        env["HOME"] = str(self.root)
        tmp = str(self.root / ".tmp")
        env["TMPDIR"] = tmp
        env["TMP"] = tmp
        env["TEMP"] = tmp
        env["LANG"] = source.get("LANG") or "C.UTF-8"
        env["LC_ALL"] = "C.UTF-8"
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        # Don't write or trust .pyc. A one-character edit in the same second
        # has the same size and mtime resolution, and Python will reuse the
        # bytecode from the previous failed run.
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        # pip install --user lands outside HOME once we point HOME at the sandbox.
        # Put that directory on PYTHONPATH so `python3 -m pytest` still imports.
        user_site = _user_site()
        if user_site:
            previous = env.get("PYTHONPATH")
            env["PYTHONPATH"] = user_site if not previous else user_site + os.pathsep + previous
        return env

    def run(self, command: str, *, timeout_s: float, max_output_chars: int) -> CommandResult:
        self.prepare()
        start = time.perf_counter()
        proc = subprocess.Popen(
            ["bash", "--noprofile", "--norc", "-o", "pipefail", "-c", command],
            cwd=self.root,
            env=self.env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            start_new_session=True,
        )
        timed_out = False
        try:
            stdout, stderr = proc.communicate(timeout=timeout_s)
            exit_code = proc.returncode if proc.returncode is not None else 1
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(proc.pid)
            stdout, stderr = proc.communicate()
            exit_code = 124
            stderr = (stderr or "") + f"\ncommand timed out after {timeout_s}s"
        duration = time.perf_counter() - start
        stdout, out_cut = _truncate(stdout or "", max_output_chars)
        stderr, err_cut = _truncate(stderr or "", max_output_chars)
        return CommandResult(
            command=command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_s=duration,
            truncated=out_cut or err_cut,
            timed_out=timed_out,
        )


def _user_site() -> str | None:
    try:
        import site

        path = site.getusersitepackages()
    except Exception:
        return None
    if path and os.path.isdir(path):
        return path
    return None


def _kill_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if limit < 0:
        limit = 0
    if len(text) <= limit:
        return text, False
    return text[:limit], True
