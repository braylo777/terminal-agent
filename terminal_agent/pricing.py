"""Illustrative token prices.

These are not a vendor price list. They exist so step and cost caps have a
number to enforce, and so a run can report a cost figure that is labeled as
illustrative. Pass your own table if you want the cap to track a real rate.
"""

from __future__ import annotations

# USD per 1M tokens.
ILLUSTRATIVE_PRICES: dict[str, dict[str, float]] = {
    "mock": {"in": 0.25, "out": 1.25},
    "default": {"in": 0.25, "out": 1.25},
}


def cost_usd(
    model: str,
    tokens_in: int,
    tokens_out: int,
    prices: dict[str, dict[str, float]] | None = None,
) -> float:
    table = prices if prices is not None else ILLUSTRATIVE_PRICES
    rate = table.get(model) or table.get("default")
    if rate is None:
        raise KeyError(f"no price for model {model!r} and no 'default' rate")
    return (tokens_in * rate["in"] + tokens_out * rate["out"]) / 1_000_000


def estimate_tokens(messages: list[dict]) -> int:
    """Rough stand-in when a provider does not return usage. Not a tokenizer."""
    chars = 0
    for message in messages:
        chars += len(message.get("content") or "")
    return max(1, (chars + 3) // 4)
