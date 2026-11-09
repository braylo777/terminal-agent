"""Hard caps for one agent run."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class Limits:
    max_steps: int = 16
    max_commands: int = 12
    max_usd: float = 0.50
    command_timeout_s: float = 30.0
    max_output_chars: int = 8_000
    max_completion_tokens: int = 1_024
    max_parse_failures: int = 2
    max_repeats: int = 3

    def as_dict(self) -> dict:
        return asdict(self)
