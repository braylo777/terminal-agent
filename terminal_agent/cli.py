"""CLI. `run` executes one task. `suite` scores the built-in tasks. `decide` only prints a policy decision."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from terminal_agent import __version__
from terminal_agent.agent import AllowAll, DenyAll, run_agent
from terminal_agent.limits import Limits
from terminal_agent.log import ActionLog
from terminal_agent.policy import CommandPolicy
from terminal_agent.providers import OllamaProvider, OpenAICompatibleProvider, ScriptedProvider
from terminal_agent.sandbox import Sandbox
from terminal_agent.suite import check_against, render_table, run_suite


class AskConfirmer:
    def confirm(self, command: str, reason: str) -> bool:
        if not sys.stdin.isatty():
            print(
                f"not a tty; refusing to confirm ({reason}): {command}",
                file=sys.stderr,
            )
            return False
        answer = input(f"Run this command? ({reason})\n  {command}\n[y/N] ")
        return answer.strip().lower() in {"y", "yes"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="terminal-agent",
        description=(
            "Run shell commands in a sandboxed directory with a policy, timeouts, "
            "and step/cost caps. The mock provider replays a script; it is not a model."
        ),
    )
    parser.add_argument("--version", action="version", version=f"terminal-agent {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="run one task")
    run_p.add_argument("task", help="instruction for the agent")
    run_p.add_argument("--workdir", default="agent-work", help="sandbox directory (created)")
    run_p.add_argument("--provider", choices=("mock", "openai", "ollama"), default="openai")
    run_p.add_argument("--model", default="")
    run_p.add_argument("--base-url", default="")
    run_p.add_argument(
        "--api-key-env",
        default="OPENAI_API_KEY",
        help="name of the env var that holds the key; the key is not accepted as an argument",
    )
    run_p.add_argument("--script", default="", help="JSON list of actions for --provider mock")
    run_p.add_argument("--max-steps", type=int, default=16)
    run_p.add_argument("--max-commands", type=int, default=12)
    run_p.add_argument("--max-usd", type=float, default=0.50)
    run_p.add_argument("--timeout", type=float, default=30.0, help="per-command seconds")
    run_p.add_argument("--confirm", choices=("ask", "deny", "allow"), default="ask")
    run_p.add_argument("--allow-any", action="store_true", help="skip the allowlist; deny and confirm still apply")
    run_p.add_argument("--allow", action="append", default=[], help="extra executable name")
    run_p.add_argument("--log", default="", help="JSONL action log path")
    run_p.add_argument("--json", action="store_true", help="print the result as JSON")
    run_p.set_defaults(func=_cmd_run)

    suite_p = sub.add_parser("suite", help="run the built-in tasks on the scripted mock")
    suite_p.add_argument("--output", default="", help="write the JSON report here")
    suite_p.add_argument(
        "--check",
        default="",
        help="compare pass/fail with a previously written report (does not compare timings)",
    )
    suite_p.set_defaults(func=_cmd_suite)

    decide_p = sub.add_parser("decide", help="print allow, confirm, or deny for one command")
    decide_p.add_argument("command")
    decide_p.add_argument("--workdir", default=".")
    decide_p.add_argument("--allow-any", action="store_true")
    decide_p.add_argument("--allow", action="append", default=[])
    decide_p.set_defaults(func=_cmd_decide)

    args = parser.parse_args(argv)
    return args.func(args)


def _cmd_run(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    sandbox = Sandbox(workdir)
    sandbox.prepare()
    provider = _provider(args)
    allow = set(CommandPolicy(workdir).allow)
    allow.update(args.allow)
    policy = CommandPolicy(workdir, allow=frozenset(allow), allow_any=args.allow_any)
    confirmer = {"ask": AskConfirmer(), "deny": DenyAll(), "allow": AllowAll()}[args.confirm]
    log_path = Path(args.log) if args.log else workdir / "actions.jsonl"
    result = run_agent(
        args.task,
        sandbox=sandbox,
        provider=provider,
        policy=policy,
        limits=Limits(
            max_steps=args.max_steps,
            max_commands=args.max_commands,
            max_usd=args.max_usd,
            command_timeout_s=args.timeout,
        ),
        confirmer=confirmer,
        action_log=ActionLog(log_path),
        price_basis="illustrative",
    )
    payload = result.as_dict()
    payload["log"] = str(log_path)
    payload["note"] = (
        "finish means the model stopped. This command does not score the workspace. "
        "Prices are illustrative. Use `terminal-agent suite` for scored tasks."
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"status: {result.status}")
        print(f"reason: {result.reason}")
        print(f"summary: {result.summary}")
        print(f"steps: {result.steps}")
        print(f"commands: {result.commands}")
        print(f"spent_usd: {result.spent_usd:.8f} ({result.price_basis})")
        print(f"log: {log_path}")
        print(payload["note"])
    return 0 if result.status == "finished" else 1


def _cmd_suite(args: argparse.Namespace) -> int:
    report = run_suite()
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {path}")
    problems = check_against(report, Path(args.check)) if args.check else []
    print(render_table(report))
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 2
    return 0 if report["passed"] == report["total"] else 1


def _cmd_decide(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    allow = set(CommandPolicy(workdir).allow)
    allow.update(args.allow)
    decision = CommandPolicy(workdir, allow=frozenset(allow), allow_any=args.allow_any).decide(
        args.command
    )
    print(f"{decision.action}\t{decision.reason}")
    return {"allow": 0, "confirm": 1, "deny": 2}[decision.action]


def _provider(args: argparse.Namespace):
    if args.provider == "mock":
        if not args.script:
            raise SystemExit(
                "mock provider needs --script, a JSON list of actions. "
                "The mock replays that list. It is not a model."
            )
        data = json.loads(Path(args.script).read_text(encoding="utf-8"))
        if not isinstance(data, list) or not data:
            raise SystemExit("--script must be a non-empty JSON list")
        return ScriptedProvider(data)
    if args.provider == "ollama":
        return OllamaProvider(
            model=args.model or "llama3.2",
            base_url=args.base_url or "http://127.0.0.1:11434/v1",
        )
    base = args.base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    key = os.environ.get(args.api_key_env, "")
    if not key:
        raise SystemExit(
            f"{args.api_key_env} is not set. Refusing to call {base} without a key. "
            "Use --provider ollama for a local server, or --provider mock --script for an offline replay."
        )
    return OpenAICompatibleProvider(
        base_url=base,
        model=args.model or "gpt-4o-mini",
        api_key=key,
    )


if __name__ == "__main__":
    raise SystemExit(main())
