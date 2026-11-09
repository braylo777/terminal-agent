"""Parse one JSON action from a model reply."""

from __future__ import annotations

import json
import re

ACTIONS = ("plan", "run", "finish")


class ParseError(ValueError):
    pass


def parse_action(text: str) -> dict:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[A-Za-z0-9]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        raw = raw.strip()
    data = _load(raw)
    if not isinstance(data, dict):
        raise ParseError("JSON value is not an object")
    action = data.get("action")
    if action not in ACTIONS:
        raise ParseError(f"unknown action {action!r}")
    if action == "plan":
        plan = data.get("plan")
        if not isinstance(plan, str) or not plan.strip():
            raise ParseError("plan is empty")
        data["plan"] = plan.strip()
    elif action == "run":
        command = data.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ParseError("command is empty")
        data["command"] = command.strip()
        reason = data.get("reason")
        data["reason"] = reason.strip() if isinstance(reason, str) else ""
    else:
        summary = data.get("summary")
        data["summary"] = summary.strip() if isinstance(summary, str) else ""
    return data


def _load(raw: str) -> object:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end <= start:
            raise ParseError("no JSON object in the reply")
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ParseError(f"invalid JSON: {exc.msg}") from exc
