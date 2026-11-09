"""Run the built-in suite on the scripted mock and write results/suite.json.

The provider is a script, not a model. The file is the measurement record.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from terminal_agent.suite import render_table, run_suite  # noqa: E402


def main() -> int:
    report = run_suite()
    out = ROOT / "results" / "suite.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(render_table(report))
    print(f"wrote {out}")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
