"""Command allow / deny / confirm policy.

This is a parser, not a container. It inspects the command string before
bash runs it. Obfuscated payloads (encoded paths, Python that builds a path
at runtime) are outside what a string check can see. See the README limits.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ALLOW = frozenset(
    {
        "awk",
        "basename",
        "cat",
        "cksum",
        "column",
        "comm",
        "cp",
        "cut",
        "date",
        "diff",
        "dirname",
        "echo",
        "egrep",
        "false",
        "fgrep",
        "find",
        "fold",
        "git",
        "grep",
        "head",
        "hexdump",
        "id",
        "join",
        "ln",
        "ls",
        "make",
        "md5sum",
        "mkdir",
        "nl",
        "od",
        "paste",
        "printf",
        "pwd",
        "python",
        "python3",
        "realpath",
        "rev",
        "sed",
        "sha256sum",
        "sleep",
        "sort",
        "stat",
        "tac",
        "tail",
        "tee",
        "test",
        "touch",
        "tr",
        "true",
        "uname",
        "uniq",
        "wc",
        "which",
        "whoami",
    }
)

# argv[0] values that would hide the real program from the allowlist, or
# start a new session so the timeout's process-group kill would miss them.
WRAPPERS = frozenset(
    {
        "bash",
        "command",
        "dash",
        "env",
        "eval",
        "exec",
        "fish",
        "nice",
        "nohup",
        "setsid",
        "sh",
        "stdbuf",
        "su",
        "sudo",
        "timeout",
        "xargs",
        "zsh",
    }
)

_ALLOWED_ABS = frozenset({"/dev/null"})

_DENY = (
    (re.compile(r":\(\)\s*\{"), "fork bomb"),
    (re.compile(r"\b(?:sudo|su)\b"), "privilege escalation"),
    (
        re.compile(r"\b(?:mkfs|dd|shutdown|reboot|poweroff|halt|telinit)\b"),
        "destructive system command",
    ),
    (
        re.compile(
            r"\b(?:curl|wget|fetch)\b.*\|\s*(?:sh|bash|zsh|dash|ksh)\b",
            re.IGNORECASE | re.DOTALL,
        ),
        "pipe remote content into a shell",
    ),
    (
        re.compile(r"\b(?:setsid|nohup|disown)\b"),
        "detaches from the timeout process group",
    ),
    (
        re.compile(
            r"\b(?:mount|umount|chroot|nsenter|unshare|systemctl|service|"
            r"iptables|ufw|useradd|userdel)\b"
        ),
        "host administration",
    ),
    # Any rm that mentions /, ~, or $HOME. A trailing quote used to hide
    # `rm -rf /` inside a larger argument; the target is enough to deny.
    (
        re.compile(r"\brm\b.*(?:/|~|\$HOME)"),
        "rm targeting /, ~, or $HOME",
    ),
    (re.compile(r"\b(?:chmod|chown)\b.*\s/(?:\s|$)"), "permission change on /"),
    (re.compile(r">\s*/dev/(?:sd|nvme|hd|vd|xvd)"), "write to a block device"),
)

_CONFIRM = (
    (re.compile(r"\brm\b"), "rm deletes files"),
    (re.compile(r"\b(?:chmod|chown|chgrp)\b"), "changes permissions or ownership"),
    (re.compile(r"\bgit\s+(?:push|reset|clean)\b"), "rewrites history or talks to a remote"),
    (re.compile(r"\b(?:kill|pkill|killall)\b"), "signals another process"),
    (
        re.compile(r"\b(?:curl|wget|ssh|scp|sftp|rsync|nc|ncat|socat)\b"),
        "network access",
    ),
    (re.compile(r"\bpip3?\s+install\b"), "installs packages"),
    (re.compile(r"\b(?:mv|truncate|shred)\b"), "moves or destroys file contents"),
    (re.compile(r"\b(?:apt-get|apt|dnf|yum|pacman)\b"), "package manager"),
)

_PATH_TOKEN = re.compile(r"[A-Za-z0-9._@+\-]+(?:/[A-Za-z0-9._@+\-]+)+")


@dataclass(frozen=True)
class PolicyDecision:
    action: str  # allow, deny, confirm
    reason: str


@dataclass
class CommandPolicy:
    sandbox: Path
    allow: frozenset[str] = field(default_factory=lambda: DEFAULT_ALLOW)
    allow_any: bool = False
    max_command_chars: int = 20_000

    def decide(self, command: str) -> PolicyDecision:
        if not isinstance(command, str) or not command.strip():
            return PolicyDecision("deny", "empty command")
        if len(command) > self.max_command_chars:
            return PolicyDecision("deny", f"command longer than {self.max_command_chars} chars")
        joined = re.sub(r"\\\n", " ", command)
        if "$(" in joined or "`" in joined or "<(" in joined or ">(" in joined:
            return PolicyDecision("deny", "command substitution")
        for pattern, reason in _DENY:
            if pattern.search(joined):
                return PolicyDecision("deny", reason)
        quoted = _quoted_absolute_paths(joined)
        for path in quoted:
            if not self._path_ok(path):
                return PolicyDecision("deny", f"path outside the sandbox: {path}")

        try:
            segments = _split_segments(joined)
        except ValueError as exc:
            return PolicyDecision("deny", str(exc))
        if not segments:
            return PolicyDecision("deny", "empty command")

        confirm_reason = None
        for segment in segments:
            decision = self._decide_segment(segment)
            if decision.action == "deny":
                return decision
            if decision.action == "confirm" and confirm_reason is None:
                confirm_reason = decision.reason
        if confirm_reason is not None:
            return PolicyDecision("confirm", confirm_reason)
        return PolicyDecision("allow", "allowed")

    def _decide_segment(self, segment: str) -> PolicyDecision:
        try:
            argv = shlex.split(segment, posix=True)
        except ValueError as exc:
            return PolicyDecision("deny", f"unparseable command: {exc}")
        if not argv:
            return PolicyDecision("deny", "empty command")
        exe = Path(argv[0]).name
        if exe in WRAPPERS or exe in {".", "source"}:
            return PolicyDecision("deny", f"{exe} can hide the program being run")
        if exe == "find" and any(
            arg in {"-exec", "-execdir", "-ok", "-okdir", "-delete"} for arg in argv
        ):
            return PolicyDecision("deny", "find -exec/-delete runs other programs")
        for token in argv:
            for path in _path_candidates(token):
                if not self._path_ok(path):
                    return PolicyDecision("deny", f"path outside the sandbox: {path}")
        for pattern, reason in _CONFIRM:
            if pattern.search(segment):
                return PolicyDecision("confirm", reason)
        if self.allow_any:
            return PolicyDecision("allow", "allow-any mode")
        if exe not in self.allow:
            return PolicyDecision("deny", f"{exe} is not in the allowlist")
        return PolicyDecision("allow", "allowed")

    def _path_ok(self, path_str: str) -> bool:
        if path_str in _ALLOWED_ABS:
            return True
        candidate = Path(path_str)
        if not candidate.is_absolute():
            candidate = self.sandbox / candidate
        try:
            resolved = candidate.resolve()
            root = self.sandbox.resolve()
        except OSError:
            return False
        try:
            resolved.relative_to(root)
        except ValueError:
            return False
        return True


def _quoted_absolute_paths(command: str) -> list[str]:
    found = []
    for match in re.finditer(r"""(['"])(/[^'"]*)\1""", command):
        found.append(match.group(2))
    return found


def _path_candidates(token: str) -> list[str]:
    candidates = []
    for part in re.split(r"(?:>>|>|<)", token):
        part = part.strip()
        if not part or (part.startswith("-") and not part.startswith(("./", "../", "/"))):
            continue
        if (
            part.startswith("/")
            or part.startswith("./")
            or part.startswith("../")
            or part in {".", ".."}
            or _PATH_TOKEN.fullmatch(part)
        ):
            candidates.append(part)
    return candidates


def _split_segments(command: str) -> list[str]:
    """Split on unquoted &&, ||, ;, |, and newlines. Quotes are preserved."""
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(command):
        char = command[i]
        if quote is not None:
            buf.append(char)
            if char == quote:
                quote = None
            i += 1
            continue
        if char in {"'", '"'}:
            quote = char
            buf.append(char)
            i += 1
            continue
        if char == "\\" and i + 1 < len(command):
            buf.append(char)
            buf.append(command[i + 1])
            i += 2
            continue
        if command.startswith("&&", i) or command.startswith("||", i):
            parts.append("".join(buf))
            buf = []
            i += 2
            continue
        if char in {";", "\n", "|"}:
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(char)
        i += 1
    if quote is not None:
        raise ValueError("unbalanced quotes")
    parts.append("".join(buf))
    return [part.strip() for part in parts if part.strip()]
